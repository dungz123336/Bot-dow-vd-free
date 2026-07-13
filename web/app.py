"""FastAPI web dashboard for the Telegram media bot."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bot import stats
from bot.config import ADMIN_ID, ADMIN_ONLY, DEFAULT_SPLIT_SECONDS, MAX_TELEGRAM_MB, WEB_HOST, WEB_PORT

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

app = FastAPI(title="Telegram Media Bot Dashboard", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    s = stats.load_stats()
    uptime = 0
    if s.get("started_at"):
        uptime = int(time.time() - float(s["started_at"]))
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "admin_id": ADMIN_ID,
            "admin_only": ADMIN_ONLY,
            "default_split": DEFAULT_SPLIT_SECONDS,
            "max_mb": MAX_TELEGRAM_MB,
            "bot_online": s.get("bot_online", False),
            "uptime": uptime,
        },
    )


@app.get("/api/stats")
async def api_stats() -> JSONResponse:
    s = stats.load_stats()
    started = float(s.get("started_at") or time.time())
    return JSONResponse(
        {
            "total_jobs": s.get("total_jobs", 0),
            "success_jobs": s.get("success_jobs", 0),
            "failed_jobs": s.get("failed_jobs", 0),
            "videos_sent": s.get("videos_sent", 0),
            "images_sent": s.get("images_sent", 0),
            "users_count": len(s.get("users") or {}),
            "users": s.get("users") or {},
            "recent": s.get("recent") or [],
            "bot_online": s.get("bot_online", False),
            "last_error": s.get("last_error"),
            "uptime_sec": int(time.time() - started),
            "config": {
                "admin_id": ADMIN_ID,
                "admin_only": ADMIN_ONLY,
                "default_split_seconds": DEFAULT_SPLIT_SECONDS,
                "max_telegram_mb": MAX_TELEGRAM_MB,
            },
        }
    )


@app.get("/api/health")
async def health() -> JSONResponse:
    s = stats.load_stats()
    return JSONResponse({"ok": True, "bot_online": s.get("bot_online", False)})


def run_web() -> None:
    import uvicorn

    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="info")
