"""Media downloader powered by yt-dlp (video, audio, images, albums)."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yt_dlp

from bot.config import DOWNLOADS_DIR
from bot.jobs import DownloadJob, JobCancelled
from bot.yandex_disk import download_yandex_disk, is_yandex_disk_url

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".ts", ".flv"}


@dataclass
class MediaItem:
    path: Path
    media_type: str  # "video" | "image" | "other"
    title: str = ""
    duration: float | None = None
    filesize: int = 0


@dataclass
class DownloadResult:
    ok: bool
    items: list[MediaItem] = field(default_factory=list)
    title: str = ""
    error: str | None = None
    work_dir: Path | None = None


ProgressCallback = Callable[[str], Any]


def _classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    # Guess by name patterns from yt-dlp
    name = path.name.lower()
    if any(x in name for x in ("thumb", "image", "photo")):
        return "image"
    return "other"


def _safe_title(title: str | None) -> str:
    if not title:
        return "media"
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip()
    return (cleaned[:80] or "media")


async def download_media(
    url: str,
    *,
    progress: ProgressCallback | None = None,
    max_height: int = 1080,
    job: DownloadJob | None = None,
) -> DownloadResult:
    """Download all media from a URL (single video, album, gallery, etc.)."""
    job_id = uuid.uuid4().hex[:12]
    work_dir = DOWNLOADS_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    # Yandex Disk public albums (images + videos) — yt-dlp often fails on folders
    if is_yandex_disk_url(url):
        def _yandex_run() -> DownloadResult:
            try:
                ok, title, pairs, err = download_yandex_disk(
                    url, work_dir, progress=progress, job=job
                )
            except JobCancelled:
                return DownloadResult(ok=False, error="cancelled", work_dir=work_dir)
            if not ok:
                return DownloadResult(ok=False, error=err or "Yandex Disk lỗi", work_dir=work_dir)
            items = [
                MediaItem(path=p, media_type=mt, title=_safe_title(title), filesize=sz)
                for p, mt, sz in pairs
            ]
            items.sort(key=lambda x: (0 if x.media_type == "video" else 1, x.path.name))
            return DownloadResult(ok=True, items=items, title=_safe_title(title), work_dir=work_dir)

        return await asyncio.to_thread(_yandex_run)

    def _hook(d: dict[str, Any]) -> None:
        if job is not None:
            if job.is_cancelled():
                raise yt_dlp.utils.DownloadError("cancelled")
            job.wait_if_paused()
        if not progress:
            return
        status = d.get("status")
        if status == "downloading":
            pct = d.get("_percent_str") or ""
            speed = d.get("_speed_str") or ""
            eta = d.get("_eta_str") or ""
            try:
                progress(f"⬇️ Đang tải... {pct.strip()} | {speed.strip()} | ETA {eta.strip()}")
            except Exception:
                pass
        elif status == "finished":
            try:
                progress("📦 Đang xử lý file...")
            except Exception:
                pass

    ydl_opts: dict[str, Any] = {
        "outtmpl": str(work_dir / "%(playlist_index|)s%(playlist_index& - |)s%(title).80B [%(id)s].%(ext)s"),
        "restrictfilenames": False,
        "windowsfilenames": True,
        "noplaylist": False,  # cho phép album / playlist
        "ignoreerrors": True,
        "no_warnings": False,
        "quiet": True,
        "no_color": True,
        "progress_hooks": [_hook],
        # Ưu tiên mp4, giới hạn độ phân giải để vừa Telegram
        "format": (
            f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
            f"b[height<={max_height}][ext=mp4]/"
            f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b"
        ),
        "merge_output_format": "mp4",
        "writethumbnail": False,
        "writesubtitles": False,
        "writeinfojson": False,
        # Tải cả ảnh nếu extractor hỗ trợ
        "format_sort": ["res:1080", "ext:mp4:m4a"],
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
    }

    def _run() -> DownloadResult:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    return DownloadResult(ok=False, error="Không lấy được thông tin từ link.", work_dir=work_dir)

                title = _safe_title(info.get("title") or info.get("id") or "media")
                entries = info.get("entries")
                items: list[MediaItem] = []

                # Thu thập file đã tải trong work_dir
                files = sorted(
                    [p for p in work_dir.rglob("*") if p.is_file() and p.suffix.lower() not in {".json", ".part", ".ytdl", ".temp"}],
                    key=lambda p: p.stat().st_mtime,
                )

                if not files:
                    # Thử fallback: tải best available
                    return DownloadResult(
                        ok=False,
                        error="Không tải được file nào từ link này (có thể bị chặn hoặc link không hỗ trợ).",
                        work_dir=work_dir,
                    )

                for fpath in files:
                    mtype = _classify(fpath)
                    if mtype == "other":
                        # Bỏ file lạ nhỏ (metadata)
                        if fpath.stat().st_size < 50_000 and fpath.suffix.lower() not in VIDEO_EXTS | IMAGE_EXTS:
                            continue
                        mtype = "video" if fpath.stat().st_size > 200_000 else "image"

                    duration = None
                    # Lấy duration từ info nếu single
                    if not entries and info.get("duration"):
                        duration = float(info["duration"])

                    items.append(
                        MediaItem(
                            path=fpath,
                            media_type=mtype,
                            title=title,
                            duration=duration,
                            filesize=fpath.stat().st_size,
                        )
                    )

                if not items:
                    return DownloadResult(ok=False, error="Không tìm thấy video/ảnh hợp lệ sau khi tải.", work_dir=work_dir)

                # Sắp xếp: video trước, rồi ảnh
                items.sort(key=lambda x: (0 if x.media_type == "video" else 1, x.path.name))
                return DownloadResult(ok=True, items=items, title=title, work_dir=work_dir)

        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            if "cancelled" in msg.lower() or (job is not None and job.is_cancelled()):
                return DownloadResult(ok=False, error="cancelled", work_dir=work_dir)
            logger.exception("yt-dlp download error")
            return DownloadResult(ok=False, error=f"Lỗi tải: {e}", work_dir=work_dir)
        except JobCancelled:
            return DownloadResult(ok=False, error="cancelled", work_dir=work_dir)
        except Exception as e:
            logger.exception("download failed")
            return DownloadResult(ok=False, error=f"Lỗi không xác định: {e}", work_dir=work_dir)

    return await asyncio.to_thread(_run)


def cleanup_dir(path: Path | None) -> None:
    if not path:
        return
    try:
        if path.exists() and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        logger.warning("cleanup failed for %s", path)
