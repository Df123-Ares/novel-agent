"""API entrypoint (FastAPI).

NOTE: this layer is kept for external API integration reference only.
The primary UI entrypoint is webui.py (Flask, port 7860), which is the
only service started by start.bat / start.ps1.
"""

from __future__ import annotations

from novel_agent.api.app import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
