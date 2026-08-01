"""python -m novel_agent.web — start the local test console."""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "novel_agent.web.app:app",
        host=os.environ.get("NOVEL_WEB_HOST", "127.0.0.1"),
        port=int(os.environ.get("NOVEL_WEB_PORT", "8000")),
        reload=bool(os.environ.get("NOVEL_WEB_RELOAD")),
    )
