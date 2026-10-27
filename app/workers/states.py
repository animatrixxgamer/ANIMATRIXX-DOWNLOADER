"""Job lifecycle: states, allowed transitions, retry policy, stage planning.

All of this is pure logic (no I/O) so crash-recovery decisions can be unit
tested without Telegram, FFmpeg or a network.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


class JobState(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    ENCODING = "encoding"
    ENCODED = "encoded"
    UPLOADING = "uploading"
    ARCHIVED = "archived"
    CLEANUP_PENDING = "cleanup_pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"

    @classmethod
    def parse(cls, value: str | "JobState") -> "JobState":
        if isinstance(value, JobState):
            return value
        return cls(str(value).lower())


class Stage(str, enum.Enum):
    DOWNLOAD = "download"
    ENCODE = "encode"
    UPLOAD = "upload"
    CLEANUP = "cleanup"


# Which states the dispatcher is allowed to pick work from.
PICKABLE: frozenset[JobState] = frozenset(
    {
        JobState.QUEUED,
        JobState.DOWNLOADED,
        JobState.ENCODED,
        JobState.ARCHIVED,
    }
)

TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.PENDING: frozenset({JobState.QUEUED, JobState.CANCELLED, JobState.FAILED}),
    JobState.QUEUED: frozenset(
        {JobState.DOWNLOADING, JobState.ENCODING, JobState.UPLOADING, JobState.CANCELLED, JobState.FAILED, JobState.PAUSED}
    ),
    JobState.DOWNLOADING: frozenset({JobState.DOWNLOADED, JobState.FAILED, JobState.CANCELLED, JobState.PAUSED}),
    JobState.DOWNLOADED: frozenset({JobState.ENCODING, JobState.UPLOADING, JobState.FAILED, JobState.CANCELLED, JobState.PAUSED}),
    JobState.ENCODING: frozenset({JobState.ENCODED, JobState.FAILED, JobState.CANCELLED, JobState.PAUSED}),
    JobState.ENCODED: frozenset({JobState.UPLOADING, JobState.FAILED, JobState.CANCELLED, JobState.PAUSED}),
    JobState.UPLOADING: frozenset({JobState.ARCHIVED, JobState.FAILED, JobState.CANCELLED, JobState.PAUSED}),
    JobState.ARCHIVED: frozenset({JobState.CLEANUP_PENDING, JobState.FAILED, JobState.COMPLETED}),
    JobState.CLEANUP_PENDING: frozenset({JobState.COMPLETED, JobState.FAILED}),
    JobState.FAILED: frozenset({JobState.QUEUED, JobState.CANCELLED}),
    JobState.CANCELLED: frozenset({JobState.QUEUED}),
    JobState.PAUSED: frozenset({JobState.QUEUED, JobState.CANCELLED}),
    JobState.COMPLETED: frozenset(),
}

# Terminal = nothing can move it along on its own.
TERMINAL: frozenset[JobState] = frozenset({JobState.COMPLETED, JobState.CANCELLED})
ACTIVE: frozenset[JobState] = frozenset(
    {
        JobState.DOWNLOADING,
        JobState.ENCODING,
        JobState.UPLOADING,
        JobState.QUEUED,
        JobState.DOWNLOADED,
        JobState.ENCODED,
        JobState.ARCHIVED,
        JobState.CLEANUP_PENDING,
    }
)


class InvalidTransition(Exception):
    def __init__(self, current: JobState, target: JobState) -> None:
        super().__init__(f"Invalid job transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: JobState | str, target: JobState | str) -> bool:
    current = JobState.parse(current)
    target = JobState.parse(target)
    return target in TRANSITIONS.get(current, frozenset())


def ensure_transition(current: JobState | str, target: JobState | str) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(JobState.parse(current), JobState.parse(target))


# --------------------------------------------------------------- retries
def backoff_seconds(attempt: int, base: int = 30, cap: int = 3600) -> int:
    """Exponential backoff with an upper bound. attempt is 1-based."""
    if attempt <= 0:
        return 0
    return min(cap, base * (2 ** (attempt - 1)))


def should_retry(attempt: int, max_attempts: int, permanent: bool) -> bool:
    """Permanent errors (403/404/invalid URL) are never retried."""
    if permanent:
        return False
    return attempt < max_attempts


def next_attempt_time(attempt: int, base: int = 30, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + timedelta(seconds=backoff_seconds(attempt, base))


def is_permanent_error(code: int | str | None) -> bool:
    """HTTP status codes and synthetic error codes that will not fix themselves."""
    if code is None:
        return False
    if isinstance(code, str):
        return code in {
            "invalid_url",
            "blocked_destination",
            "unsupported_scheme",
            "file_type_rejected",
            "size_limit",
            "no_media_stream",
        }
    return code in {400, 401, 403, 404, 410, 451}


# --------------------------------------------------------------- planning
@dataclass(frozen=True)
class JobPlan:
    """Which stages a job still needs, decided from observable facts."""

    stage: Stage | None  # None when the job has nothing left to do


def next_stage(
    state: JobState | str,
    *,
    needs_download: bool,
    file_present: bool,
    output_present: bool,
    profile: str,
    archived: bool,
    cancel_requested: bool = False,
) -> JobPlan:
    """Pick the next stage for a job the dispatcher picked up.

    ``needs_download`` - the source is a URL / Telegram upload, not a local file.
    ``file_present``   - the working file exists in TEMP_DIR.
    ``output_present`` - the encoded output exists (encoded jobs only).
    ``archived``       - the archive row already holds a message id (idempotency).
    """
    if cancel_requested:
        return JobPlan(None)

    state = JobState.parse(state)
    if state not in PICKABLE:
        return JobPlan(None)

    if needs_download and not file_present:
        return JobPlan(Stage.DOWNLOAD)
    if not file_present and not output_present:
        return JobPlan(None)  # nothing to work with; caller marks it failed

    if profile and profile != "original" and not output_present:
        return JobPlan(Stage.ENCODE)

    if not archived:
        return JobPlan(Stage.UPLOAD)

    if file_present or output_present:
        return JobPlan(Stage.CLEANUP)
    return JobPlan(None)


__all__ = [
    "JobState",
    "Stage",
    "JobPlan",
    "PICKABLE",
    "ACTIVE",
    "TERMINAL",
    "TRANSITIONS",
    "InvalidTransition",
    "can_transition",
    "ensure_transition",
    "backoff_seconds",
    "should_retry",
    "next_attempt_time",
    "is_permanent_error",
    "next_stage",
]
