from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def curl(base, path, key=None):
    command = ["curl", "--silent", "--show-error", "--fail-with-body", "--write-out", "\n%{http_code}"]
    config = ""
    if key:
        command += ["--config", "-"]
        config = f'header = "Authorization: Bearer {key}"\n'
    result = subprocess.run(command + [base + path], input=config, text=True, capture_output=True)
    body, status = result.stdout.rsplit("\n", 1)
    if result.returncode not in (0, 22):
        raise RuntimeError(result.stderr)
    return int(status), json.loads(body)


async def seed():
    from app.db.session import SessionLocal, close_db, init_db
    from app.modules.api_keys.repository import ApiKeysRepository
    from app.modules.api_keys.service import ApiKeysService
    from app.modules.api_keys.service_data import ApiKeyCreateData
    from app.modules.settings.repository import SettingsRepository

    await init_db()
    async with SessionLocal() as session:
        await SettingsRepository(session).get_or_create()
        await SettingsRepository(session).update(api_key_auth_enabled=True)
        service = ApiKeysService(ApiKeysRepository(session))
        full = await service.create_key(ApiKeyCreateData(name="lookup-proof", allowed_models=None))
        limited = await service.create_key(ApiKeyCreateData(name="lookup-limited", allowed_models=["deepseek-flash"]))
    await close_db()
    return full.key, limited.key


def verify(base, full, limited):
    status, catalog = curl(base, "/v1/models", full)
    assert status == 200, (status, catalog)
    for model_id in ("deepseek-flash", "gpt-5.4"):
        status, model = curl(base, f"/v1/models/{model_id}", full)
        assert status == 200, (status, model)
        listed = next(item for item in catalog["data"] if item["id"] == model_id)
        assert abs(model["created"] - listed["created"]) < 10
        assert {k: v for k, v in model.items() if k != "created"} == {k: v for k, v in listed.items() if k != "created"}
        print(f"GET /v1/models/{model_id}: 200; matches listed object ({len(json.dumps(model))} bytes)")
    status, missing = curl(base, "/v1/models/lookup-nonexistent", full)
    assert status == 404 and missing["error"]["code"] == "model_not_found", (status, missing)
    assert missing["error"]["type"] == "invalid_request_error"
    print(f"GET /v1/models/lookup-nonexistent: {status} {json.dumps(missing)}")
    for label, key in (("missing", None), ("invalid", "sk-clb-invalid-proof")):
        listed = curl(base, "/v1/models", key)
        retrieved = curl(base, "/v1/models/gpt-5.4", key)
        assert listed == retrieved and listed[0] == 401, (listed, retrieved)
        print(f"{label} key: list and lookup identical: {listed[0]} {json.dumps(listed[1])}")
    status, catalog = curl(base, "/v1/models", limited)
    assert status == 200 and [m["id"] for m in catalog["data"]] == ["deepseek-flash"]
    assert curl(base, "/v1/models/deepseek-flash", limited)[0] == 200
    assert curl(base, "/v1/models/gpt-5.4", limited)[0] == 404
    print("restricted key: allowed model 200; hidden model 404; list visibility matches")


def main():
    (ROOT / ".local").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="model-lookup-", dir=ROOT / ".local") as directory:
        data = Path(directory)
        for name in tuple(os.environ):
            if name.startswith("AGENT_LB_"):
                del os.environ[name]
        values = {
            "data_dir": str(data),
            "database_url": f"sqlite+aiosqlite:///{data / 'store.db'}",
            "encryption_key_file": str(data / "encryption.key"),
            "identity_enabled": "false",
            "proxy_unauthenticated_client_cidrs": "127.0.0.1/32",
        }
        for name in (
            "account_pulse",
            "usage_refresh",
            "model_registry",
            "auth_guardian",
            "reset_credit_auto_redeem",
            "reset_credit_expiry_redeem",
            "accounts_cache_warmer",
            "quota_planner_scheduler",
            "metrics",
            "otel",
            "leader_election",
        ):
            values[f"{name}_enabled"] = "false"
        os.environ.update({f"AGENT_LB_{k.upper()}": v for k, v in values.items()})
        full, limited = asyncio.run(seed())
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with (data / "server.log").open("w+") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import os; from pathlib import Path; "
                    "from app.core.forensics import register_stack_dump_signal; "
                    "register_stack_dump_signal(Path(os.environ['AGENT_LB_DATA_DIR']) / 'forensics'); "
                    "import uvicorn; uvicorn.run('app.main:app', host='127.0.0.1', port=int(os.environ['LOOKUP_PORT']))",
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                env={**os.environ, "LOOKUP_PORT": str(port)},
            )
            try:
                base = f"http://127.0.0.1:{port}"
                print(f"isolated app: {base}; database: {data / 'store.db'}")
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("local app exited before readiness")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError("local app did not become ready")
                verify(base, full, limited)
                print("model lookup HTTP proof passed")
            except BaseException:
                log.seek(0)
                print(log.read()[-6000:])
                raise
            finally:
                server.terminate()
                server.wait(timeout=20)


if __name__ == "__main__":
    main()
