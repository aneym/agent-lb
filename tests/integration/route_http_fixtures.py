"""Serve pool/model fixtures at the network edge while forwarding real lease reads."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen


@contextmanager
def pick_http_env(env: dict[str, str]):
    fixture_dir = Path(env["ROUTE_FIXTURE_DIR"])
    upstream = env["AGENT_LB_URL"]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/pools/reservations"):
                try:
                    with urlopen(upstream + self.path, timeout=3) as response:
                        status, body = response.status, response.read()
                except HTTPError as error:
                    status, body = error.code, error.read()
            else:
                slug = self.path.split("?", 1)[0].strip("/").replace("/", "_") or "root"
                try:
                    body = (fixture_dir / f"{slug}.json").read_bytes()
                    status = 200
                except FileNotFoundError:
                    status, body = 404, b"{}"
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as httpd:
        thread = Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        network_env = {key: value for key, value in env.items() if key != "ROUTE_FIXTURE_DIR"}
        network_env["AGENT_LB_URL"] = f"http://127.0.0.1:{httpd.server_port}"
        try:
            yield network_env
        finally:
            httpd.shutdown()
            thread.join()
