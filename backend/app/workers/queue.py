"""Queue abstraction: Redis + RQ in production, an in-process thread runner for local dev/tests."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor

from app.config import Settings, get_settings
from app.logging_setup import get_logger

log = get_logger("queue")

_executor: ThreadPoolExecutor | None = None
_running: dict[str, Future[str]] = {}
_lock = threading.Lock()


def enqueue_job(job_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if settings.queue_backend == "rq":
        from redis import Redis
        from rq import Queue

        q = Queue(settings.rq_queue_name, connection=Redis.from_url(settings.redis_url))
        q.enqueue("app.workers.processor.run_job_sync", job_id, job_timeout=24 * 3600, result_ttl=86400)
        log.info("job_enqueued", job_id=job_id, backend="rq")
        return
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
        fut = _running.get(job_id)
        if fut is not None and not fut.done():
            return
        from app.workers.processor import run_job_sync

        _running[job_id] = _executor.submit(run_job_sync, job_id)
    log.info("job_enqueued", job_id=job_id, backend="inline")


def wait_inline(job_id: str, timeout: float | None = None) -> str | None:
    fut = _running.get(job_id)
    return fut.result(timeout=timeout) if fut else None
