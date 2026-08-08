"""API entrypoint."""

from __future__ import annotations

from novel_agent.api.app import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
