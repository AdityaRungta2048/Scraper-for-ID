"""HTTP API. Long-running work never happens inside a request: jobs are queued."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.schemas import JobOut, ReviewSubmit, RowDetail, RowOut, RowPage, StartRequest
from app.config import Settings, get_settings
from app.db import get_db
from app.excel.exporter import output_filename
from app.excel.importer import SUPPORTED_SUFFIXES, WorkbookValidationError
from app.excel.review_report import review_filename
from app.models import JobStatus, ProcessingJob, ProcessingRow, RowStatus
from app.models.tables import ERROR_STATUSES
from app.reviews.service import ReviewError, review_items, submit_verdict
from app.services.jobs import build_export, create_job, recompute_counters, start_job
from app.version import MATCHING_ENGINE_VERSION
from app.workers.processor import is_stale
from app.workers.queue import enqueue_job

router = APIRouter(prefix="/api")
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _job_or_404(db: Session, job_id: str) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


def _job_out(job: ProcessingJob) -> JobOut:
    out = JobOut.model_validate(job)
    out.output_available = bool(job.output_path and Path(job.output_path).exists()) or (
        job.status in (JobStatus.COMPLETED.value, JobStatus.PARTIAL.value) and job.export_stale
    )
    out.output_filename = output_filename(job.filename)
    out.review_filename = review_filename(job.filename)
    out.needs_platform_choice = job.status == JobStatus.UPLOADED.value and job.detected_platform is None
    return out


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "matching_engine_version": MATCHING_ENGINE_VERSION}


@router.get("/config")
def config(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Non-secret configuration summary for the UI."""
    return {
        "twitch_configured": settings.twitch_configured,
        "kick_configured": settings.kick_configured,
        "search_engine_fallback": settings.search_engine_configured,
        "kick_public_profile_enrichment": settings.kick_public_profile_enrichment,
        "match_threshold": settings.match_threshold,
        "review_threshold": settings.review_threshold,
        "max_candidates": settings.max_candidates,
        "existing_destination_policy": settings.existing_destination_policy,
        "queue_backend": settings.queue_backend,
        "matching_engine_version": MATCHING_ENGINE_VERSION,
    }


@router.post("/jobs", response_model=JobOut, status_code=201)
async def upload(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobOut:
    name = file.filename or "upload.xlsx"
    if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES | {".xls"}:
        raise HTTPException(400, "Please upload an Excel workbook (.xlsx or .xlsm).")
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {settings.max_upload_mb} MB).")
    if not content:
        raise HTTPException(400, "The uploaded file is empty.")
    try:
        job, _ = create_job(db, settings, name, content)
    except WorkbookValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _job_out(job)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db), limit: int = Query(50, le=200)) -> list[JobOut]:
    jobs = db.execute(select(ProcessingJob).order_by(ProcessingJob.created_at.desc()).limit(limit)).scalars()
    return [_job_out(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobOut:
    return _job_out(_job_or_404(db, job_id))


@router.post("/jobs/{job_id}/start", response_model=JobOut)
def start(
    job_id: str, body: StartRequest, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> JobOut:
    job = _job_or_404(db, job_id)
    try:
        start_job(db, job, body.source_platform)
    except (ValueError, WorkbookValidationError) as exc:
        raise HTTPException(409, str(exc)) from exc
    enqueue_job(job.id, settings)
    return _job_out(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: str, db: Session = Depends(get_db)) -> JobOut:
    job = _job_or_404(db, job_id)
    if job.status in (JobStatus.QUEUED.value, JobStatus.PROCESSING.value, JobStatus.UPLOADED.value):
        job.status = JobStatus.CANCELLED.value
        db.commit()
    return _job_out(job)


@router.post("/jobs/{job_id}/resume", response_model=JobOut)
def resume(job_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> JobOut:
    """Continue from the last persisted row (after a crash, cancellation or failure)."""
    job = _job_or_404(db, job_id)
    if job.status == JobStatus.PROCESSING.value and not is_stale(job):
        raise HTTPException(409, "Job is currently processing.")
    if job.status in (JobStatus.UPLOADED.value, JobStatus.COMPLETED.value):
        raise HTTPException(409, f"Job is {job.status}; nothing to resume.")
    job.status = JobStatus.QUEUED.value
    job.error_message = None
    db.commit()
    enqueue_job(job.id, settings)
    return _job_out(job)


@router.post("/jobs/{job_id}/retry-failed", response_model=JobOut)
def retry_failed(
    job_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> JobOut:
    job = _job_or_404(db, job_id)
    if job.status not in (
        JobStatus.PARTIAL.value,
        JobStatus.FAILED.value,
        JobStatus.COMPLETED.value,
        JobStatus.CANCELLED.value,
    ):
        raise HTTPException(409, f"Job is {job.status}; wait for it to finish first.")
    res = db.execute(
        update(ProcessingRow)
        .where(ProcessingRow.job_id == job.id, ProcessingRow.status.in_([s.value for s in ERROR_STATUSES]))
        .values(status=RowStatus.PENDING.value, error_message=None)
    )
    if not res.rowcount and job.status != JobStatus.FAILED.value:  # type: ignore[attr-defined]
        raise HTTPException(409, "There are no failed rows to retry.")
    job.status = JobStatus.QUEUED.value
    job.error_message = None
    job.export_stale = True
    recompute_counters(db, job)
    db.commit()
    enqueue_job(job.id, settings)
    return _job_out(job)


@router.get("/jobs/{job_id}/rows", response_model=RowPage)
def rows(
    job_id: str,
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    decision: str | None = None,
    status: str | None = None,
) -> RowPage:
    _job_or_404(db, job_id)
    q = select(ProcessingRow).where(ProcessingRow.job_id == job_id)
    if decision:
        q = q.where(ProcessingRow.decision == decision)
    if status == "ERROR":
        q = q.where(ProcessingRow.status.in_([s.value for s in ERROR_STATUSES]))
    elif status:
        q = q.where(ProcessingRow.status == status)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    items = db.execute(q.order_by(ProcessingRow.original_row).offset(offset).limit(limit)).scalars()
    return RowPage(total=total, offset=offset, limit=limit, items=[RowOut.model_validate(r) for r in items])


@router.get("/jobs/{job_id}/rows/{original_row}", response_model=RowDetail)
def row_detail(job_id: str, original_row: int, db: Session = Depends(get_db)) -> RowDetail:
    row = db.execute(
        select(ProcessingRow).where(
            ProcessingRow.job_id == job_id, ProcessingRow.original_row == original_row
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "row not found")
    return RowDetail.model_validate(row)


def _ensure_export(db: Session, settings: Settings, job: ProcessingJob) -> None:
    if job.status not in (JobStatus.COMPLETED.value, JobStatus.PARTIAL.value):
        raise HTTPException(409, f"The processed workbook is not available (job is {job.status}).")
    cols = job.columns_json or {}
    missing_link_columns = cols.get("twitch_link") is None or cols.get("kick_link") is None
    if job.export_stale or missing_link_columns or not job.output_path or not Path(job.output_path).exists():
        report = build_export(db, settings, job)
        if not report["ok"]:
            raise HTTPException(500, job.error_message or "Output verification failed.")


@router.get("/jobs/{job_id}/download")
def download(
    job_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> FileResponse:
    job = _job_or_404(db, job_id)
    _ensure_export(db, settings, job)
    assert job.output_path
    media = (
        XLSX_MEDIA if job.output_path.endswith(".xlsx") else "application/vnd.ms-excel.sheet.macroEnabled.12"
    )
    return FileResponse(job.output_path, media_type=media, filename=output_filename(job.filename))


@router.get("/jobs/{job_id}/review-report")
def review_report(
    job_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> FileResponse:
    job = _job_or_404(db, job_id)
    _ensure_export(db, settings, job)
    if not job.review_path or not Path(job.review_path).exists():
        raise HTTPException(404, "review report not available")
    return FileResponse(job.review_path, media_type=XLSX_MEDIA, filename=review_filename(job.filename))


@router.get("/jobs/{job_id}/reviews")
def list_reviews(
    job_id: str, include_decided: bool = False, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    job = _job_or_404(db, job_id)
    return review_items(db, job, include_decided)


@router.post("/jobs/{job_id}/reviews")
def post_review(
    job_id: str, body: ReviewSubmit, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    job = _job_or_404(db, job_id)
    try:
        return submit_verdict(
            db, settings, job, body.original_row, body.verdict, body.target_username, body.note
        )
    except ReviewError as exc:
        raise HTTPException(400, str(exc)) from exc
