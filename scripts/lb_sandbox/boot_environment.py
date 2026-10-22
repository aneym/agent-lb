from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from .credentials import (
    check_data_custody_fd,
    open_data_dir,
)
from .paths import (
    Refused,
    Unhealthy,
)


def read_token_fd(fd: int) -> str:

    try:
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            raise Refused(f"--token-fd {fd} is not a pipe")
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
    finally:
        os.close(fd)
    token = b"".join(chunks).decode().strip()
    if not token:
        raise Unhealthy("the token pipe was empty")
    return token


RELATIVE_DATA_ENV = {
    "AGENT_LB_DATA_DIR": ".",
    "AGENT_LB_DATABASE_URL": "sqlite+aiosqlite:///store.db",
    "AGENT_LB_ENCRYPTION_KEY_FILE": "encryption.key",
    "AGENT_LB_FEDERATION_PUSH_PATH": "federation-push.json",
    "AGENT_LB_CONVERSATION_ARCHIVE_DIR": "conversation-archive",
}


def enter_data_dir(root: Path, data_id: str) -> None:

    match = re.fullmatch(r"([0-9]+):([0-9]+)", data_id or "")
    if not match:
        raise Refused("--data-id must be <device>:<inode>")
    try:
        data_fd = open_data_dir(root, expect=(int(match.group(1)), int(match.group(2))))
    except FileNotFoundError:
        raise Refused("sandbox data dir is missing") from None
    except OSError as exc:
        raise Refused(f"sandbox data dir is a link or not the root's ({exc.__class__.__name__})") from None
    try:
        check_data_custody_fd(data_fd)
        os.fchdir(data_fd)
    finally:
        os.close(data_fd)
    os.environ.update(RELATIVE_DATA_ENV)
