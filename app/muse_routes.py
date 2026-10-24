from fastapi import FastAPI

from app.modules.proxy.meta_service import provider_router, router
from app.modules.proxy.muse_claude_api import router as claude_router


def register_muse_routes(app: FastAPI) -> None:
    app.include_router(router)
    app.include_router(provider_router)
    app.include_router(claude_router)
