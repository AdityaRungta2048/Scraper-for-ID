"""Explicit row state machine: (source platform, resolution) -> Excel cell writes.

Implements the tables in docs/DESIGN.md §G exactly. Every combination of inputs maps
to exactly one outcome; unknown combinations raise instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.tables import RowStatus

REMARK_NO_KICK = "no kick id"
REMARK_NO_TWITCH = "no twitch id found"
REMARK_NO_BOTH = "no Id on both platforms"
REMARK_MISSING = {"kick": REMARK_NO_KICK, "twitch": REMARK_NO_TWITCH}


def remark_source_missing(source_platform: str) -> str:
    """Only the other platform has the channel (the source account does not exist)."""
    return f"no {source_platform} channel found"


# written by earlier versions; removed from every row (also rows whose ID was already filled in)
OLD_NEAREST_REMARKS = {
    "nearest possible twitch channel",
    "nearest possible kick channel",
    "nearest possible channel",
}
APP_REMARKS = {
    REMARK_NO_KICK,
    REMARK_NO_TWITCH,
    REMARK_NO_BOTH,
    remark_source_missing("kick"),
    remark_source_missing("twitch"),
    *OLD_NEAREST_REMARKS,
}
EMPTY_PLACEHOLDERS = {"", "none", "null", "nan", "n/a", "na", "-", "--", "unknown", "not found"}


def is_blank(value: Any) -> bool:
    return value is None or str(value).strip().lower() in EMPTY_PLACEHOLDERS


@dataclass(frozen=True)
class RowOutcome:
    status: RowStatus
    destination: str | None  # value to write (None = empty cell)
    remarks: str | None  # value to write (None = empty cell)
    write_destination: bool
    write_remarks: bool
    case: str  # e.g. "KICK_A" — for audit/debugging


# (source_platform, source_status, decision, target_status) -> (case, write the matched id?)
_TABLE: dict[tuple[str, str, str, str | None], tuple[str, bool]] = {
    # ---- Kick source -------------------------------------------------------------
    ("kick", "EXISTS", "MATCH", None): ("KICK_A", True),
    ("kick", "EXISTS", "REVIEW", None): ("KICK_B", False),
    ("kick", "EXISTS", "NO_MATCH", None): ("KICK_B", False),
    ("kick", "NOT_FOUND", "MATCH", "VERIFIED"): ("KICK_C", True),
    ("kick", "NOT_FOUND", "REVIEW", "EXISTS_UNVERIFIED"): ("KICK_C2", False),
    ("kick", "NOT_FOUND", "NO_MATCH", "EXISTS_UNVERIFIED"): ("KICK_C2", False),
    ("kick", "NOT_FOUND", "NO_MATCH", "NOT_FOUND"): ("KICK_D", False),
    # ---- Twitch source -----------------------------------------------------------
    ("twitch", "EXISTS", "MATCH", None): ("TWITCH_A", True),
    ("twitch", "EXISTS", "REVIEW", None): ("TWITCH_B", False),
    ("twitch", "EXISTS", "NO_MATCH", None): ("TWITCH_B", False),
    ("twitch", "NOT_FOUND", "MATCH", "VERIFIED"): ("TWITCH_C1", True),
    ("twitch", "NOT_FOUND", "REVIEW", "EXISTS_UNVERIFIED"): ("TWITCH_C2", False),
    ("twitch", "NOT_FOUND", "NO_MATCH", "EXISTS_UNVERIFIED"): ("TWITCH_C2", False),
    ("twitch", "NOT_FOUND", "NO_MATCH", "NOT_FOUND"): ("TWITCH_C", False),
}


def link_presence(source_platform: str, resolution: dict[str, Any]) -> dict[str, bool]:
    """Which platforms get a channel link (same rule as the link columns).

    Source platform: the source account exists. Other platform: only a matched account.
    """
    target = "twitch" if source_platform == "kick" else "kick"
    return {
        source_platform: resolution.get("source_status") == "EXISTS",
        target: bool(resolution.get("matched_id")),
    }


def remark_for_links(links: dict[str, bool], source_platform: str = "twitch") -> str | None:
    """Remarks mirror the link columns.

    both channels -> empty; only the source channel -> "no <target> id"; only the other
    platform's channel -> "no <source> channel found"; neither -> "no Id on both platforms".
    """
    target = "twitch" if source_platform == "kick" else "kick"
    has_source, has_target = links.get(source_platform, False), links.get(target, False)
    if has_source and has_target:
        return None
    if has_source:
        return REMARK_MISSING[target]
    if has_target:
        return remark_source_missing(source_platform)
    return REMARK_NO_BOTH


class StateMachineError(ValueError):
    pass


def transition(
    source_platform: str,
    resolution: dict[str, Any],
    existing_destination: Any = None,
    existing_remarks: Any = None,
    policy: str = "preserve",
) -> RowOutcome:
    source_status = resolution.get("source_status")
    decision = resolution.get("decision")
    target_status = resolution.get("target_status") if source_status == "NOT_FOUND" else None
    key = (source_platform, str(source_status), str(decision), target_status)
    if key not in _TABLE:
        raise StateMachineError(f"undefined state {key}")
    case, write_id = _TABLE[key]
    remark = remark_for_links(link_presence(source_platform, resolution), source_platform)

    matched = resolution.get("matched_id")
    if write_id and not matched:
        raise StateMachineError(f"state {case} requires a matched id")
    destination = str(matched) if write_id else None

    if source_status == "NOT_FOUND":
        status = RowStatus.MATCH if write_id else RowStatus.SOURCE_NOT_FOUND
    else:
        status = {"MATCH": RowStatus.MATCH, "REVIEW": RowStatus.REVIEW, "NO_MATCH": RowStatus.NO_MATCH}[
            str(decision)
        ]

    # Existing destination values in the upload are user data.
    if not is_blank(existing_destination):
        if policy == "preserve":
            clear_old = str(existing_remarks or "").strip() in OLD_NEAREST_REMARKS
            return RowOutcome(RowStatus.PRESERVED, None, None, False, clear_old, case + "_PRESERVED")
        # overwrite policy: replace only with a verified id; never erase user data on no-match
        write_dest = destination is not None
    else:
        # write the matched id, or turn a placeholder such as "None" into a genuinely empty cell
        write_dest = destination is not None or existing_destination is not None

    # Remarks: never erase a user's own note; only fill blanks or replace/clear our own phrases.
    existing_rem_blank = is_blank(existing_remarks)
    existing_is_ours = existing_remarks is not None and str(existing_remarks).strip() in APP_REMARKS
    if remark is not None:
        write_rem = existing_rem_blank or existing_is_ours
    else:
        write_rem = existing_is_ours or (existing_remarks is not None and existing_rem_blank)
    return RowOutcome(status, destination, remark, write_dest, write_rem, case)


def error_outcome(status: RowStatus) -> RowOutcome:
    """Errors never touch the workbook."""
    assert status.is_error
    return RowOutcome(status, None, None, False, False, "ERROR")


def empty_source_outcome() -> RowOutcome:
    return RowOutcome(RowStatus.SKIPPED_EMPTY, None, None, False, False, "EMPTY_SOURCE")
