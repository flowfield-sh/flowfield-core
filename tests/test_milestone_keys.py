"""Milestone references are project-local, immutable and shared by every interface."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from project_fixtures import adopt, task_request

from flowfield.application import (
    MilestoneCreate,
    MilestoneEdit,
    ProjectSetup,
    TaskEdit,
    Workspace,
)
from flowfield.errors import ApplicationError
from flowfield.reads import ContextReads


def test_milestone_keys_survive_edits_restart_and_concurrent_creation(tmp_path: Path):
    workspace = Workspace(tmp_path / "state")
    for project in ("one", "two"):
        adopt(workspace, ProjectSetup(path=str(tmp_path / project)))

    def create(index):
        return workspace.create_milestone("one", MilestoneCreate(title=f"Group {index}"))

    with ThreadPoolExecutor(4) as pool:
        milestones = list(pool.map(create, range(12)))
    assert {m.key for m in milestones} == {f"M-{n}" for n in range(1, 13)}
    original = workspace.milestone("one", "M-1")
    assert workspace.milestone("one", "m-1") == original
    edited = workspace.edit_milestone(
        "one", "M-1", MilestoneEdit(expected_revision=1, title="Renamed")
    )
    assert edited.id == original.id and edited.key == "M-1"
    assert workspace.milestone("one", original.id).title == "Renamed"
    with pytest.raises(ApplicationError, match="stale"):
        workspace.edit_milestone("one", "M-1", MilestoneEdit(expected_revision=1, title="Lost"))
    with pytest.raises(ApplicationError, match="already exists"):
        workspace.create_milestone("one", MilestoneCreate(id=original.id, title="Duplicate"))
    assert create(13).key == "M-13"
    assert workspace.create_milestone("two", MilestoneCreate(title="Other")).key == "M-1"
    assert workspace.milestone("two", "M-1").title == "Other"
    with pytest.raises(ApplicationError, match="not found"):
        workspace.milestone("two", "M-2")
    with pytest.raises(ValueError):
        MilestoneCreate(id="M-1", title="Cannot shadow a key")

    task = workspace.create_task("one", task_request(title="Member", milestone_id="m-1"))
    assert task.milestone_id == original.id
    assert (
        workspace.edit_task(
            "one", task.key, TaskEdit(expected_revision=1, milestone_id="M-1")
        ).revision
        == 1
    )
    moved = workspace.edit_task("one", task.key, TaskEdit(expected_revision=1, milestone_id="M-2"))
    assert moved.milestone_id == workspace.milestone("one", "M-2").id
    reads = ContextReads(workspace)
    assert reads.tasks("one", milestone_id="M-2")["items"][0]["key"] == task.key
    assert reads.milestone("one", "M-1")["key"] == "M-1"
    reopened = Workspace(tmp_path / "state")
    assert reopened.milestones("one") == workspace.milestones("one")
    assert [m.key for m in reopened.milestones("one")] == [f"M-{n}" for n in range(1, 14)]
