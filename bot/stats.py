"""Simple JSON-backed statistics for the web dashboard."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from bot.config import STATS_FILE

_lock = threading.Lock()


def _empty() -> dict[str, Any]:
    return {
        "total_jobs": 0,
        "success_jobs": 0,
        "failed_jobs": 0,
        "videos_sent": 0,
        "images_sent": 0,
        "users": {},
        "recent": [],
        "started_at": time.time(),
        "bot_online": False,
        "last_error": None,
    }


def load_stats() -> dict[str, Any]:
    with _lock:
        if not STATS_FILE.exists():
            data = _empty()
            _write(data)
            return data
        try:
            return json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return _empty()


def _write(data: dict[str, Any]) -> None:
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_bot_online(online: bool) -> None:
    data = load_stats()
    with _lock:
        data["bot_online"] = online
        _write(data)


def set_last_error(err: str | None) -> None:
    data = load_stats()
    with _lock:
        data["last_error"] = err
        _write(data)


def record_job(
    *,
    user_id: int,
    username: str | None,
    url: str,
    success: bool,
    media_type: str,
    parts: int = 0,
    error: str | None = None,
) -> None:
    data = load_stats()
    with _lock:
        data["total_jobs"] = data.get("total_jobs", 0) + 1
        if success:
            data["success_jobs"] = data.get("success_jobs", 0) + 1
        else:
            data["failed_jobs"] = data.get("failed_jobs", 0) + 1
            data["last_error"] = error

        if media_type == "video":
            data["videos_sent"] = data.get("videos_sent", 0) + max(parts, 1 if success else 0)
        elif media_type == "image":
            data["images_sent"] = data.get("images_sent", 0) + max(parts, 1 if success else 0)

        users = data.setdefault("users", {})
        key = str(user_id)
        u = users.get(key, {"username": username, "jobs": 0})
        u["username"] = username or u.get("username")
        u["jobs"] = u.get("jobs", 0) + 1
        users[key] = u

        recent = data.setdefault("recent", [])
        recent.insert(
            0,
            {
                "ts": time.time(),
                "user_id": user_id,
                "username": username,
                "url": url[:200],
                "success": success,
                "media_type": media_type,
                "parts": parts,
                "error": error,
            },
        )
        data["recent"] = recent[:50]
        _write(data)
