"""ReviewService: human confirm/reject of REVIEW rows.

Verdicts are stored as explicit, versioned overrides (manual_reviews) and re-applied
through the same state machine. They never change weights or thresholds.
Only accounts that the engine actually discovered can be confirmed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import JobStatus, ManualReview, ProcessingJob, ProcessingRow, RowStatus
from app.services.jobs import (
    apply_resolution_to_row,
    best_candidate,
    manual_verdicts,
    recompute_counters,
    verdict_key,
)
from app.version import MATCHING_ENGINE_VERSION

VERDICTS = {"CONFIRM": "CONFIRMED", "REJECT": "REJECTED"}


class ReviewError(ValueError):
    pass


def review_items(session: Session, job: ProcessingJob, include_decided: bool = False) -> list[dict[str, Any]]:
    rows = session.execute(
        select(ProcessingRow)
        .where(ProcessingRow.job_id == job.id, ProcessingRow.decision == "REVIEW")
        .order_by(ProcessingRow.original_row)
    ).scalars()
    items = []
    for r in rows:
        if r.manual_verdict and not include_decided:
            continue
        if not RowStatus(r.status).is_final:
            continue
        ev = r.evidence_json or {}
        items.append(
            {
                "original_row": r.original_row,
                "source_platform": job.source_platform,
                "source_value": r.source_value,
                "country": r.country,
                "status": r.status,
                "source_status": r.source_status,
                "confidence": r.confidence,
                "reason": r.reason,
                "manual_verdict": r.manual_verdict,
                "source_profile": ev.get("source_profile"),
                "source_socials": ev.get("source_socials", []),
                "candidate": best_candidate(r),
                "other_candidates": [
                    c for c in (ev.get("candidates") or []) if c.get("decision") in ("MATCH", "REVIEW")
                ][:5],
            }
        )
    return items


def submit_verdict(
    session: Session,
    settings: Settings,
    job: ProcessingJob,
    original_row: int,
    verdict: str,
    target_username: str | None,
    note: str | None = None,
) -> dict[str, Any]:
    if job.status not in (JobStatus.COMPLETED.value, JobStatus.PARTIAL.value):
        raise ReviewError("Reviews can be submitted once processing has finished.")
    row = session.execute(
        select(ProcessingRow).where(
            ProcessingRow.job_id == job.id, ProcessingRow.original_row == original_row
        )
    ).scalar_one_or_none()
    if row is None:
        raise ReviewError(f"row {original_row} not found")
    if verdict == "SKIP":
        return {"original_row": original_row, "verdict": "SKIP"}
    if verdict not in VERDICTS:
        raise ReviewError("verdict must be CONFIRM, REJECT or SKIP")
    ev = row.evidence_json or {}
    candidates = {c["username"]: c for c in ev.get("candidates") or []}
    target = (target_username or row.review_candidate or "").lower()
    if target not in candidates:
        raise ReviewError("Only a candidate account discovered by the engine can be confirmed or rejected.")
    source_platform = str(job.source_platform)
    key = verdict_key(row)
    target_platform = candidates[target]["platform"]
    mr = session.execute(
        select(ManualReview).where(
            ManualReview.source_platform == source_platform,
            ManualReview.source_key == key,
            ManualReview.target_platform == target_platform,
            ManualReview.target_key == target,
        )
    ).scalar_one_or_none()
    if mr is None:
        mr = ManualReview(
            source_platform=source_platform,
            source_key=key,
            target_platform=target_platform,
            target_key=target,
            matching_engine_version=MATCHING_ENGINE_VERSION,
        )
        session.add(mr)
    mr.verdict = VERDICTS[verdict]
    mr.note = note
    mr.job_id = job.id
    mr.target_display = candidates[target].get("display_name")
    session.flush()

    all_verdicts = manual_verdicts(session, source_platform)
    affected = session.execute(select(ProcessingRow).where(ProcessingRow.job_id == job.id)).scalars()
    updated = []
    for r in affected:
        if verdict_key(r) != key or not r.evidence_json or not RowStatus(r.status).is_final:
            continue
        if r.status == RowStatus.PRESERVED.value or r.status == RowStatus.SKIPPED_EMPTY.value:
            continue
        resolution = {k: v for k, v in r.evidence_json.items() if k != "state_case"}
        apply_resolution_to_row(
            r, source_platform, resolution, all_verdicts.get(key, {}), settings.existing_destination_policy
        )
        updated.append(r.original_row)
    job.export_stale = True
    recompute_counters(session, job)
    session.commit()
    return {
        "original_row": original_row,
        "verdict": VERDICTS[verdict],
        "target": target,
        "rows_updated": updated,
    }
