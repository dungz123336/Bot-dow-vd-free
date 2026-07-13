"""Download public Yandex Disk albums/files (images + videos).

Yandex cloud download API often returns empty href for public folders.
Strategy:
  - Images: ORIGINAL size URL from cloud-api
  - Videos: HLS streams via public/api/get-video-streams + FFmpeg
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import httpx

from bot.jobs import DownloadJob, JobCancelled
from bot.splitter import FFMPEG

logger = logging.getLogger(__name__)

API_RESOURCES = "https://cloud-api.yandex.net/v1/disk/public/resources"
PUBLIC_API = "https://disk.yandex.com/public/api"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ProgressCallback = Callable[[str], Any]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".ts", ".flv"}


def is_yandex_disk_url(url: str) -> bool:
    u = (url or "").lower()
    if not u.startswith("http"):
        return False
    if any(h in u for h in ("disk.yandex.", "yadi.sk", "disk.360.yandex.")):
        return True
    return False


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    return cleaned[:180] or "file"


def _parse_store(html: str) -> dict[str, Any] | None:
    m = re.search(
        r'<script[^>]+id="store-prefetch"[^>]*>\s*(\{.+?\})\s*</script>',
        html,
        re.S,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _list_api_files(client: httpx.Client, public_key: str) -> tuple[str, list[dict[str, Any]]]:
    """List all files via cloud API (supports pagination + subfolders)."""
    files: list[dict[str, Any]] = []
    title = "Yandex Disk"

    def walk(path: str = "/") -> None:
        nonlocal title
        offset = 0
        limit = 100
        while True:
            r = client.get(
                API_RESOURCES,
                params={"public_key": public_key, "path": path, "limit": limit, "offset": offset},
            )
            r.raise_for_status()
            data = r.json()
            if path == "/":
                title = data.get("name") or title
                if data.get("type") == "file":
                    files.append(data)
                    return
            embedded = data.get("_embedded") or {}
            items = embedded.get("items") or []
            total = int(embedded.get("total") or 0)
            for it in items:
                if it.get("type") == "dir":
                    walk(it.get("path") or "/")
                elif it.get("type") == "file":
                    files.append(it)
            offset += limit
            if offset >= total or not items:
                break

    walk("/")
    return title, files


def _media_kind(item: dict[str, Any]) -> str | None:
    mime = (item.get("mime_type") or "").lower()
    media = (item.get("media_type") or "").lower()
    name = (item.get("name") or "").lower()
    ext = Path(name).suffix.lower()
    if media == "image" or mime.startswith("image/") or ext in IMAGE_EXTS:
        return "image"
    if media == "video" or mime.startswith("video/") or ext in VIDEO_EXTS:
        return "video"
    return None


def _download_image(client: httpx.Client, item: dict[str, Any], out: Path) -> bool:
    sizes = item.get("sizes") or []
    url = next((s.get("url") for s in sizes if s.get("name") == "ORIGINAL"), None)
    if not url:
        # fallback largest preview
        for name in ("XXXL", "XXL", "XL", "L", "DEFAULT"):
            url = next((s.get("url") for s in sizes if s.get("name") == name), None)
            if url:
                break
    if not url:
        return False
    r = client.get(url)
    r.raise_for_status()
    if len(r.content) < 50:
        return False
    out.write_bytes(r.content)
    return True


def _build_path_hash(public_key_hash: str, filename: str) -> str:
    # Yandex resource path format: "{folderPublicKey}:/{filename}"
    return f"{public_key_hash}:/{filename}"


def _get_store_context(client: httpx.Client, url: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
    page = client.get(url)
    page.raise_for_status()
    store = _parse_store(page.text)
    if not store:
        return None, None, None
    env = store.get("environment") or {}
    sk = env.get("sk")
    yandexuid = env.get("yandexuid")
    if yandexuid:
        client.cookies.set("yandexuid", str(yandexuid), domain="disk.yandex.com")
        client.cookies.set("yandexuid", str(yandexuid), domain=".yandex.com")
    return store, sk, str(yandexuid) if yandexuid else None


def _resource_map(store: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map filename -> resource from store-prefetch."""
    out: dict[str, dict[str, Any]] = {}
    for res in (store.get("resources") or {}).values():
        name = res.get("name")
        if name and res.get("type") == "file":
            out[name] = res
    return out


def _download_video_hls(
    client: httpx.Client,
    *,
    file_hash: str,
    sk: str,
    out: Path,
    referer: str,
    job: DownloadJob | None = None,
) -> bool:
    body = json.dumps({"hash": file_hash, "sk": sk}).encode()
    r = client.post(
        f"{PUBLIC_API}/get-video-streams",
        content=body,
        headers={"Content-Type": "text/plain", "Referer": referer, "Origin": "https://disk.yandex.com"},
    )
    if r.status_code != 200:
        logger.warning("get-video-streams HTTP %s: %s", r.status_code, r.text[:200])
        return False
    payload = r.json() or {}
    if payload.get("error"):
        logger.warning("get-video-streams error: %s", payload)
        return False
    data = payload.get("data") or {}
    videos = data.get("videos") or []
    if not videos:
        return False

    adaptive = None
    best = None
    best_h = -1
    for v in videos:
        if v.get("dimension") == "adaptive":
            adaptive = v
            continue
        h = int((v.get("size") or {}).get("height") or 0)
        if h >= best_h and v.get("url"):
            best = v
            best_h = h
    stream = adaptive or best
    if not stream or not stream.get("url"):
        return False

    cmd = [
        FFMPEG,
        "-y",
        "-headers",
        f"Referer: {referer}\r\nUser-Agent: {UA}\r\n",
        "-i",
        stream["url"],
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        "-movflags",
        "+faststart",
        str(out),
    ]
    try:
        creation = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creation,
        )
        if job is not None:
            job.active_proc = proc
        try:
            # Poll so we can honor pause/cancel
            while True:
                if job is not None:
                    if job.is_cancelled():
                        proc.kill()
                        proc.wait(timeout=5)
                        out.unlink(missing_ok=True)
                        raise JobCancelled("Đã dừng tải theo yêu cầu.")
                    job.wait_if_paused()
                ret = proc.poll()
                if ret is not None:
                    break
                # brief sleep
                try:
                    proc.wait(timeout=0.4)
                except subprocess.TimeoutExpired:
                    pass
            stderr = ""
            if proc.stderr:
                try:
                    stderr = proc.stderr.read() or ""
                except Exception:
                    pass
            if proc.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
                logger.warning("ffmpeg hls failed: %s", stderr[-500:])
                out.unlink(missing_ok=True)
                return False
            return True
        finally:
            if job is not None:
                job.active_proc = None
    except JobCancelled:
        raise
    except Exception as e:
        logger.warning("ffmpeg hls exception: %s", e)
        out.unlink(missing_ok=True)
        return False


def download_yandex_disk(
    url: str,
    work_dir: Path,
    progress: ProgressCallback | None = None,
    job: DownloadJob | None = None,
) -> tuple[bool, str, list[tuple[Path, str, int]], str | None]:
    """
    Download all images/videos from a public Yandex Disk link.

    Returns: (ok, title, [(path, media_type, size), ...], error)
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    public_url = url.strip()
    headers = {"User-Agent": UA, "Referer": public_url, "Origin": "https://disk.yandex.com"}

    try:
        with httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0), follow_redirects=True, headers=headers) as client:
            if progress:
                progress("🔍 Đang đọc Yandex Disk...")

            store, sk, _uid = _get_store_context(client, public_url)
            title, api_files = _list_api_files(client, public_url)
            media_items = [(f, _media_kind(f)) for f in api_files]
            media_items = [(f, k) for f, k in media_items if k]

            if not media_items:
                return False, title, [], "Thư mục Yandex không có ảnh/video công khai."

            res_by_name = _resource_map(store) if store else {}
            root_hash = None
            if store:
                root = (store.get("resources") or {}).get(store.get("rootResourceId") or "")
                root_hash = (root or {}).get("hash")

            if progress:
                progress(f"📂 «{title}» — {len(media_items)} file. Bắt đầu tải...")

            results: list[tuple[Path, str, int]] = []
            total = len(media_items)
            if job is not None:
                job.set_progress(0, total, f"Yandex · {total} file")

            for idx, (item, kind) in enumerate(media_items, start=1):
                if job is not None:
                    job.wait_if_paused()
                    if job.is_cancelled():
                        raise JobCancelled("Đã dừng tải theo yêu cầu.")
                    job.set_progress(idx, total, f"Yandex [{idx}/{total}]")

                name = _safe_name(item.get("name") or f"file_{idx}")
                out = work_dir / name
                if out.exists():
                    out = work_dir / f"{idx:03d}_{name}"

                if progress:
                    progress(f"⬇️ Yandex [{idx}/{total}] {name[:48]} ({kind})")

                ok = False
                try:
                    if kind == "image":
                        ok = _download_image(client, item, out)
                    else:
                        # video via HLS
                        if not sk:
                            logger.warning("No sk cookie for video %s", name)
                            continue
                        res = res_by_name.get(item.get("name") or "")
                        file_hash = (res or {}).get("path")
                        if not file_hash and root_hash:
                            file_hash = _build_path_hash(root_hash, item.get("name") or name)
                        if not file_hash:
                            logger.warning("No path hash for %s", name)
                            continue
                        # ensure mp4 extension for ffmpeg output
                        if out.suffix.lower() not in VIDEO_EXTS:
                            out = out.with_suffix(".mp4")
                        ok = _download_video_hls(
                            client,
                            file_hash=file_hash,
                            sk=sk,
                            out=out,
                            referer=public_url,
                            job=job,
                        )
                except JobCancelled:
                    raise
                except Exception as e:
                    logger.warning("download item %s failed: %s", name, e)
                    ok = False

                if ok and out.exists() and out.stat().st_size > 100:
                    results.append((out, kind, out.stat().st_size))
                else:
                    out.unlink(missing_ok=True)

            if not results:
                return False, title, [], "Không tải được file nào từ Yandex Disk."
            return True, title, results, None

    except JobCancelled:
        raise
    except httpx.HTTPStatusError as e:
        logger.exception("Yandex HTTP error")
        return False, "Yandex Disk", [], f"Yandex API lỗi HTTP {e.response.status_code}"
    except Exception as e:
        logger.exception("Yandex download failed")
        return False, "Yandex Disk", [], f"Lỗi Yandex Disk: {e}"
