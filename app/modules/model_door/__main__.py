from __future__ import annotations

import uvicorn

from app.modules.model_door.config import DoorConfig
from app.modules.model_door.door import build_app


def main() -> None:
    config = DoorConfig.from_env()
    uvicorn.run(
        build_app(config),
        host=config.host,
        port=config.port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        log_level="warning",
    )


if __name__ == "__main__":
    main()
