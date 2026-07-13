"""Download job control: pause / resume / cancel."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class JobCancelled(Exception):
    """Raised when a job is cancelled by the user."""


@dataclass
class DownloadJob:
    job_id: str
    user_id: int
    chat_id: int
    url: str
    segment_seconds: int
    status: JobStatus = JobStatus.RUNNING
    created_at: float = field(default_factory=time.time)
    message: str = ""
    current: int = 0
    total: int = 0
    videos_sent: int = 0
    images_sent: int = 0
    # Sync pause flag (works from download threads)
    _pause_event: threading.Event = field(default_factory=threading.Event)
    _cancel_flag: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    # Optional: kill ffmpeg mid-download
    active_proc: Any = None

    def __post_init__(self) -> None:
        self._pause_event.set()  # running by default

    def pause(self) -> bool:
        with self._lock:
            if self.status in (JobStatus.CANCELLED, JobStatus.COMPLETED, JobStatus.FAILED):
                return False
            self.status = JobStatus.PAUSED
            self._pause_event.clear()
            return True

    def resume(self) -> bool:
        with self._lock:
            if self.status != JobStatus.PAUSED:
                return False
            self.status = JobStatus.RUNNING
            self._pause_event.set()
            return True

    def cancel(self) -> bool:
        with self._lock:
            if self.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
                return False
            self.status = JobStatus.CANCELLED
            self._cancel_flag.set()
            self._pause_event.set()  # unblock waiters
            proc = self.active_proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        return True

    def is_cancelled(self) -> bool:
        return self._cancel_flag.is_set() or self.status == JobStatus.CANCELLED

    def is_paused(self) -> bool:
        return self.status == JobStatus.PAUSED

    def wait_if_paused(self, poll: float = 0.25) -> None:
        """Block while paused; raise JobCancelled if cancelled."""
        while True:
            if self.is_cancelled():
                raise JobCancelled("Đã dừng tải theo yêu cầu.")
            if self._pause_event.wait(timeout=poll):
                if self.is_cancelled():
                    raise JobCancelled("Đã dừng tải theo yêu cầu.")
                return

    async def checkpoint(self) -> None:
        """Async checkpoint for pause/cancel between steps."""
        while True:
            if self.is_cancelled():
                raise JobCancelled("Đã dừng tải theo yêu cầu.")
            if self.status != JobStatus.PAUSED:
                return
            await asyncio.sleep(0.3)

    def set_progress(self, current: int, total: int, message: str = "") -> None:
        self.current = current
        self.total = total
        if message:
            self.message = message

    def mark_completed(self) -> None:
        with self._lock:
            if self.status not in (JobStatus.CANCELLED, JobStatus.FAILED):
                self.status = JobStatus.COMPLETED

    def mark_failed(self) -> None:
        with self._lock:
            if self.status != JobStatus.CANCELLED:
                self.status = JobStatus.FAILED


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, DownloadJob] = {}
        self._by_user: dict[int, str] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        user_id: int,
        chat_id: int,
        url: str,
        segment_seconds: int,
    ) -> DownloadJob:
        # Cancel previous active job for same user
        prev = self.get_user_job(user_id)
        if prev and prev.status in (JobStatus.RUNNING, JobStatus.PAUSED):
            prev.cancel()

        job = DownloadJob(
            job_id=uuid.uuid4().hex[:10],
            user_id=user_id,
            chat_id=chat_id,
            url=url,
            segment_seconds=segment_seconds,
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._by_user[user_id] = job.job_id
        return job

    def get(self, job_id: str) -> DownloadJob | None:
        return self._jobs.get(job_id)

    def get_user_job(self, user_id: int) -> DownloadJob | None:
        jid = self._by_user.get(user_id)
        if not jid:
            return None
        return self._jobs.get(jid)

    def cleanup_old(self, max_age: float = 3600) -> None:
        now = time.time()
        with self._lock:
            dead = [
                jid
                for jid, j in self._jobs.items()
                if now - j.created_at > max_age
                and j.status in (JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED)
            ]
            for jid in dead:
                job = self._jobs.pop(jid, None)
                if job and self._by_user.get(job.user_id) == jid:
                    self._by_user.pop(job.user_id, None)


jobs = JobManager()
