"""Immutable result collection must not execute project hooks/filters as the service."""

import os
from pathlib import Path

from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_execution import LocalHost


def test_snapshot_preserves_source_and_collects_new_deleted_and_ignored(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    (source / "old.txt").write_text("original")
    (source / ".gitignore").write_text("ignored.txt\n")
    git(source, "add", ".")
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "base",
    )
    base = baseline(source)
    env = LocalHost(os.environ).prepare(tmp_path / "state", source, "attempt", base)
    marker = tmp_path / "filter-ran"
    git(source, "config", "filter.untrusted.clean", f"touch {marker}")
    (env.checkout / ".gitattributes").write_text("*.txt filter=untrusted\n")
    (env.checkout / "old.txt").unlink()
    (env.checkout / "new.txt").write_text("new content")
    (env.checkout / "ignored.txt").write_text("local artifact")
    result, ignored = env.snapshot(base)
    assert not marker.exists()
    assert ignored == ["ignored.txt"]
    assert baseline(source) == base and (source / "old.txt").read_text() == "original"
    assert git(source, "show", f"{result}:new.txt") == b"new content"
    assert git(source, "diff", "--name-status", base, result).decode().splitlines() == [
        "A\t.gitattributes",
        "A\tnew.txt",
        "D\told.txt",
    ]
    assert env.runtime.is_dir() and (env.checkout / "new.txt").exists()

    # Capturing evidence without changing bytes retains the original Git identity.
    unchanged = LocalHost(os.environ).prepare(tmp_path / "state", source, "notes", result)
    same, _ = unchanged.snapshot(result)
    assert same == result
    (unchanged.checkout / "new.txt").write_text("changed again")
    different, _ = unchanged.snapshot(result)
    assert different != result
