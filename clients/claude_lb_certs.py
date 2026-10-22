from __future__ import annotations  # noqa: I001


import shutil
import subprocess

from clients.claude_lb_config import CA_CERT
from clients.claude_lb_config import CA_KEY
from clients.claude_lb_config import CERT_MARKER
from clients.claude_lb_config import LEAF_CERT
from clients.claude_lb_config import LEAF_KEY
from clients.claude_lb_config import TLS_DIR


def run_openssl(args: list[str]) -> None:
    proc = subprocess.run(
        ["openssl", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError("openssl " + " ".join(args) + " failed:\n" + proc.stdout.decode("utf-8", "replace"))


def ensure_mitm_certs() -> None:

    if CERT_MARKER.exists() and CA_CERT.exists() and CA_KEY.exists() and LEAF_CERT.exists() and LEAF_KEY.exists():
        return
    if shutil.which("openssl") is None:
        raise RuntimeError("openssl not found on PATH (needed to mint the local MITM cert)")

    TLS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        TLS_DIR.chmod(0o700)
    except OSError:
        pass

    ca_conf = TLS_DIR / "ca.cnf"
    leaf_req_conf = TLS_DIR / "leaf-req.cnf"
    leaf_ext_conf = TLS_DIR / "leaf-ext.cnf"
    leaf_csr = TLS_DIR / "leaf.csr"
    ca_conf.write_text(
        "[req]\n"
        "distinguished_name = dn\n"
        "x509_extensions = v3_ca\n"
        "prompt = no\n"
        "[dn]\n"
        "CN = agent-lb local MITM CA (claude cc)\n"
        "[v3_ca]\n"
        "basicConstraints = critical,CA:TRUE,pathlen:0\n"
        "keyUsage = critical,keyCertSign,cRLSign\n"
        "subjectKeyIdentifier = hash\n"
    )
    leaf_req_conf.write_text("[req]\ndistinguished_name = dn\nprompt = no\n[dn]\nCN = api.anthropic.com\n")
    leaf_ext_conf.write_text(
        "[v3_leaf]\n"
        "basicConstraints = critical,CA:FALSE\n"
        "keyUsage = critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage = serverAuth\n"
        "subjectAltName = DNS:api.anthropic.com\n"
    )

    run_openssl(
        [
            "req",
            "-x509",
            "-new",
            "-nodes",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-days",
            "3650",
            "-keyout",
            str(CA_KEY),
            "-out",
            str(CA_CERT),
            "-config",
            str(ca_conf),
        ]
    )
    run_openssl(
        [
            "req",
            "-new",
            "-nodes",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(LEAF_KEY),
            "-out",
            str(leaf_csr),
            "-config",
            str(leaf_req_conf),
        ]
    )
    run_openssl(
        [
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(CA_CERT),
            "-CAkey",
            str(CA_KEY),
            "-CAcreateserial",
            "-out",
            str(LEAF_CERT),
            "-days",
            "3650",
            "-sha256",
            "-extfile",
            str(leaf_ext_conf),
            "-extensions",
            "v3_leaf",
        ]
    )
    for key_file in (CA_KEY, LEAF_KEY):
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
    for scratch in (leaf_csr, ca_conf, leaf_req_conf, leaf_ext_conf):
        try:
            scratch.unlink()
        except OSError:
            pass
    CERT_MARKER.write_text("1\n")
