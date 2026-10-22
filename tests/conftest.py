from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="agent-lb-tests-"))
TEST_DB_PATH = TEST_DB_DIR / "agent-lb.db"

os.environ["AGENT_LB_DATABASE_URL"] = os.environ.get(
    "AGENT_LB_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{TEST_DB_PATH}"
)
os.environ["AGENT_LB_UPSTREAM_BASE_URL"] = "https://example.invalid/backend-api"
os.environ["AGENT_LB_USAGE_REFRESH_ENABLED"] = "false"
os.environ["AGENT_LB_MODEL_REGISTRY_ENABLED"] = "false"
os.environ["AGENT_LB_STICKY_SESSION_CLEANUP_ENABLED"] = "false"
os.environ["AGENT_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED"] = "false"
os.environ["AGENT_LB_QUOTA_PLANNER_SCHEDULER_ENABLED"] = "false"
os.environ["AGENT_LB_ACCOUNTS_CACHE_WARMER_ENABLED"] = "false"

_PIN = subprocess.run(
    ["git", "-C", str(Path(__file__).resolve().parents[1]), "merge-base", "HEAD", "origin/main"],
    capture_output=True,
    text=True,
    timeout=60,
).stdout.strip()
if _PIN:
    os.environ["AGENT_LB_POLICY_TEST_PIN"] = _PIN

os.environ["AGENT_LB_SEAT_STATE"] = str(Path(tempfile.gettempdir()) / "agent-lb-tests-no-seat-state.json")


VENDOR_CLI_STUBS = Path(tempfile.mkdtemp(prefix="agent-lb-vendor-cli-stubs-"))
VENDOR_CLI_STUB_EXIT = 97
for _name in ("cursor-agent", "agent", "devin", "muse"):
    _stub = VENDOR_CLI_STUBS / _name
    _stub.write_text(
        f"#!/bin/sh\necho 'agent-lb tests never run the real {_name} CLI; set its command in the test' >&2\n"
        f"exit {VENDOR_CLI_STUB_EXIT}\n"
    )
    _stub.chmod(0o755)
os.environ["PATH"] = f"{VENDOR_CLI_STUBS}{os.pathsep}{os.environ.get('PATH', '')}"

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import create_app  # noqa: E402


def _drop_test_migration_tables(sync_conn) -> None:
    sync_conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    sync_conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))


def _recreate_test_schema(sync_conn) -> None:
    _drop_test_migration_tables(sync_conn)
    Base.metadata.drop_all(sync_conn)
    Base.metadata.create_all(sync_conn)


def _reset_test_database(sync_conn) -> None:
    _recreate_test_schema(sync_conn)


@pytest_asyncio.fixture
async def _reset_db_state():
    from app.db.session import close_db

    await close_db()
    async with engine.begin() as conn:
        await conn.run_sync(_reset_test_database)
    return True


@pytest_asyncio.fixture
async def app_instance(_reset_db_state, monkeypatch):
    del _reset_db_state
    import app.main as main_module

    async def _noop_init_db() -> None:
        return None

    monkeypatch.setattr(main_module, "init_db", _noop_init_db)
    app = create_app()
    return app


@pytest_asyncio.fixture(scope="session", autouse=True)
async def dispose_engine():
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def db_setup(_reset_db_state):
    del _reset_db_state
    return True


@pytest_asyncio.fixture
async def async_client(app_instance):
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.fixture(autouse=True)
def _clear_account_read_caches():

    from app.modules.accounts.service_caches import clear_account_caches

    clear_account_caches()
    yield


@pytest.fixture(autouse=True)
def temp_key_file(monkeypatch):
    key_path = TEST_DB_DIR / f"encryption-{uuid4().hex}.key"
    monkeypatch.setenv("AGENT_LB_ENCRYPTION_KEY_FILE", str(key_path))
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    return key_path


@pytest.fixture(autouse=True)
def _reset_model_registry():
    from app.core.openai.model_registry import get_model_registry

    registry = get_model_registry()
    registry._snapshot = None
    yield
    registry._snapshot = None


@pytest.fixture(autouse=True)
def _reset_codex_version_cache():
    from app.core.clients.codex_version import get_codex_version_cache

    cache = get_codex_version_cache()
    cache._cached_version = None
    cache._cached_at = 0.0
    yield
    cache._cached_version = None
    cache._cached_at = 0.0


def _reset_global_state() -> None:

    try:
        from app.core.auth.api_key_cache import get_api_key_cache

        get_api_key_cache().clear()
    except Exception:
        pass
    try:
        from app.modules.team.service import reset_team_usage_cache

        reset_team_usage_cache()
    except Exception:
        pass
    try:
        from app.core.middleware.firewall_cache import get_firewall_ip_cache as get_firewall_cache

        get_firewall_cache().invalidate_all()
    except Exception:
        pass
    try:
        from app.modules.proxy.account_cache import get_account_selection_cache

        get_account_selection_cache().invalidate()
    except Exception:
        pass
    try:
        from app.core.resilience.degradation import set_normal

        set_normal()
    except Exception:
        pass
    try:
        from app.core.shutdown import set_bridge_drain_active

        set_bridge_drain_active(False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_hot_path_caches():

    _reset_global_state()
    yield
    _reset_global_state()


def _commit_as_origin_main(repo: Path) -> Path:
    import subprocess

    def git(*words: str) -> None:
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *words,
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "policy")
    origin = repo.with_name(repo.name + "-origin.git")
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(repo), str(origin)], check=True, capture_output=True, timeout=60
    )
    git("remote", "add", "origin", str(origin))
    git("fetch", "-q", "origin", "main")
    return repo


@pytest.fixture
def policy_checkout():
    return _commit_as_origin_main
