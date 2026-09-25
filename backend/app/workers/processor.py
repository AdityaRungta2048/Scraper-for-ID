"""JobProcessor.

* resolves each UNIQUE source id once (duplicates reuse the result),
* runs resolutions concurrently (bounded), in any completion order,
* writes every result back to the rows that reference it BY ``original_row``,
* persists each row as soon as it is resolved (resumable after a crash),
* never converts API failures into "not found"/"no match" (row -> *_ERROR, retryable),
* then exports + verifies the workbook.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.cache.service import as_utc
from app.config import Settings, get_settings
from app.db import session_factory as default_session_factory
from app.excel.state_machine import empty_source_outcome
from app.logging_setup import get_logger
from app.models import JobStatus, MatchCandidate, MatchDecision, ProcessingJob, ProcessingRow, RowStatus
from app.platforms.errors import ConfigurationError, PlatformError
from app.services.context import MatchingContext, build_context
from app.services.jobs import apply_resolution_to_row, build_export, manual_verdicts, recompute_counters

log = get_logger("processor")

ContextFactory = Callable[[Settings, sessionmaker[Session]], MatchingContext]
STALE_AFTER = timedelta(minutes=10)


def claim_job(sf: sessionmaker[Session], job_id: str) -> bool:
    """Atomically move QUEUED -> PROCESSING so two workers never run the same job."""
    with sf() as s:
        res = s.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id, ProcessingJob.status == JobStatus.QUEUED.value)
            .values(status=JobStatus.PROCESSING.value, updated_at=datetime.now(UTC))
        )
        s.commit()
        return bool(res.rowcount)  # type: ignore[attr-defined]


def is_stale(job: ProcessingJob) -> bool:
    return (
        job.status == JobStatus.PROCESSING.value and as_utc(job.updated_at) < datetime.now(UTC) - STALE_AFTER
    )


async def process_job(
    job_id: str,
    *,
    settings: Settings | None = None,
    sf: sessionmaker[Session] | None = None,
    context_factory: ContextFactory | None = None,
    on_group_done: Callable[[str], Any] | None = None,
) -> str:
    settings = settings or get_settings()
    sf = sf or default_session_factory()
    if not claim_job(sf, job_id):
        log.info("job_not_claimed", job_id=job_id)
        with sf() as s:
            job = s.get(ProcessingJob, job_id)
            return job.status if job else "MISSING"

    with sf() as s:
        job = s.get(ProcessingJob, job_id)
        assert job is not None
        if job.started_at is None:
            job.started_at = datetime.now(UTC)
        platform = str(job.source_platform)
        pending = list(
            s.execute(
                select(
                    ProcessingRow.id,
                    ProcessingRow.original_row,
                    ProcessingRow.source_value,
                    ProcessingRow.source_key,
                    ProcessingRow.country,
                )
                .where(
                    ProcessingRow.job_id == job_id,
                    ProcessingRow.status.in_([RowStatus.PENDING.value, RowStatus.SOURCE_EXISTS.value]),
                )
                .order_by(ProcessingRow.original_row)
            ).all()
        )
        verdicts = manual_verdicts(s, platform)
        s.commit()

    # Group rows by the identity that will be resolved (duplicates resolved once).
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    first_value: dict[tuple[str, str], tuple[str, str | None]] = {}
    empty_rows: list[int] = []
    for row_id, _orig, value, key, country in pending:
        if value is None or not str(value).strip():
            empty_rows.append(row_id)
            continue
        gk = (key or f"invalid:{str(value).strip().lower()}", (country or "").strip().lower())
        groups[gk].append(row_id)
        first_value.setdefault(gk, (str(value), country))

    with sf() as s:
        for row_id in empty_rows:
            row = s.get(ProcessingRow, row_id)
            assert row is not None
            out = empty_source_outcome()
            row.status, row.reason = out.status.value, "Source ID cell is empty; row left unchanged."
            row.write_destination = row.write_remarks = False
        s.commit()

    log.info(
        "job_started",
        job_id=job_id,
        source_platform=platform,
        pending_rows=len(pending),
        unique_ids=len(groups),
    )
    ctx = (context_factory or build_context)(settings, sf)
    fatal: list[str] = []
    cancelled = False
    sem = asyncio.Semaphore(max(1, settings.row_concurrency))
    db_lock = asyncio.Lock()
    last_counts = [0.0]

    async def run_group(gk: tuple[str, str], row_ids: list[int]) -> None:
        nonlocal cancelled
        if fatal or cancelled:
            return
        async with sem:
            if fatal or cancelled:
                return
            with sf() as s:
                j = s.get(ProcessingJob, job_id)
                if j is None or j.status == JobStatus.CANCELLED.value:
                    cancelled = True
                    return
            value, country = first_value[gk]
            started = datetime.now(UTC)
            resolution: dict[str, Any] | None = None
            error: PlatformError | Exception | None = None
            try:
                resolution = await ctx.resolver.resolve(platform, value, country)
            except ConfigurationError as exc:
                fatal.append(str(exc))
                error = exc
            except PlatformError as exc:
                error = exc
            except Exception as exc:
                log.exception("processing_error", job_id=job_id, source=value)
                error = exc
            async with db_lock:
                with sf() as s:
                    for row_id in row_ids:
                        row = s.get(ProcessingRow, row_id)
                        assert row is not None
                        row.attempts += 1
                        if resolution is not None:
                            try:
                                apply_resolution_to_row(
                                    row,
                                    platform,
                                    resolution,
                                    verdicts.get(gk[0], {}),
                                    settings.existing_destination_policy,
                                )
                            except Exception as exc:
                                row.status = RowStatus.PROCESSING_ERROR.value
                                row.error_message = f"state machine: {exc}"
                        else:
                            status = (
                                RowStatus(error.row_status)
                                if isinstance(error, PlatformError)
                                else RowStatus.PROCESSING_ERROR
                            )
                            row.status = status.value
                            row.error_message = f"{type(error).__name__}: {error}"
                            row.write_destination = row.write_remarks = False
                    if resolution is not None and not resolution.get("from_cache"):
                        _persist_decision(s, job_id, resolution)
                    now = asyncio.get_running_loop().time()
                    if now - last_counts[0] >= 1.0:  # progress counters, throttled for big jobs
                        last_counts[0] = now
                        j = s.get(ProcessingJob, job_id)
                        assert j is not None
                        recompute_counters(s, j)
                    s.commit()
            log.info(
                "identity_resolved",
                job_id=job_id,
                source_platform=platform,
                source_id=value,
                rows=len(row_ids),
                candidate_count=(resolution or {}).get("candidate_count"),
                decision=(resolution or {}).get("decision"),
                confidence=(resolution or {}).get("confidence"),
                error=None if error is None else type(error).__name__,
                duration_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
            )
            if on_group_done is not None:
                on_group_done(value)

    try:
        await asyncio.gather(*(run_group(gk, ids) for gk, ids in groups.items()))
    finally:
        await ctx.aclose()

    with sf() as s:
        job = s.get(ProcessingJob, job_id)
        assert job is not None
        recompute_counters(s, job)
        if fatal:
            job.status = JobStatus.FAILED.value
            job.error_message = f"Configuration error: {fatal[0]}"
            s.commit()
            log.error("job_failed_configuration", job_id=job_id, error=fatal[0])
            return job.status
        if cancelled or job.status == JobStatus.CANCELLED.value:
            job.status = JobStatus.CANCELLED.value
            job.completed_at = datetime.now(UTC)
            s.commit()
            return job.status
        job.status = JobStatus.PARTIAL.value if job.error_count else JobStatus.COMPLETED.value
        job.error_message = (
            f"{job.error_count} row(s) hit temporary/API errors and were left unchanged; "
            "use 'Retry failed rows'."
            if job.error_count
            else None
        )
        job.completed_at = datetime.now(UTC)
        s.commit()
        build_export(s, settings, job)
        log.info(
            "job_finished",
            job_id=job_id,
            status=job.status,
            matches=job.match_count,
            reviews=job.review_count,
            errors=job.error_count,
        )
        return job.status


def _persist_decision(s: Session, job_id: str, res: dict[str, Any]) -> None:
    src = res.get("source_profile") or {}
    for c in res.get("candidates", [])[:10]:
        s.add(
            MatchCandidate(
                job_id=job_id,
                source_platform=res["source_platform"],
                source_key=res.get("source_key") or "",
                target_platform=res["target_platform"],
                target_key=c["username"],
                discovered_via=c.get("discovered_via"),
                decision=c.get("decision"),
                confidence=c.get("confidence"),
                evidence_json={
                    "evidence": c.get("evidence"),
                    "gates": c.get("gates"),
                    "signals": c.get("signals"),
                },
                matching_engine_version=res["engine_version"],
            )
        )
    best = next(
        (
            c
            for c in res.get("candidates", [])
            if c["username"] in {res.get("matched_username"), res.get("review_candidate")}
        ),
        None,
    )
    s.add(
        MatchDecision(
            job_id=job_id,
            source_platform=res["source_platform"],
            source_key=res.get("source_key") or "",
            source_account_id=src.get("user_id"),
            target_platform=res["target_platform"],
            target_key=best["username"] if best else None,
            target_account_id=best.get("user_id") if best else None,
            decision=res["decision"],
            confidence=res.get("confidence"),
            reason=res.get("reason"),
            evidence_json={
                "evidence": best.get("evidence") if best else None,
                "source_status": res.get("source_status"),
                "target_status": res.get("target_status"),
            },
            matching_engine_version=res["engine_version"],
        )
    )


def run_job_sync(job_id: str) -> str:
    """Entry point for RQ workers / threads."""
    return asyncio.run(process_job(job_id))
