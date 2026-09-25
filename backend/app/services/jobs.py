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
from app.excel.state_machine import transition
from app.excel.verifier import verify_output
from app.logging_setup import get_logger
from app.models import JobStatus, ManualReview, ProcessingJob, ProcessingRow, RowStatus
from app.models.tables import ERROR_STATUSES, new_id
from app.platforms.base import PlatformAdapter
from app.platforms.kick import KickAdapter
from app.platforms.twitch import TwitchAdapter
from app.services.resolver import apply_manual_reviews
from app.version import MATCHING_ENGINE_VERSION

log = get_logger("jobs")
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


def _pct(value: Any) -> str:
    return f", {round(value)}%" if isinstance(value, (int, float)) else ""


def channel_links(
    row: ProcessingRow, source_platform: str, verdicts: dict[str, str]
) -> dict[str, str | None] | None:
    """Text for the twitch_id_link / kick_id_link cells: every channel the engine found.

    The matched channel comes first as a plain URL (it becomes the cell's hyperlink); every
    other account the engine looked at is listed with a label saying what it is — these
    are for convenience only and never change the ID column.
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

    matched = (row.matched_id or "").lower() if row.decision == "MATCH" else ""
    lines: list[str] = []
    candidates = sorted(ev.get("candidates") or [], key=lambda c: -(c.get("confidence") or 0))
    for c in candidates:
        user = c.get("username")
        if not user:
            continue
        url = c.get("profile_url") or CHANNEL_URL[target].format(user)
        verdict = verdicts.get(user)
        if user == matched:
            label = " (confirmed in review)" if verdict == "CONFIRMED" else ""
            lines.insert(0, url + label)
            continue
        if verdict == "REJECTED":
            label = " (rejected in review)"
        elif ev.get("source_status") == "NOT_FOUND":
            label = " (same name, unverified)"  # the source account itself does not exist
        elif c.get("decision") in ("MATCH", "REVIEW"):
            label = f" (needs review{_pct(c.get('confidence'))})"
        else:
            label = f" (not matched{_pct(c.get('confidence'))})"
        lines.append(url + label)
    if matched and not any(line.split(" ")[0].lower().endswith("/" + matched) for line in lines):
        lines.insert(0, CHANNEL_URL[target].format(matched))
    links[target] = "\n".join(lines) or None
    return links
