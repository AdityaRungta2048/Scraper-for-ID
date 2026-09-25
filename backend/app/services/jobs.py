"""Job lifecycle: create from upload, import rows, counters, export + verification."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.excel.exporter import CellWrite, ExportError, export_processed, output_filename
from app.excel.importer import ColumnMap, WorkbookAnalysis, analyze_workbook, read_rows, upgrade_columns
from app.excel.review_report import review_filename, write_review_report
from app.excel.state_machine import StateMachineError, transition
from app.excel.verifier import verify_output
from app.logging_setup import get_logger
from app.matching.normalize import normalize_username
from app.models import JobStatus, ManualReview, ProcessingJob, ProcessingRow, RowStatus
from app.models.tables import ERROR_STATUSES, new_id
from app.platforms.base import PlatformAdapter
from app.platforms.kick import KickAdapter
from app.platforms.twitch import TwitchAdapter
from app.services.resolver import apply_manual_reviews
from app.version import MATCHING_ENGINE_VERSION

log = get_logger("jobs")
# Bump when the processed-workbook layout changes: existing jobs are re-exported on download.
EXPORT_FORMAT_VERSION = 4
NORMALIZERS: dict[str, type[PlatformAdapter]] = {"twitch": TwitchAdapter, "kick": KickAdapter}


def safe_filename(name: str) -> str:
    base = Path(name or "upload.xlsx").name
    base = re.sub(r"[^\w.\- ()]+", "_", base).strip() or "upload.xlsx"
    return base[:180]


def create_job(
    session: Session, settings: Settings, filename: str, content: bytes
) -> tuple[ProcessingJob, WorkbookAnalysis]:
    job_id = new_id()
    fname = safe_filename(filename)
    folder = settings.uploads_dir / job_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / fname
    path.write_bytes(content)
    try:
        analysis = analyze_workbook(path)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    job = ProcessingJob(
        id=job_id,
        filename=fname,
        stored_path=str(path),
        detected_platform=analysis.detected_platform,
        detection_note=analysis.detection_reason,
        sheet_name=analysis.sheet_name,
        header_row=analysis.header_row,
        columns_json=analysis.columns.to_dict(),
        total_rows=analysis.data_rows,
        status=JobStatus.UPLOADED.value,
        warnings_json=analysis.warnings,
        matching_engine_version=MATCHING_ENGINE_VERSION,
    )
    session.add(job)
    session.commit()
    return job, analysis


def start_job(session: Session, job: ProcessingJob, source_platform: str | None) -> ProcessingJob:
    if job.status != JobStatus.UPLOADED.value:
        raise ValueError(f"job is {job.status}; only UPLOADED jobs can be started")
    platform = source_platform or job.detected_platform
    if platform not in ("kick", "twitch"):
        raise ValueError("The source platform could not be determined; please choose Kick or Twitch.")
    columns = ColumnMap.from_dict(job.columns_json or {})
    rows = read_rows(Path(job.stored_path), columns, platform)
    normalizer = NORMALIZERS[platform]
    for r in rows:
        session.add(
            ProcessingRow(
                job_id=job.id,
                original_row=r.original_row,
                source_value=r.source_value,
                source_key=normalizer.normalize_handle(r.source_value) if r.source_value else None,
                country=r.country,
                existing_destination=r.existing_destination,
                existing_remarks=r.existing_remarks,
                status=RowStatus.PENDING.value,
            )
        )
    job.source_platform = platform
    job.total_rows = len(rows)
    job.status = JobStatus.QUEUED.value
    job.export_stale = True
    session.commit()
    return job


def recompute_counters(session: Session, job: ProcessingJob) -> None:
    rows = session.execute(
        select(ProcessingRow.status, ProcessingRow.decision, ProcessingRow.manual_verdict).where(
            ProcessingRow.job_id == job.id
        )
    ).all()
    c = dict.fromkeys(("processed", "match", "no_match", "review", "not_found", "error", "skipped"), 0)
    for status, decision, verdict in rows:
        st = RowStatus(status)
        if st in (RowStatus.PENDING, RowStatus.SOURCE_EXISTS):
            continue
        c["processed"] += 1
        if st in ERROR_STATUSES:
            c["error"] += 1
        elif st in (RowStatus.SKIPPED_EMPTY, RowStatus.PRESERVED):
            c["skipped"] += 1
        elif st == RowStatus.MATCH:
            c["match"] += 1
        elif decision == "REVIEW" and not verdict:
            c["review"] += 1
        else:
            c["no_match"] += 1
        if st == RowStatus.SOURCE_NOT_FOUND:
            c["not_found"] += 1
    job.total_rows = len(rows)
    job.processed_rows = c["processed"]
    job.match_count = c["match"]
    job.no_match_count = c["no_match"]
    job.review_count = c["review"]
    job.not_found_count = c["not_found"]
    job.error_count = c["error"]
    job.skipped_count = c["skipped"]
    job.updated_at = datetime.now(UTC)


def manual_verdicts(session: Session, source_platform: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for mr in session.execute(
        select(ManualReview).where(ManualReview.source_platform == source_platform)
    ).scalars():
        out.setdefault(mr.source_key, {})[mr.target_key] = mr.verdict
    return out


def apply_resolution_to_row(
    row: ProcessingRow,
    source_platform: str,
    resolution: dict[str, Any],
    verdicts: dict[str, str],
    policy: str,
) -> None:
    """Resolution (+ manual verdicts) -> state machine -> row output fields."""
    final = apply_manual_reviews(resolution, verdicts)
    outcome = transition(source_platform, final, row.existing_destination, row.existing_remarks, policy)
    row.status = outcome.status.value
    row.source_status = final.get("source_status")
    row.target_status = final.get("target_status")
    row.decision = final.get("decision")
    row.confidence = final.get("confidence")
    row.matched_id = final.get("matched_id")
    row.review_candidate = final.get("review_candidate")
    row.output_destination = outcome.destination
    row.output_remarks = outcome.remarks
    row.write_destination = outcome.write_destination
    row.write_remarks = outcome.write_remarks
    row.reason = final.get("reason")
    row.evidence_json = {**resolution, "state_case": outcome.case}
    row.error_message = None
    row.manual_verdict = (
        next(
            (
                verdicts[u]
                for u in (final.get("matched_username"), final.get("review_candidate"))
                if u and u in verdicts
            ),
            None,
        )
        if final.get("manual")
        else None
    )
    row.matching_engine_version = resolution.get("engine_version", MATCHING_ENGINE_VERSION)


def build_export(session: Session, settings: Settings, job: ProcessingJob) -> dict[str, Any]:
    """Generate <name>_processed.xlsx from the ORIGINAL upload, verify it, and the review report.

    If verification fails, the output file is removed and the job is marked FAILED —
    an unverified workbook is never offered for download.
    """
    original = Path(job.stored_path)
    columns = ColumnMap.from_dict(job.columns_json or {})
    if columns.twitch_link is None or columns.kick_link is None:  # job created before link columns
        columns = upgrade_columns(columns, original)
        job.columns_json = columns.to_dict()
    platform = job.source_platform or ""
    rows = list(
        session.execute(
            select(ProcessingRow).where(ProcessingRow.job_id == job.id).order_by(ProcessingRow.original_row)
        ).scalars()
    )
    verdicts = manual_verdicts(session, platform)
    # Re-derive every resolved row with the current output rules (e.g. remark wording) from its
    # stored evidence — no platform calls — so re-downloads of older jobs follow the same rules.
    for r in rows:
        if r.evidence_json and RowStatus(r.status).is_final and r.status != RowStatus.SKIPPED_EMPTY.value:
            resolution = {k: v for k, v in r.evidence_json.items() if k != "state_case"}
            try:
                apply_resolution_to_row(
                    r,
                    platform,
                    resolution,
                    verdicts.get(verdict_key(r), {}),
                    settings.existing_destination_policy,
                )
            except StateMachineError:  # incomplete stored evidence: keep the stored outputs
                log.warning("row_rederive_skipped", job_id=job.id, original_row=r.original_row)
    recompute_counters(session, job)
    writes = []
    for r in rows:
        if not RowStatus(r.status).is_final:
            continue
        links = channel_links(r, platform, verdicts.get(verdict_key(r), {}))
        if r.write_destination or r.write_remarks or links is not None:
            writes.append(
                CellWrite(
                    r.original_row,
                    r.write_destination,
                    r.output_destination,
                    r.write_remarks,
                    r.output_remarks,
                    links,
                )
            )
    out_dir = settings.outputs_dir / job.id
    out_path = out_dir / output_filename(job.filename)
    try:
        expected = export_processed(original, out_path, columns, platform, writes)
        report = verify_output(
            original, out_path, columns, platform, expected, [(r.original_row, r.source_value) for r in rows]
        ).to_dict()
    except (ExportError, OSError, ValueError, KeyError) as exc:
        report = {
            "ok": False,
            "checks": [{"name": "export", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}],
            "mismatches": [],
        }
    report["export_format"] = EXPORT_FORMAT_VERSION
    job.verification_json = report
    if not report["ok"]:
        if out_path.exists():
            out_path.unlink()
        job.output_path = None
        job.status = JobStatus.FAILED.value
        job.error_message = (
            "Output verification failed; the processed workbook was not released. "
            + "; ".join(f"{c['name']}: {c['detail']}" for c in report["checks"] if not c["ok"])[:2000]
        )
        log.error(
            "export_verification_failed", job_id=job.id, failures=[c for c in report["checks"] if not c["ok"]]
        )
    else:
        job.output_path = str(out_path)
        job.export_stale = False
        review_path = out_dir / review_filename(job.filename)
        write_review_report(review_path, _job_dict(job), [_row_report_dict(r) for r in rows])
        job.review_path = str(review_path)
        log.info("export_verified", job_id=job.id, writes=len(writes), output=out_path.name)
    session.commit()
    return report


def _job_dict(job: ProcessingJob) -> dict[str, Any]:
    return {
        "filename": job.filename,
        "source_platform": job.source_platform,
        "total_rows": job.total_rows,
        "match_count": job.match_count,
        "no_match_count": job.no_match_count,
        "review_count": job.review_count,
        "error_count": job.error_count,
        "matching_engine_version": job.matching_engine_version,
    }


def best_candidate(row: ProcessingRow) -> dict[str, Any] | None:
    ev = row.evidence_json or {}
    wanted = {
        u
        for u in (
            ev.get("matched_username"),
            ev.get("review_candidate"),
            row.review_candidate,
            (row.matched_id or "").lower(),
        )
        if u
    }
    for c in ev.get("candidates") or []:
        if c.get("username") in wanted:
            return c
    cands = ev.get("candidates") or []
    return cands[0] if cands else None


def _row_report_dict(r: ProcessingRow) -> dict[str, Any]:
    ev = dict(r.evidence_json or {})
    ev["best"] = best_candidate(r)
    return {
        "original_row": r.original_row,
        "source_value": r.source_value,
        "country": r.country,
        "status": r.status,
        "decision": r.decision,
        "confidence": r.confidence,
        "matched_id": r.matched_id,
        "review_candidate": r.review_candidate,
        "output_destination": r.output_destination,
        "output_remarks": r.output_remarks,
        "manual_verdict": r.manual_verdict,
        "reason": r.reason,
        "error_message": r.error_message,
        "evidence_json": ev,
    }


OTHER_PLATFORM = {"kick": "twitch", "twitch": "kick"}
CHANNEL_URL = {"twitch": "https://www.twitch.tv/{}", "kick": "https://kick.com/{}"}


def verdict_key(row: ProcessingRow) -> str:
    return row.source_key or f"invalid:{(row.source_value or '').strip().lower()}"


def channel_links(
    row: ProcessingRow, source_platform: str, verdicts: dict[str, str]
) -> dict[str, str | None] | None:
    """One channel URL per platform for the twitch_id_link / kick_id_link cells.

    * source platform: the row's own channel (if the account exists)
    * other platform: the single best account — the confirmed match; otherwise the review
      candidate; otherwise the closest candidate found (exact same name first, then
      confidence). Accounts rejected in review are never shown. The ID column is still
      only filled for verified matches, so an empty ID next to a link means "unconfirmed".
    """
    ev = row.evidence_json
    if not ev or row.status == RowStatus.SKIPPED_EMPTY.value:
        return None
    target = OTHER_PLATFORM.get(source_platform)
    if target is None:
        return None
    links: dict[str, str | None] = {source_platform: None, target: None}

    src = ev.get("source_profile") or {}
    if ev.get("source_status") == "EXISTS" and src.get("username"):
        links[source_platform] = src.get("profile_url") or CHANNEL_URL[source_platform].format(
            src["username"]
        )

    candidates = [
        c
        for c in (ev.get("candidates") or [])
        if c.get("username") and verdicts.get(c["username"]) != "REJECTED"
    ]
    best: str | None = None
    if row.decision == "MATCH" and row.matched_id:
        best = row.matched_id.lower()
    elif row.review_candidate and row.review_candidate not in {
        u for u, v in verdicts.items() if v == "REJECTED"
    }:
        best = row.review_candidate
    elif candidates:
        source_name = normalize_username(row.source_value)

        def rank(c: dict[str, Any]) -> tuple[bool, float]:
            return (normalize_username(c["username"]) == source_name, c.get("confidence") or 0.0)

        best = max(candidates, key=rank)["username"]
    if best:
        profile = next((c for c in candidates if c["username"] == best), None)
        links[target] = (profile or {}).get("profile_url") or CHANNEL_URL[target].format(best)
    return links
