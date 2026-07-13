"""Split videos into fixed-duration segments with FFmpeg."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from bot.config import MAX_TELEGRAM_BYTES

logger = logging.getLogger(__name__)


def _find_ffmpeg() -> str:
    which = shutil.which("ffmpeg")
    if which:
        return which
    # Common WinGet install path pattern (user machine)
    candidates = list(Path(r"C:\Users\ADMIN\AppData\Local\Microsoft\WinGet\Packages").glob(
        "Gyan.FFmpeg_*\\ffmpeg-*\\bin\\ffmpeg.exe"
    ))
    if candidates:
        return str(candidates[0])
    return "ffmpeg"


def _find_ffprobe() -> str:
    which = shutil.which("ffprobe")
    if which:
        return which
    ffmpeg = _find_ffmpeg()
    probe = Path(ffmpeg).with_name("ffprobe.exe" if ffmpeg.lower().endswith(".exe") else "ffprobe")
    if probe.exists():
        return str(probe)
    return "ffprobe"


FFMPEG = _find_ffmpeg()
FFPROBE = _find_ffprobe()


def get_duration(path: Path) -> float:
    """Return media duration in seconds."""
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=60)
        return max(float(out.strip()), 0.0)
    except Exception as e:
        logger.warning("ffprobe duration failed: %s", e)
        return 0.0


def _run_ffmpeg(cmd: list[str], timeout: int = 1800) -> None:
    logger.info("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[-1500:]
        raise RuntimeError(f"FFmpeg failed ({proc.returncode}): {err}")


def split_video(path: Path, segment_seconds: int, out_dir: Path | None = None) -> list[Path]:
    """
    Split video into segments of `segment_seconds`.
    Returns list of output paths (even a single file if no split needed).
    """
    if segment_seconds <= 0:
        return [path]

    duration = get_duration(path)
    if duration <= 0:
        # Không đo được duration — vẫn thử segment
        pass
    elif duration <= segment_seconds + 0.5:
        return [path]

    work = out_dir or (path.parent / f"parts_{path.stem}")
    work.mkdir(parents=True, exist_ok=True)
    pattern = work / f"{path.stem}_part_%03d.mp4"

    # -c copy nhanh nhưng cắt tại keyframe; re-encode nếu cần chính xác hơn
    # Ưu tiên copy + segment; fallback re-encode
    cmd_copy = [
        FFMPEG,
        "-y",
        "-i",
        str(path),
        "-c",
        "copy",
        "-map",
        "0",
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-reset_timestamps",
        "1",
        "-break_non_keyframes",
        "1",
        str(pattern),
    ]
    try:
        _run_ffmpeg(cmd_copy)
    except Exception:
        logger.warning("copy-split failed, re-encoding...")
        # Clean partial
        for p in work.glob(f"{path.stem}_part_*.mp4"):
            p.unlink(missing_ok=True)
        cmd_re = [
            FFMPEG,
            "-y",
            "-i",
            str(path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{segment_seconds})",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            str(pattern),
        ]
        _run_ffmpeg(cmd_re)

    parts = sorted(work.glob(f"{path.stem}_part_*.mp4"))
    # Bỏ part rỗng
    parts = [p for p in parts if p.stat().st_size > 1000]
    if not parts:
        return [path]
    return parts


def compress_if_needed(path: Path, max_bytes: int = MAX_TELEGRAM_BYTES) -> Path:
    """Re-encode smaller if file exceeds Telegram limit."""
    size = path.stat().st_size
    if size <= max_bytes:
        return path

    out = path.with_name(path.stem + "_tg.mp4")
    # Ước lượng bitrate
    duration = get_duration(path) or 60.0
    # chừa ~10% cho container/audio
    target_bits = int((max_bytes * 8 * 0.85) / max(duration, 1))
    video_bitrate = max(target_bits - 128_000, 300_000)

    cmd = [
        FFMPEG,
        "-y",
        "-i",
        str(path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        str(video_bitrate),
        "-maxrate",
        str(int(video_bitrate * 1.2)),
        "-bufsize",
        str(int(video_bitrate * 2)),
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        str(out),
    ]
    try:
        _run_ffmpeg(cmd)
        if out.exists() and out.stat().st_size <= max_bytes:
            return out
        # Vẫn quá lớn — nén mạnh hơn
        cmd2 = cmd.copy()
        idx = cmd2.index("-b:v")
        cmd2[idx + 1] = str(max(video_bitrate // 2, 200_000))
        _run_ffmpeg(cmd2)
        if out.exists():
            return out
    except Exception as e:
        logger.warning("compress failed: %s", e)
    return path


async def split_video_async(path: Path, segment_seconds: int) -> list[Path]:
    return await asyncio.to_thread(split_video, path, segment_seconds)


async def compress_if_needed_async(path: Path) -> Path:
    return await asyncio.to_thread(compress_if_needed, path)
