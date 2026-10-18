from __future__ import annotations

import warnings

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.db.models import Base

_MANUAL_DRIFT_INDEX_REQUIREMENTS: dict[str, frozenset[str]] = {
    "usage_history": frozenset(
        {
            "idx_usage_window_account_latest",
            "idx_usage_window_account_time",
            "idx_usage_window_raw_account_latest",
        }
    ),
    "request_logs": frozenset(
        {
            "idx_logs_requested_at_id",
            "idx_logs_deleted_at_requested_at_id",
            "idx_logs_requested_at_model_tier",
            "idx_logs_model_effort_time",
            "idx_logs_status_error_time",
            "idx_logs_api_key_time_account",
            "idx_logs_source_requested_at",
        }
    ),
    "account_limit_warmups": frozenset(
        {
            "idx_account_limit_warmups_account_attempted",
            "idx_account_limit_warmups_status_attempted",
        }
    ),
    "api_keys": frozenset({"idx_api_keys_name"}),
}
_SQLITE_FLOAT_TYPE_COMPAT_COLUMNS = frozenset(
    {
        ("dashboard_settings", "sticky_reallocation_primary_budget_threshold_pct"),
        ("dashboard_settings", "sticky_reallocation_secondary_budget_threshold_pct"),
    }
)
_LEGACY_EXTRA_COLUMNS = frozenset(
    {
        ("request_logs", "slim_summary_json"),
    }
)


def _sqlite_index_names(connection: Connection, table_name: str) -> set[str]:
    escaped_name = table_name.replace('"', '""')
    rows = connection.execute(text(f'PRAGMA index_list("{escaped_name}")')).fetchall()
    return {str(row[1]) for row in rows if len(row) > 1 and row[1] is not None}


def _postgresql_index_names(connection: Connection, table_name: str) -> set[str]:
    rows = connection.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = ANY (current_schemas(false))
              AND tablename = :table_name
            """
        ),
        {"table_name": table_name},
    ).fetchall()
    return {str(row[0]) for row in rows if row and row[0] is not None}


def _read_index_names_for_drift(connection: Connection, table_name: str) -> set[str]:
    if connection.dialect.name == "sqlite":
        return _sqlite_index_names(connection, table_name)
    if connection.dialect.name == "postgresql":
        return _postgresql_index_names(connection, table_name)

    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def _manual_schema_drift_diffs(connection: Connection) -> tuple[str, ...]:
    diffs: list[str] = []
    for table_name, required_indexes in _MANUAL_DRIFT_INDEX_REQUIREMENTS.items():
        existing_indexes = _read_index_names_for_drift(connection, table_name)
        for index_name in sorted(required_indexes - existing_indexes):
            diffs.append(repr(("missing_index", table_name, index_name)))
    return tuple(diffs)


def _unwrap_schema_drift_diff(diff: object) -> object:
    if isinstance(diff, list) and len(diff) == 1:
        return diff[0]
    return diff


def _is_ignored_schema_drift(connection: Connection, diff: object) -> bool:
    diff = _unwrap_schema_drift_diff(diff)
    if not isinstance(diff, tuple) or not diff:
        return False

    if diff[0] == "remove_column" and len(diff) >= 4:
        column = diff[3]
        column_name = getattr(column, "name", None)
        if (str(diff[2]), str(column_name)) in _LEGACY_EXTRA_COLUMNS:
            return True

    if connection.dialect.name == "sqlite" and diff[0] == "modify_type" and len(diff) >= 7:
        table_name = str(diff[2])
        column_name = str(diff[3])
        if (table_name, column_name) not in _SQLITE_FLOAT_TYPE_COMPAT_COLUMNS:
            return False
        existing_type = diff[5]
        metadata_type = diff[6]
        return str(existing_type).upper() == "REAL" and str(metadata_type).upper() in {"FLOAT", "REAL"}

    return False


def inspect_schema_drift(connection: Connection) -> tuple[str, ...]:
    migration_context = MigrationContext.configure(
        connection=connection,
        opts={
            "target_metadata": Base.metadata,
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Skipped unsupported reflection of expression-based index .*",
        )
        warnings.filterwarnings(
            "ignore",
            message=r"autogenerate skipping metadata-specified expression-based index .*",
        )
        diffs = [
            diff
            for diff in compare_metadata(migration_context, Base.metadata)
            if not _is_ignored_schema_drift(connection, diff)
        ]
    manual_diffs = _manual_schema_drift_diffs(connection)

    return tuple(repr(diff) for diff in diffs) + manual_diffs
