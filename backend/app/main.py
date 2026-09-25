"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.routes import router
from app.config import get_settings
from app.db import create_all, session_scope
from app.logging_setup import configure_logging, get_logger
from app.models import JobStatus, ProcessingJob

log = get_logger("app")


def _resume_interrupted_jobs() -> None:
    """Inline queue only: jobs interrupted by a restart continue from their persisted rows."""
    from app.workers.queue import enqueue_job

    with session_scope() as s:
        ids = [
            j.id
            for j in s.execute(
                select(ProcessingJob).where(
                    ProcessingJob.status.in_([JobStatus.PROCESSING.value, JobStatus.QUEUED.value])
                )
            ).scalars()
        ]
        for job_id in ids:
            job = s.get(ProcessingJob, job_id)
            assert job is not None
            job.status = JobStatus.QUEUED.value
    for job_id in ids:
        log.info("resuming_job", job_id=job_id)
        enqueue_job(job_id)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    create_all()  # idempotent; production deployments run `alembic upgrade head` first
    if not settings.twitch_configured or not settings.kick_configured:
        log.warning(
            "platform_credentials_missing", twitch=settings.twitch_configured, kick=settings.kick_configured
        )
    if settings.queue_backend == "inline":
        _resume_interrupted_jobs()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Cross-Platform Streamer Identity Matcher", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    app.include_router(router)
    return app


app = create_app()
