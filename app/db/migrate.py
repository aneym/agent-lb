from __future__ import annotations

import argparse
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from anyio import to_thread
from sqlalchemy import create_engine, inspect, text
from sqlalchemy import exc as sa_exc
from sqlalchemy.engine import Connection

from app.core.config.settings import get_settings
from app.db.alembic.revision_ids import REVISION_ID_PATTERN
from app.db.migration_url import to_sync_database_url
from app.db.schema_drift import inspect_schema_drift

logger = logging.getLogger(__name__)

_ALEMBIC_VERSION_TABLE = "alembic_version"
_ALEMBIC_VERSION_COLUMN = "version_num"
_EXISTING_COLUMN_REVISION_SKIPS: tuple[tuple[str, str, dict[str, frozenset[str]]], ...] = (
    (
        "20260609_150000_add_account_subscription_ledger",
        "20260609_120000_merge_provider_and_useragent_heads",
        {
            "accounts": frozenset(
                {
                    "subscription_status",
                    "subscription_next_charge_at",
                    "subscription_current_period_end_at",
                    "subscription_amount",
                    "subscription_currency",
                    "subscription_last_verified_at",
                    "subscription_notes",
                }
            )
        },
    ),
    (
        "20260610_180000_add_seed_target_offset_minutes",
        "20260609_150000_add_account_subscription_ledger",
        {"quota_planner_settings": frozenset({"seed_target_offset_minutes"})},
    ),
)


@dataclass(frozen=True)
class MigrationRunResult:
    current_revision: str | None


@dataclass(frozen=True)
class MigrationState:
    current_revision: str | None
    head_revision: str
    has_alembic_version_table: bool
    needs_upgrade: bool


class MigrationBootstrapError(RuntimeError):
    pass


def _script_location() -> str:
    return str((Path(__file__).resolve().parent / "alembic").resolve())


def _build_alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", _script_location())
    config.set_main_option("sqlalchemy.url", to_sync_database_url(database_url).replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


def _required_sqlalchemy_url(config: Config) -> str:
    sync_database_url = config.get_main_option("sqlalchemy.url")
    if not sync_database_url:
        raise MigrationBootstrapError("sqlalchemy.url is missing in alembic config")
    return sync_database_url


@contextmanager
def _sync_connection(sync_database_url: str) -> Iterator[Connection]:
    engine = create_engine(sync_database_url, future=True)
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


@contextmanager
def _sync_transaction(sync_database_url: str) -> Iterator[Connection]:
    engine = create_engine(sync_database_url, future=True)
    try:
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


def _read_table_names(connection: Connection) -> set[str]:
    inspector = inspect(connection)
    return set(inspector.get_table_names())


def _read_current_revisions_from_connection(connection: Connection) -> tuple[str, ...]:
    try:
        rows = connection.execute(text(f"SELECT {_ALEMBIC_VERSION_COLUMN} FROM {_ALEMBIC_VERSION_TABLE}")).fetchall()
    except (sa_exc.ProgrammingError, sa_exc.OperationalError) as exc:
        message = str(exc).lower()
        if _ALEMBIC_VERSION_TABLE in message and (
            "does not exist" in message or "undefinedtable" in message or "no such table" in message
        ):
            return ()
        raise
    revisions = {str(row[0]) for row in rows if row and row[0]}
    return tuple(sorted(revisions))


def _read_current_revision_from_connection(connection: Connection) -> str | None:
    revisions = list(_read_current_revisions_from_connection(connection))
    if not revisions:
        return None
    if len(revisions) == 1:
        return revisions[0]
    return ",".join(sorted(revisions))


def _read_current_revision(sync_database_url: str) -> str | None:
    with _sync_connection(sync_database_url) as connection:
        tables = _read_table_names(connection)
        if _ALEMBIC_VERSION_TABLE not in tables:
            return None
        return _read_current_revision_from_connection(connection)


def _head_revision(config: Config) -> str:
    script_directory = ScriptDirectory.from_config(config)
    heads = sorted(script_directory.get_heads())
    if not heads:
        raise MigrationBootstrapError("No Alembic head revision found")
    if len(heads) == 1:
        return heads[0]
    return ",".join(heads)


def _max_revision_id_length(config: Config) -> int:
    script_directory = ScriptDirectory.from_config(config)
    lengths = [len(revision.revision) for revision in script_directory.walk_revisions() if revision.revision]
    if not lengths:
        raise MigrationBootstrapError("No Alembic revisions found")
    return max(lengths)


def _ensure_alembic_version_table_capacity_for_connection(connection: Connection, *, required_length: int) -> None:
    if connection.dialect.name != "postgresql":
        return

    inspector = inspect(connection)
    if not inspector.has_table(_ALEMBIC_VERSION_TABLE):
        connection.execute(
            text(
                " ".join(
                    (
                        f"CREATE TABLE {_ALEMBIC_VERSION_TABLE} (",
                        f"{_ALEMBIC_VERSION_COLUMN} VARCHAR({required_length}) NOT NULL,",
                        f"PRIMARY KEY ({_ALEMBIC_VERSION_COLUMN})",
                        ")",
                    )
                )
            )
        )
        return

    columns = inspector.get_columns(_ALEMBIC_VERSION_TABLE)
    version_num_column = next((column for column in columns if column.get("name") == _ALEMBIC_VERSION_COLUMN), None)
    if version_num_column is None:
        raise MigrationBootstrapError(
            f"{_ALEMBIC_VERSION_TABLE}.{_ALEMBIC_VERSION_COLUMN} is missing from migration metadata table"
        )
    version_num_type = version_num_column.get("type")
    version_num_length = getattr(version_num_type, "length", None)
    if version_num_length is None or version_num_length >= required_length:
        return

    connection.execute(
        text(
            f"ALTER TABLE {_ALEMBIC_VERSION_TABLE} "
            f"ALTER COLUMN {_ALEMBIC_VERSION_COLUMN} TYPE VARCHAR({required_length})"
        )
    )


def _ensure_alembic_version_table_capacity(config: Config) -> None:
    sync_database_url = _required_sqlalchemy_url(config)
    required_length = _max_revision_id_length(config)
    with _sync_transaction(sync_database_url) as connection:
        _ensure_alembic_version_table_capacity_for_connection(connection, required_length=required_length)


def _collect_migration_policy_violations(config: Config) -> tuple[str, ...]:
    violations: list[str] = []
    script_directory = ScriptDirectory.from_config(config)

    heads = sorted(script_directory.get_heads())
    if len(heads) != 1:
        violations.append(f"alembic_head_count_invalid expected=1 actual={len(heads)} heads={','.join(heads)}")

    seen_revision_ids: set[str] = set()
    for revision in script_directory.walk_revisions():
        revision_id = revision.revision
        if not revision_id:
            continue

        if revision_id in seen_revision_ids:
            violations.append(f"alembic_revision_duplicate revision={revision_id}")
        else:
            seen_revision_ids.add(revision_id)

        if not REVISION_ID_PATTERN.fullmatch(revision_id):
            violations.append(f"alembic_revision_id_format_invalid revision={revision_id}")

        revision_path = getattr(revision, "path", None)
        if revision_path:
            actual_name = Path(str(revision_path)).name
            expected_name = f"{revision_id}.py"
            if actual_name != expected_name:
                violations.append(
                    "alembic_revision_filename_mismatch "
                    f"revision={revision_id} expected={expected_name} actual={actual_name}"
                )

    return tuple(sorted(violations))


def check_migration_policy(database_url: str) -> tuple[str, ...]:
    config = _build_alembic_config(database_url)
    return _collect_migration_policy_violations(config)


def inspect_migration_state(database_url: str) -> MigrationState:
    config = _build_alembic_config(database_url)
    sync_database_url = _required_sqlalchemy_url(config)
    head_revision = _head_revision(config)

    with _sync_connection(sync_database_url) as connection:
        tables = _read_table_names(connection)
        has_alembic = _ALEMBIC_VERSION_TABLE in tables
        current = _read_current_revision_from_connection(connection) if has_alembic else None

    if has_alembic:
        needs_upgrade = current != head_revision
    else:
        needs_upgrade = True

    return MigrationState(
        current_revision=current,
        head_revision=head_revision,
        has_alembic_version_table=has_alembic,
        needs_upgrade=needs_upgrade,
    )


def check_schema_drift(database_url: str) -> tuple[str, ...]:
    config = _build_alembic_config(database_url)
    sync_database_url = _required_sqlalchemy_url(config)

    with _sync_connection(sync_database_url) as connection:
        return inspect_schema_drift(connection)


def _head_revision_target(config: Config, revision: str) -> str | None:
    head_revision = _head_revision(config)
    if revision in {"head", head_revision}:
        return head_revision
    return None


def _required_columns_exist(sync_database_url: str, required_columns: dict[str, frozenset[str]]) -> bool:
    with _sync_connection(sync_database_url) as connection:
        inspector = inspect(connection)
        for table_name, column_names in required_columns.items():
            if not inspector.has_table(table_name):
                return False
            existing = {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name")}
            if not column_names.issubset(existing):
                return False
    return True


def _revision_is_pending(current_revision: str | None, revision: str) -> bool:
    if current_revision is None:
        return True
    if "," in current_revision:
        return False
    return current_revision < revision


def _upgrade_with_existing_column_revision_skips(
    config: Config,
    *,
    revision: str,
    sync_database_url: str,
) -> None:
    if _head_revision_target(config, revision) is None:
        command.upgrade(config, revision)
        return

    for skip_revision, parent_revision, required_columns in _EXISTING_COLUMN_REVISION_SKIPS:
        current_revision = _read_current_revision(sync_database_url)
        if not _revision_is_pending(current_revision, skip_revision):
            continue
        if not _required_columns_exist(sync_database_url, required_columns):
            continue
        command.upgrade(config, parent_revision)
        current_revision = _read_current_revision(sync_database_url)
        if current_revision != skip_revision:
            logger.info(
                "Stamping Alembic revision because physical columns already exist revision=%s",
                skip_revision,
            )
            command.stamp(config, skip_revision)

    command.upgrade(config, revision)


def run_upgrade(database_url: str, revision: str = "head") -> MigrationRunResult:
    config = _build_alembic_config(database_url)
    state_before = inspect_migration_state(database_url)
    config.attributes["agent_lb_fresh_install"] = (
        state_before.current_revision is None and not state_before.has_alembic_version_table
    )

    _ensure_alembic_version_table_capacity(config)

    sync_database_url = _required_sqlalchemy_url(config)
    _upgrade_with_existing_column_revision_skips(config, revision=revision, sync_database_url=sync_database_url)

    return MigrationRunResult(current_revision=_read_current_revision(sync_database_url))


async def run_startup_migrations(database_url: str) -> MigrationRunResult:
    return await to_thread.run_sync(lambda: run_upgrade(database_url, "head"))


def current_revision(database_url: str) -> str | None:
    state = inspect_migration_state(database_url)
    return state.current_revision


def downgrade_refresh_intent_for_rollback(database_url: str) -> str:

    config = _build_alembic_config(database_url)
    revision = "20260926_000000_refresh_intent_expiry"
    parent = ScriptDirectory.from_config(config).get_revision(revision).down_revision
    if not isinstance(parent, str):
        raise MigrationBootstrapError("Refresh-intent rollback requires a single parent revision")
    if _read_current_revision(_required_sqlalchemy_url(config)) != revision:
        raise MigrationBootstrapError("Refresh-intent rollback requires its exact migration head")
    command.downgrade(config, parent)
    return parent


def stamp_revision(database_url: str, revision: str) -> None:
    config = _build_alembic_config(database_url)
    _ensure_alembic_version_table_capacity(config)
    command.stamp(config, revision)


def wait_for_connection(
    database_url: str,
    *,
    timeout_seconds: float,
    interval_seconds: float = 2.0,
) -> None:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than 0")

    started_at = time.monotonic()
    last_error: Exception | None = None
    sync_database_url = to_sync_database_url(database_url)

    while True:
        try:
            with _sync_connection(sync_database_url):
                return
        except Exception as exc:
            last_error = exc
        elapsed = time.monotonic() - started_at
        if elapsed >= timeout_seconds:
            if last_error is not None:
                raise TimeoutError(
                    f"Timed out waiting for database connectivity after {timeout_seconds:.1f}s: {last_error}"
                ) from last_error
            raise TimeoutError(f"Timed out waiting for database connectivity after {timeout_seconds:.1f}s")
        time.sleep(min(interval_seconds, timeout_seconds - elapsed))


def wait_for_head(
    database_url: str,
    *,
    timeout_seconds: float,
    interval_seconds: float = 2.0,
) -> MigrationState:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than 0")

    started_at = time.monotonic()
    last_error: Exception | None = None

    while True:
        try:
            state = inspect_migration_state(database_url)
            if not state.needs_upgrade:
                return state
        except Exception as exc:
            last_error = exc
        elapsed = time.monotonic() - started_at
        if elapsed >= timeout_seconds:
            if last_error is not None:
                raise TimeoutError(
                    f"Timed out waiting for database schema to reach Alembic head after {timeout_seconds:.1f}s: "
                    f"{last_error}"
                ) from last_error
            raise TimeoutError(
                f"Timed out waiting for database schema to reach Alembic head after {timeout_seconds:.1f}s"
            )
        time.sleep(min(interval_seconds, timeout_seconds - elapsed))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Database migration utility for agent-lb.")
    parser.add_argument(
        "--db-url",
        default=None,
        help="Database URL to migrate. Defaults to AGENT_LB_DATABASE_URL from settings.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    upgrade_parser = subparsers.add_parser("upgrade", help="Upgrade schema to a target revision.")
    upgrade_parser.add_argument("revision", nargs="?", default="head")

    subparsers.add_parser("current", help="Print current alembic revision.")

    subparsers.add_parser("check", help="Check Alembic policy and model/schema drift.")
    subparsers.add_parser(
        "rollback-refresh-intent", help="Downgrade the stage-1 refresh-intent migration before reverting the build."
    )

    wait_parser = subparsers.add_parser(
        "wait-for-head",
        help="Wait until the database schema reaches Alembic head without applying migrations locally.",
    )
    wait_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum seconds to wait for the schema to reach Alembic head.",
    )
    wait_parser.add_argument(
        "--interval-seconds",
        type=float,
        default=2.0,
        help="Polling interval in seconds while waiting for the schema to reach Alembic head.",
    )

    connect_parser = subparsers.add_parser(
        "wait-for-connection",
        help="Wait until the database accepts connections without applying migrations locally.",
    )
    connect_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum seconds to wait for database connectivity.",
    )
    connect_parser.add_argument(
        "--interval-seconds",
        type=float,
        default=2.0,
        help="Polling interval in seconds while waiting for database connectivity.",
    )

    stamp_parser = subparsers.add_parser("stamp", help="Set current revision without running migrations.")
    stamp_parser.add_argument("revision")

    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    database_url = args.db_url or get_settings().database_url

    if args.command == "upgrade":
        result = run_upgrade(database_url, args.revision)
        print(f"current_revision={result.current_revision or 'none'}")
        return

    if args.command == "rollback-refresh-intent":
        parent = downgrade_refresh_intent_for_rollback(database_url)
        print(f"current_revision={parent}")
        return

    if args.command == "current":
        revision = current_revision(database_url)
        print(revision or "none")
        return

    if args.command == "check":
        policy_violations = check_migration_policy(database_url)
        if policy_violations:
            print("migration_policy_violations_detected")
            for violation in policy_violations:
                print(violation)
        drift = check_schema_drift(database_url)
        if drift:
            print("schema_drift_detected")
            for diff in drift:
                print(diff)
        if policy_violations or drift:
            raise SystemExit(1)
        print("migration_policy=ok")
        print("schema_drift=none")
        return

    if args.command == "stamp":
        stamp_revision(database_url, args.revision)
        print(f"stamped={args.revision}")
        return

    if args.command == "wait-for-head":
        state = wait_for_head(
            database_url,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
        )
        print(f"current_revision={state.current_revision or 'none'}")
        print(f"head_revision={state.head_revision}")
        return

    if args.command == "wait-for-connection":
        wait_for_connection(
            database_url,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
        )
        print("database_connection=ready")
        return

    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
