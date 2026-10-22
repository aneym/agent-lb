from __future__ import annotations

import asyncio
import logging
from importlib import import_module

from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE, make_scrape_registry
from app.core.metrics.serve import serve_metrics

metrics_logger = logging.getLogger("app.main")


def start_metrics_server(settings):
    metrics_server = None
    metrics_server_task = None
    if settings.metrics_enabled and PROMETHEUS_AVAILABLE:
        import uvicorn

        scrape_registry = make_scrape_registry()
        prometheus_module = import_module("prometheus_client")
        make_asgi_app = getattr(prometheus_module, "make_asgi_app")
        metrics_app = make_asgi_app(registry=scrape_registry)
        config = uvicorn.Config(
            metrics_app,
            host=settings.metrics_host,
            port=settings.metrics_port,
            log_level="warning",
        )
        metrics_server = uvicorn.Server(config)

        metrics_server_task = asyncio.create_task(serve_metrics(metrics_server, settings.metrics_port))
    elif settings.metrics_enabled:
        metrics_logger.warning("Metrics endpoint enabled but prometheus-client is not installed")

    return metrics_server, metrics_server_task
