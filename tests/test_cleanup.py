"""Cleanup must never delete active work or anything outside TEMP_DIR."""

import os
import time

from app.services.cleanup import (
    delete_files,
    ensure_free_space,
    execute,
    free_space_message,
    plan_cleanup,
)


def touch(path, content=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_plan_only_selects_old_files(tmp_path):
    temp = tmp_path / "temp"
    old = touch(temp / "old.mkv")
    fresh = touch(temp / "fresh.mkv")
    set_mtime(old, hours_ago=48)
    set_mtime(fresh, hours_ago=1)

    plan = plan_cleanup(temp, older_than_hours=24, dry_run=True)
    assert old in plan.candidates
    assert fresh not in plan.candidates
    assert plan.bytes_reclaimable == old.stat().st_size


def set_mtime(path, hours_ago):
    stamp = time.time() - hours_ago * 3600
    os.utime(path, (stamp, stamp))


def test_active_files_are_never_candidates(tmp_path):
    temp = tmp_path / "temp"
    active = touch(temp / "job1.mkv")
    set_mtime(active, hours_ago=72)
    part = touch(temp / "job2.mkv.part", b"partial")
    set_mtime(part, hours_ago=72)

    plan = plan_cleanup(
        temp, active_paths=[active, temp / "job2.mkv"], older_than_hours=24, dry_run=True
    )
    assert active not in plan.candidates
    assert part not in plan.candidates
    assert {active, part} == set(plan.skipped_active)


def test_include_all_covers_every_inactive_file(tmp_path):
    temp = tmp_path / "temp"
    a = touch(temp / "a.mp4")
    b = touch(temp / "nested" / "b.mp4")
    set_mtime(a, hours_ago=1)
    set_mtime(b, hours_ago=1)

    plan = plan_cleanup(temp, older_than_hours=24, include_all=True)
    assert set(plan.candidates) == {a, b}


async def test_dry_run_deletes_nothing(tmp_path):
    temp = tmp_path / "temp"
    victim = touch(temp / "old.mkv")
    set_mtime(victim, hours_ago=99)
    plan = plan_cleanup(temp, older_than_hours=24, dry_run=True)
    removed, freed = await execute(plan)
    assert (removed, freed) == (0, 0)
    assert victim.exists()


async def test_execute_removes_planned_files(tmp_path):
    temp = tmp_path / "temp"
    victim = touch(temp / "old.mkv", b"12345")
    set_mtime(victim, hours_ago=99)
    plan = plan_cleanup(temp, older_than_hours=24, dry_run=False)
    removed, freed = await execute(plan)
    assert removed == 1
    assert freed == 5
    assert not victim.exists()


def test_delete_files_refuses_paths_outside_temp(tmp_path):
    temp = tmp_path / "temp"
    temp.mkdir()
    outside = touch(tmp_path / "important.txt", b"keep me")
    inside = touch(temp / "junk.txt", b"bye")

    removed = delete_files([outside, inside], temp)
    assert removed == 1
    assert outside.exists()
    assert not inside.exists()


def test_free_space_message():
    gib = 2**30
    assert free_space_message(10 * gib, gib, min_free_mb=100) is None
    message = free_space_message(50 * 2**20, 500 * 2**20, min_free_mb=2048)
    assert message and "Not enough free space" in message


async def test_ensure_free_space_runs(tmp_path):
    result = await ensure_free_space(tmp_path, needed_bytes=1, min_free_mb=1)
    assert result is None
