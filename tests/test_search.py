"""Retrieval correctness, bounded evidence, frozen worker scope and completion context."""

import asyncio
import json

import pytest
from execution_fixtures import BASE
from execution_fixtures import fixture as execution_fixture
from project_fixtures import adopt, task_request
from result_fixtures import approve, current, fixture

from flowfield.activity import ActivityCreate
from flowfield.application import (
    ProjectSetup,
    TaskEdit,
    TaskProgress,
    TaskPublish,
    Workspace,
)
from flowfield.errors import ApplicationError
from flowfield.reads import PAGE_BYTES, ContextReads, size
from flowfield.search import Search, assignment_search
from flowfield.supervisor import WorkerBridge


def test_current_handoff_wins_over_similar_old_text_and_filters_scope(tmp_path):
    workspace = Workspace(tmp_path / "state")
    for project in ("harbor", "other"):
        adopt(workspace, ProjectSetup(path=str(tmp_path / project)))
        workspace.create_task(project, task_request(id="export", title="Export"))
    old = workspace.add_activity(
        "harbor",
        ActivityCreate(
            task_id="export",
            kind="handoff",
            expected_task_revision=1,
            supersedes=None,
            body="CSV export preserves insertion ordering. This is obsolete.",
        ),
    )
    latest = workspace.add_activity(
        "harbor",
        ActivityCreate(
            task_id="export",
            kind="handoff",
            expected_task_revision=1,
            supersedes=old.id,
            body="CSV export uses stable alphabetical ordering.",
        ),
    )
    workspace.add_activity(
        "other",
        ActivityCreate(
            task_id="export",
            kind="handoff",
            expected_task_revision=1,
            supersedes=None,
            body="CSV export private choice.",
        ),
    )
    search = Search(workspace, "http://localhost:1234")
    current_hits = search.page("harbor", "CSV export", kind="handoff")
    assert [hit["identity"] for hit in current_hits["items"]] == [latest.id]
    assert current_hits["items"][0]["current"]
    assert current_hits["items"][0]["url"].endswith("/tasks/HAR-1")
    assert current_hits["items"][0]["source"]["identity"] == latest.id
    old_hits = search.page("harbor", "CSV export", history="history")
    assert [hit["identity"] for hit in old_hits["items"]] == [old.id]
    assert not old_hits["items"][0]["current"]
    assert "[CSV]" in old_hits["items"][0]["snippet"]
    assert search.page("harbor", "CSV export", since="2099-01-01")["items"] == []
    with pytest.raises(ApplicationError, match="YYYY-MM-DD"):
        search.page("harbor", "CSV", since="yesterday")
    with pytest.raises(ApplicationError, match="1–20"):
        search.page("harbor", "*()")


def test_task_revision_search_pages_are_bounded_and_sources_exact(tmp_path):
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(tmp_path / "harbor")))
    task = workspace.create_task(
        "harbor", task_request(title="Needle task", body="old needle requirement")
    )
    workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=1, body="new needle requirement")
    )
    for index in range(25):
        workspace.add_activity(
            "harbor",
            ActivityCreate(
                task_id=task.id,
                body=f"needle observation {index} " + "z" * 10000,
            ),
        )
    search = Search(workspace)
    current_hits = search.page("harbor", "needle", entity="task", task_id=task.key)["items"]
    assert len(current_hits) == 1 and current_hits[0]["revision"] == 2
    historical = search.page("harbor", "needle", entity="task", history="history")["items"]
    assert len(historical) == 1 and historical[0]["revision"] == 1
    source = historical[0]["source"]
    text = ContextReads(workspace).text(
        "harbor", source["resource"], source["identity"], source["field"], source["revision"]
    )
    assert text["text"] == "old needle requirement"
    before, identities = None, []
    while True:
        page = search.page(
            "harbor", "needle", entity="activity", kind="note", before=before, limit=50
        )
        assert size(page) <= PAGE_BYTES
        identities += [item["identity"] for item in page["items"]]
        before = page["next_cursor"]
        if not before:
            break
    assert len(identities) == len(set(identities)) == 25


def test_result_brief_names_owner_and_next_action_without_full_report(tmp_path):
    service, _, _ = fixture(tmp_path)
    reads = ContextReads(service.workspace)
    preparing = reads.overview("harbor")
    entry = preparing["results"]["items"][0]
    assert entry["owner"] == "service" and entry["next_action"] == "wait"
    assert preparing["recommendation"]["action"] != "review_result"
    service.results.process("harbor")
    ready = reads.overview("harbor")
    assert ready["results"]["items"][0]["owner"] == "human"
    assert ready["recommendation"]["action"] == "review_result"
    assert ready["workers"]["occupied_slots"] == 0
    assert reads.task("harbor", "work")["proposed_result"]["id"] == current(service).id
    assert size(ready) < PAGE_BYTES
    approve(service, current(service))
    assert reads.overview("harbor")["results"]["items"][0]["next_action"] == "wait"
    service.results.process("harbor")
    assert reads.overview("harbor")["results"]["items"][0]["next_action"] == "none"
    matches = Search(service.workspace).page("harbor", "Outcome", entity="result")["items"]
    assert len(matches) == 1 and matches[0]["current"]
    assert matches[0]["revision"] == current(service).revision
    assert reads.text("harbor", "result", current(service).id, "summary")["text"] == "Outcome"


def test_worker_search_cannot_escape_snapshot_and_manual_report_is_usable(tmp_path):
    execution = execution_fixture(tmp_path, count=0)
    workspace = execution.workspace
    report = workspace.create_task(
        "harbor", task_request(id="report", title="Inspect catalog", body="Report scope")
    )
    workspace.add_activity(
        "harbor",
        ActivityCreate(
            task_id=report.id,
            kind="handoff",
            expected_task_revision=report.revision,
            supersedes=None,
            body="Measured finding: catalog contains two books.",
        ),
    )
    accepted = workspace.record_progress(
        "harbor",
        report.id,
        TaskProgress(
            expected_revision=report.revision,
            status="done",
            completion="report",
        ),
    )
    assert accepted.report_completion_revision == accepted.revision
    child = workspace.create_task(
        "harbor",
        task_request(
            title="Use report",
            status="up_next",
            body="Use the measured finding",
            dependencies=[report.id],
        ),
    )
    workspace.publish_task(
        "harbor",
        child.id,
        TaskPublish(
            expected_revision=child.revision,
            completion="report",
        ),
    )
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run and run.task_id == child.id
    execution.started("harbor", run.id)
    sections = execution.assignment("harbor", run.id)
    assert json.loads(sections["prerequisites"])[0]["commit"] is None
    workspace.add_activity(
        "harbor", ActivityCreate(task_id=child.id, body="Secret later observation")
    )
    bridge = WorkerBridge(execution, run, object())
    response = json.loads(asyncio.run(bridge.call("search_context", {"query": "measured finding"})))
    assert response["scope"] == "frozen_assignment" and response["items"]
    assert not assignment_search(sections, "secret later")["items"]
    assert "two books" in sections["prerequisites"]
    assert json.loads(sections["agreement"])["task_revision"] == child.revision + 1
