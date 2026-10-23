from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles


def resolve_static_asset_path(static_root: Path, requested_path: str) -> Path | None:

    normalized = PurePosixPath(requested_path)
    if normalized.is_absolute() or ".." in normalized.parts:
        return None
    full_path, stat_result = StaticFiles(directory=static_root, check_dir=False).lookup_path(normalized.as_posix())
    if stat_result is None or not stat.S_ISREG(stat_result.st_mode):
        return None
    return Path(full_path)


def configure_static_routes(app: FastAPI) -> None:
    static_dir = Path(__file__).parent / "static"
    index_html = static_dir / "index.html"
    static_root = static_dir.resolve()
    frontend_build_hint = "Frontend assets are missing. Run `cd frontend && bun run build`."
    excluded_prefixes = ("api/", "v1/", "backend-api/", "health")

    def _is_static_asset_path(path: str) -> bool:
        if path.startswith("assets/"):
            return True
        last_segment = path.rsplit("/", maxsplit=1)[-1]
        return "." in last_segment

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str = ""):
        normalized = path.lstrip("/")
        if normalized and any(
            normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in excluded_prefixes
        ):
            raise HTTPException(status_code=404, detail="Not Found")

        if normalized:
            candidate = resolve_static_asset_path(static_root, normalized)
            if candidate is not None:
                if normalized.startswith("assets/"):
                    return FileResponse(candidate, headers={"Cache-Control": "public, max-age=31536000, immutable"})
                return FileResponse(candidate)
            if _is_static_asset_path(normalized):
                raise HTTPException(status_code=404, detail="Not Found")

        if not index_html.is_file():
            raise HTTPException(status_code=503, detail=frontend_build_hint)

        return FileResponse(
            index_html,
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )
