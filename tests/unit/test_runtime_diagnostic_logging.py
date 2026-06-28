from __future__ import annotations

import logging

import pytest

import app.main as main
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.config.settings import Settings

pytestmark = pytest.mark.unit


def test_runtime_diagnostic_fingerprint_uses_safe_runtime_posture_fields(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_url="postgresql+asyncpg://user:password@db.example/agentlb",
        dashboard_auth_mode=DashboardAuthMode.TRUSTED_HEADER,
        firewall_trust_proxy_headers=True,
        firewall_trusted_proxy_cidrs=["127.0.0.1/32", "100.64.0.0/10"],
        proxy_unauthenticated_client_cidrs=["100.64.0.0/10"],
        metrics_enabled=True,
        metrics_host="127.0.0.1",
        metrics_port=9091,
        proxy_account_response_create_limit=0,
        proxy_account_stream_limit=0,
        http_responses_session_bridge_enabled=True,
        http_responses_session_bridge_instance_id="unit-test",
        http_responses_session_bridge_max_sessions=1024,
        http_responses_session_bridge_queue_limit=64,
        http_responses_session_bridge_response_create_concurrency=64,
    )

    fingerprint = main._runtime_diagnostic_fingerprint(settings, bridge_durable_schema_ready=True)

    assert fingerprint["event"] == "agent_lb_runtime_fingerprint"
    assert fingerprint["database_backend"] == "postgresql"
    assert fingerprint["data_dir"] == str(tmp_path)
    assert fingerprint["dashboard_auth_mode"] == "trusted_header"
    assert fingerprint["firewall_trusted_proxy_cidrs_count"] == 2
    assert fingerprint["proxy_unauthenticated_client_cidrs_count"] == 1
    assert fingerprint["proxy_unauthenticated_tailnet_allowed"] is True
    assert fingerprint["metrics_enabled"] is True
    assert fingerprint["metrics_port"] == 9091
    assert fingerprint["proxy_account_response_create_limit"] == 0
    assert fingerprint["proxy_account_stream_limit"] == 0
    assert fingerprint["bridge_schema_status"] == "ready"


def test_runtime_diagnostic_fingerprint_log_omits_raw_database_url(tmp_path, caplog):
    settings = Settings(
        data_dir=tmp_path,
        database_url="postgresql+asyncpg://user:password@db.example/agentlb",
        metrics_enabled=True,
        metrics_port=9091,
        http_responses_session_bridge_enabled=True,
        http_responses_session_bridge_instance_id="unit-test",
    )
    caplog.set_level(logging.INFO, logger=main.logger.name)

    main._log_runtime_diagnostic_fingerprint(settings, bridge_durable_schema_ready=None)

    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "agent_lb_runtime_fingerprint"
    )
    assert record.database_backend == "postgresql"
    assert record.bridge_schema_status == "not_checked"
    assert "postgresql+asyncpg://" not in record.getMessage()
    assert "password" not in record.getMessage()
