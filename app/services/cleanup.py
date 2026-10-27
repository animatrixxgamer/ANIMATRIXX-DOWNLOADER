"""Safe temporary-file cleanup: never touches active work or anything outside TEMP_DIR."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass
class CleanupPlan:
    """What would be deleted - inspected by /storage before anything is removed."""

    candidates: list[Path] = field(default_factory=list)
    bytes_reclaimable: int = 0
    skipped_active: list[Path] = field(default_factory=list)
    skipped_outside: list[Path] = field(default_factory=list)
    dry_run: bool = True

    @property
    def count(self) -> int:
        return len(self.candidates)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def plan_cleanup(
    temp_dir: Path,
    *,
    active_paths: Iterable[Path] = (),
    older_than_hours: float = 24.0,
    dry_run: bool = True,
    include_all: bool = False,
) -> CleanupPlan:
    """Select deletable files under ``temp_dir`` only.

    Active job files and anything outside the temp directory are never
    candidates, no matter how old they are.
    """
    temp_dir = Path(temp_dir)
    plan = CleanupPlan(dry_run=dry_run)
    if not temp_dir.exists():
        return plan

    active = {Path(p).resolve() for p in active_paths if p}
    # a download in progress is written to <file>.part - protect both forms
    active |= {Path(str(p) + ".part") for p in active}
    cutoff = time.time() - older_than_hours * 3600

    for path in temp_dir.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not _is_inside(path, temp_dir):
            plan.skipped_outside.append(path)
            continue
        if resolved in active:
            plan.skipped_active.append(path)
            continue
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - race with another cleaner
            continue
        if include_all or stat.st_mtime < cutoff:
            plan.candidates.append(path)
            plan.bytes_reclaimable += stat.st_size
    return plan


async def execute(plan: CleanupPlan) -> tuple[int, int]:
    """Delete the planned files. Returns ``(files_removed, bytes_reclaimed)``."""
    if plan.dry_run:
        return 0, 0
    removed = 0
    freed = 0
    for path in plan.candidates:
        try:
            size = path.stat().st_size
            await asyncio.to_thread(path.unlink)
            removed += 1
            freed += size
        except OSError as exc:
            logger.debug("Could not delete %s: %s", path, exc)
    if removed:
        logger.info("Cleanup removed %s file(s), freed %s bytes", removed, freed)
    return removed, freed


def free_space_message(available_bytes: int, required_bytes: int, min_free_mb: int) -> str | None:
    """Explain an insufficient-disk refusal in plain language."""
    if available_bytes >= required_bytes + min_free_mb * 2**20:
        return None
    return (
        f"Not enough free space: {available_bytes // 2**20} MiB available, "
        f"{required_bytes // 2**20} MiB needed plus a {min_free_mb} MiB reserve. "
        "Run /cleanup or free space on the VPS first."
    )


async def ensure_free_space(
    temp_dir: Path, needed_bytes: int, min_free_mb: int
) -> str | None:
    root = Path(temp_dir)
    usage = shutil.disk_usage(root if root.exists() else ".")
    return free_space_message(usage.free, needed_bytes, min_free_mb)


def delete_files(paths: Iterable[Path], temp_dir: Path) -> int:
    """Delete specific files, refusing anything outside ``temp_dir``."""
    removed = 0
    for path in paths:
        path = Path(path)
        if not _is_inside(path, temp_dir):
            logger.warning("Refusing to delete outside temp dir: %s", path)
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.debug("Could not delete %s: %s", path, exc)
    return removed


__all__ = [
    "CleanupPlan",
    "plan_cleanup",
    "execute",
    "ensure_free_space",
    "free_space_message",
    "delete_files",
]
