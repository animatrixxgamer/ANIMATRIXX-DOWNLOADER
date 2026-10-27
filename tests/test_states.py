"""Job state machine, retry policy and stage planning."""

import pytest

from app.workers.states import (
    ACTIVE,
    PICKABLE,
    InvalidTransition,
    JobState,
    Stage,
    backoff_seconds,
    can_transition,
    ensure_transition,
    is_permanent_error,
    next_stage,
    should_retry,
)


@pytest.mark.parametrize(
    "current,target,ok",
    [
        ("pending", "queued", True),
        ("queued", "downloading", True),
        ("queued", "completed", False),
        ("downloading", "downloaded", True),
        ("downloading", "archived", False),
        ("downloaded", "encoding", True),
        ("downloaded", "uploading", True),
        ("encoding", "encoded", True),
        ("encoding", "downloaded", False),
        ("encoded", "uploading", True),
        ("uploading", "archived", True),
        ("uploading", "downloaded", False),
        ("archived", "cleanup_pending", True),
        ("archived", "queued", False),
        ("cleanup_pending", "completed", True),
        ("failed", "queued", True),
        ("failed", "uploading", False),
        ("cancelled", "queued", True),
        ("paused", "queued", True),
        ("completed", "queued", False),
        ("completed", "failed", False),
    ],
)
def test_transitions(current, target, ok):
    assert can_transition(current, target) is ok


def test_ensure_transition_raises():
    ensure_transition("queued", "downloading")
    with pytest.raises(InvalidTransition):
        ensure_transition("completed", "queued")


def test_parse_accepts_enum_and_string():
    assert JobState.parse(JobState.QUEUED) is JobState.QUEUED
    assert JobState.parse("UPLOADING") is JobState.UPLOADING


def test_pickable_and_active_sets():
    assert JobState.PENDING not in PICKABLE
    assert JobState.PAUSED not in PICKABLE
    assert {JobState.QUEUED, JobState.DOWNLOADED} <= PICKABLE
    assert JobState.FAILED not in ACTIVE
    assert JobState.DOWNLOADING in ACTIVE


@pytest.mark.parametrize(
    "attempt,base,expected",
    [(0, 30, 0), (1, 30, 30), (2, 30, 60), (3, 30, 120), (10, 30, 3600)],
)
def test_backoff_grows_and_caps(attempt, base, expected):
    assert backoff_seconds(attempt, base) == expected


def test_retry_policy():
    assert should_retry(attempt=1, max_attempts=3, permanent=False) is True
    assert should_retry(attempt=3, max_attempts=3, permanent=False) is False
    assert should_retry(attempt=1, max_attempts=3, permanent=True) is False


def test_permanent_error_classification():
    assert is_permanent_error(404)
    assert is_permanent_error(403)
    assert not is_permanent_error(500)
    assert not is_permanent_error(429)
    assert is_permanent_error("blocked_destination")
    assert is_permanent_error("size_limit")
    assert not is_permanent_error("transient")
    assert not is_permanent_error(None)


def test_next_stage_download_first():
    plan = next_stage(
        "queued", needs_download=True, file_present=False, output_present=False,
        profile="balanced", archived=False,
    )
    assert plan.stage is Stage.DOWNLOAD


def test_next_stage_encodes_after_download():
    plan = next_stage(
        "downloaded", needs_download=True, file_present=True, output_present=False,
        profile="balanced", archived=False,
    )
    assert plan.stage is Stage.ENCODE


def test_next_stage_original_profile_skips_encode():
    plan = next_stage(
        "downloaded", needs_download=True, file_present=True, output_present=False,
        profile="original", archived=False,
    )
    assert plan.stage is Stage.UPLOAD


def test_next_stage_uploads_then_cleans():
    plan = next_stage(
        "encoded", needs_download=False, file_present=True, output_present=True,
        profile="balanced", archived=False,
    )
    assert plan.stage is Stage.UPLOAD
    plan = next_stage(
        "archived", needs_download=False, file_present=True, output_present=True,
        profile="balanced", archived=True,
    )
    assert plan.stage is Stage.CLEANUP


def test_next_stage_stops_when_cancelled():
    plan = next_stage(
        "queued", needs_download=True, file_present=False, output_present=False,
        profile="balanced", archived=False, cancel_requested=True,
    )
    assert plan.stage is None


def test_next_stage_none_for_terminal_states():
    for state in ("completed", "failed", "paused", "pending"):
        plan = next_stage(
            state, needs_download=True, file_present=False, output_present=False,
            profile="balanced", archived=False,
        )
        assert plan.stage is None


def test_next_stage_none_when_file_and_source_missing():
    plan = next_stage(
        "queued", needs_download=True, file_present=False, output_present=False,
        profile="balanced", archived=False,
    )
    # planned download is correct; the "impossible" case is a local-file job
    plan = next_stage(
        "queued", needs_download=False, file_present=False, output_present=False,
        profile="balanced", archived=False,
    )
    assert plan.stage is None
