"""Local entrypoint. Use this instead of bare `uvicorn app.main:app` on Windows —
uvicorn would otherwise start a ProactorEventLoop, which psycopg's async driver
cannot use (see app/platform_compat.py). On Linux/macOS it behaves identically to
uvicorn's own runner.
"""

from __future__ import annotations

import os

import uvicorn

from app import platform_compat


def main() -> None:
    config = uvicorn.Config(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        # 'none' stops uvicorn from installing its own (Windows-incompatible) loop;
        # platform_compat.run supplies the right one.
        loop="none",
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
    server = uvicorn.Server(config)
    platform_compat.run(server.serve())


if __name__ == "__main__":
    main()
