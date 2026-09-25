import pytest

from app.excel.state_machine import (
    REMARK_NEAREST,
    REMARK_NO_BOTH,
    REMARK_NO_KICK,
    REMARK_NO_TWITCH,
    StateMachineError,
    error_outcome,
    transition,
)
from app.models.tables import RowStatus


def res(source_status, decision, target_status=None, matched="Target_1", candidates=(), review=None):
    return {
        "source_status": source_status,
        "decision": decision,
        "target_status": target_status,
        "matched_id": matched if decision == "MATCH" else None,
        "review_candidate": review,
        "candidates": [{"username": c} for c in candidates],
    }


# ------------------------------------------------------------------ Kick source
@pytest.mark.parametrize(
    ("resolution", "dest", "remark", "status", "case"),
    [
        (res("EXISTS", "MATCH"), "Target_1", None, RowStatus.MATCH, "KICK_A"),
        (res("EXISTS", "REVIEW", review="cand"), None, REMARK_NEAREST, RowStatus.REVIEW, "KICK_B"),
        (
            res("EXISTS", "NO_MATCH", candidates=["closest"]),
            None,
            REMARK_NEAREST,
            RowStatus.NO_MATCH,
            "KICK_B",
        ),
        (res("EXISTS", "NO_MATCH"), None, REMARK_NO_TWITCH, RowStatus.NO_MATCH, "KICK_B"),
        (res("NOT_FOUND", "MATCH", "VERIFIED"), "Target_1", REMARK_NO_KICK, RowStatus.MATCH, "KICK_C"),
        (
            res("NOT_FOUND", "REVIEW", "EXISTS_UNVERIFIED", review="same"),
            None,
            REMARK_NO_KICK,
            RowStatus.SOURCE_NOT_FOUND,
            "KICK_C2",
        ),
        (
            res("NOT_FOUND", "NO_MATCH", "NOT_FOUND"),
            None,
            REMARK_NO_BOTH,
            RowStatus.SOURCE_NOT_FOUND,
            "KICK_D",
        ),
    ],
)
def test_kick_source_table(resolution, dest, remark, status, case):
    out = transition("kick", resolution)
    assert (out.destination, out.remarks, out.status, out.case) == (dest, remark, status, case)


# ------------------------------------------------------------------ Twitch source
@pytest.mark.parametrize(
    ("resolution", "dest", "remark", "case"),
    [
        (res("EXISTS", "MATCH"), "Target_1", None, "TWITCH_A"),
        (res("EXISTS", "REVIEW", review="cand"), None, REMARK_NEAREST, "TWITCH_B"),
        (res("EXISTS", "NO_MATCH", candidates=["closest"]), None, REMARK_NEAREST, "TWITCH_B"),
        (res("EXISTS", "NO_MATCH"), None, REMARK_NO_KICK, "TWITCH_B"),
        (res("NOT_FOUND", "NO_MATCH", "NOT_FOUND"), None, REMARK_NO_BOTH, "TWITCH_C"),
        (res("NOT_FOUND", "REVIEW", "EXISTS_UNVERIFIED", review="same"), None, REMARK_NO_TWITCH, "TWITCH_C2"),
        (res("NOT_FOUND", "MATCH", "VERIFIED"), "Target_1", REMARK_NO_TWITCH, "TWITCH_C1"),
    ],
)
def test_twitch_source_table(resolution, dest, remark, case):
    out = transition("twitch", resolution)
    assert (out.destination, out.remarks, out.case) == (dest, remark, case)


def test_remark_never_says_no_kick_id_when_a_kick_link_is_given():
    # twitch source, twitch account exists, a (non-rejected) Kick account is linked
    out = transition("twitch", res("EXISTS", "NO_MATCH", candidates=["somekick"]))
    assert out.remarks == REMARK_NEAREST  # a Kick link is given, but only the nearest channel
    # ...but a candidate rejected in review is not linked, so no Kick id remains
    rejected = res("EXISTS", "NO_MATCH")
    rejected["candidates"] = [{"username": "somekick", "manual_verdict": "REJECTED"}]
    assert transition("twitch", rejected).remarks == REMARK_NO_KICK


def test_exact_remark_phrases():
    assert REMARK_NO_KICK == "no kick id"
    assert REMARK_NO_TWITCH == "no twitch id found"
    assert REMARK_NEAREST == "nearest possible channel"
    assert REMARK_NO_BOTH == "no Id on both platforms"


def test_undefined_states_raise_instead_of_guessing():
    with pytest.raises(StateMachineError):
        transition("kick", {"source_status": "EXISTS", "decision": "MAYBE"})
    with pytest.raises(StateMachineError):
        transition("kick", {"source_status": "NOT_FOUND", "decision": "MATCH", "target_status": "NOT_FOUND"})
    with pytest.raises(StateMachineError):  # MATCH without an id is impossible
        transition("kick", {"source_status": "EXISTS", "decision": "MATCH", "matched_id": None})


def test_errors_never_write():
    out = error_outcome(RowStatus.TEMPORARY_ERROR)
    assert not out.write_destination and not out.write_remarks


def test_existing_destination_preserved_by_default():
    out = transition("kick", res("EXISTS", "MATCH"), existing_destination="SomeoneTyped")
    assert out.status == RowStatus.PRESERVED and not out.write_destination and not out.write_remarks


def test_overwrite_policy_only_replaces_with_verified_id():
    out = transition("kick", res("EXISTS", "MATCH"), existing_destination="old", policy="overwrite")
    assert out.write_destination and out.destination == "Target_1"
    out2 = transition("kick", res("EXISTS", "NO_MATCH"), existing_destination="old", policy="overwrite")
    assert not out2.write_destination  # never erase user data on a no-match


def test_placeholder_none_becomes_genuinely_empty():
    out = transition("twitch", res("EXISTS", "NO_MATCH"), existing_destination="None")
    assert out.write_destination and out.destination is None
    assert out.remarks == REMARK_NO_KICK


def test_blank_destination_not_rewritten_when_nothing_to_write():
    out = transition("kick", res("EXISTS", "NO_MATCH"), existing_destination=None)
    assert not out.write_destination


def test_user_remark_is_never_erased():
    out = transition("kick", res("NOT_FOUND", "NO_MATCH", "NOT_FOUND"), existing_remarks="call back monday")
    assert not out.write_remarks
    out2 = transition("kick", res("EXISTS", "MATCH"), existing_remarks="call back monday")
    assert not out2.write_remarks


def test_app_remark_from_previous_run_is_replaced_or_cleared():
    out = transition("kick", res("EXISTS", "MATCH"), existing_remarks="no kick id")
    assert out.write_remarks and out.remarks is None
    out2 = transition("twitch", res("NOT_FOUND", "NO_MATCH", "NOT_FOUND"), existing_remarks="no kick id")
    assert out2.write_remarks and out2.remarks == REMARK_NO_BOTH
