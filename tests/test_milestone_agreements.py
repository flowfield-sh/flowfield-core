"""Retained whole-outcome intent and task preparation remain distinct from acceptance."""

import asyncio
import json

import pytest
from test_execution import BASE, RESULT, fixture
from test_input_continuation import answer, question
from test_storage_migrations import logical_data
from test_worker_documents import prepare

from flowfield import migrations
from flowfield.agent_tools import workspace_scope
from flowfield.application import (
    Edit,
    MilestoneCreate,
    MilestoneEdit,
    TaskEdit,
    TaskPublish,
    Workspace,
)
from flowfield.attention import attention_page
from flowfield.browser import BrowserReads
from flowfield.documents import DocumentCreate, DocumentEdit, Documents
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.migrations import Migration
from flowfield.milestones import Milestones
from flowfield.questions import Questions
from flowfield.search import Search
from flowfield.supervisor import Supervisor
from flowfield.worker_tools import worker_tools
from flowfield.workspaces import WorkspaceCreate, Workspaces


def attach(store, milestone):
    task = store.task("harbor", "task-0")
    store.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, milestone_id=milestone.id)
    )
    return prepare(store)


def test_preparation_freezes_milestone_criteria_sources_and_worker_contribution(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    docs = Documents(store)
    doc = docs.create("harbor", DocumentCreate(title="Design", body="Preserve the API"))
    milestones = Milestones(store)
    original = milestones.create(
        "harbor",
        MilestoneCreate(
            title="Installed integration",
            body="A working installed experience",
            acceptance_criteria="Cold installation works without a source checkout.",
            source_url="https://example.test/issues/20",
            documents=[{"id": doc.id}],
        ),
    )
    first = attach(store, original)
    assert first.publication.milestone.revision == 1
    assert original.documents[0].revision == 1
    assert original.documents[0].title == "Design"
    changed_doc = docs.edit("harbor", doc.id, DocumentEdit(expected_revision=1, body="New policy"))
    latest = milestones.edit(
        "harbor",
        original.id,
        MilestoneEdit(
            expected_revision=1,
            acceptance_criteria="Also works offline.",
            documents=[{"id": doc.id}],
        ),
    )
    assert latest.documents[0].revision == changed_doc.revision
    assert milestones.get("harbor", original.key, 1) == original
    assert (
        milestones.edit("harbor", original.id, MilestoneEdit(expected_revision=2, body=latest.body))
        == latest
    )
    assert len(milestones.history("harbor", original.id)["items"]) == 2
    current = store.task("harbor", first.id)
    assert current.publication == first.publication and current.readiness == "ready"
    assert current.milestone_update.prepared_revision == 1
    assert current.milestone_update.latest_revision == 2
    impact = milestones.impact("harbor", original.id)["items"]
    assert len(impact) == 1 and impact[0]["task_id"] == first.id
    board = BrowserReads(store).board("harbor")
    assert board.tasks[0].milestone_update == current.milestone_update
    notices = attention_page(store, "harbor", "waiting").items
    assert len([item for item in notices if item.kind == "document"]) == 1
    assert Search(store).page("harbor", "offline")["items"][0]["identity"] == original.id
    run = execution.claim("harbor", BASE, {BASE: set()})
    assignment = execution.assignment("harbor", run.id)
    assert assignment["milestone"] == original.body
    assert assignment["milestone_criteria"] == original.acceptance_criteria
    assert json.loads(assignment["milestone_agreement"])["revision"] == 1
    assert assignment[f"document:{doc.id}"] == "Preserve the API"
    assert assignment["description"] == first.body
    assert Execution(Workspace(store.directory)).assignment("harbor", run.id) == assignment


def test_adopting_milestone_revision_changes_task_agreement_atomically(tmp_path):
    store = fixture(tmp_path).workspace
    milestones = Milestones(store)
    initial = milestones.create(
        "harbor", MilestoneCreate(title="Outcome", acceptance_criteria="First")
    )
    first = attach(store, initial)
    milestones.edit(
        "harbor", initial.id, MilestoneEdit(expected_revision=1, acceptance_criteria="Second")
    )
    with pytest.raises(ApplicationError, match="current stages"):
        store.publish_task(
            "harbor", first.id, TaskPublish(expected_revision=first.revision, completion="report")
        )
    unchanged = store.task("harbor", first.id)
    assert unchanged.agreement_revision == first.agreement_revision
    adopted = prepare(store)
    assert adopted.agreement_revision == first.agreement_revision + 1
    assert adopted.publication.milestone.revision == 2 and adopted.milestone_update is None
    assert milestones.impact("harbor", initial.id)["items"] == []
    assert prepare(store) == adopted


def test_milestone_changes_preserve_question_continuations(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    milestones = Milestones(store)
    milestone = milestones.create(
        "harbor", MilestoneCreate(title="Outcome", acceptance_criteria="First")
    )
    attach(store, milestone)
    run, q = question(execution)
    original = execution.assignment("harbor", run.id)
    milestones.edit(
        "harbor", milestone.id, MilestoneEdit(expected_revision=1, acceptance_criteria="Second")
    )
    answer(Questions(store), q)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=RESULT)
    successor = execution.claim("harbor", BASE, {RESULT: set()})
    continued = execution.assignment("harbor", successor.id)
    for section in ("milestone", "milestone_criteria", "milestone_agreement"):
        assert continued[section] == original[section]


def test_sources_guard_scope_deletion_and_task_overrides(tmp_path):
    store = fixture(tmp_path).workspace
    other = Workspaces(store).create(WorkspaceCreate(name="Other"))
    docs = Documents(store)
    foreign = docs.create(other.id, DocumentCreate(title="Private", body="Unrelated"))
    with pytest.raises(ApplicationError, match="not found"):
        Milestones(store).create(
            "harbor", MilestoneCreate(title="Bad", documents=[{"id": foreign.id}])
        )
    doc = docs.create("harbor", DocumentCreate(title="Rules", body="First"))
    milestone = Milestones(store).create(
        "harbor", MilestoneCreate(title="Outcome", documents=[{"id": doc.id}])
    )
    with pytest.raises(ApplicationError, match="milestone sources"):
        docs.delete("harbor", doc.id, Edit(expected_revision=1))
    docs.edit("harbor", doc.id, DocumentEdit(expected_revision=1, body="Second"))
    attach(store, milestone)
    task = store.task("harbor", "task-0")
    store.edit_task(
        "harbor",
        task.id,
        TaskEdit(
            expected_revision=task.revision,
            documents=[{"id": doc.id, "revision": 2, "purpose": "reference"}],
        ),
    )
    prepared = prepare(store)
    selected = next(item for item in prepared.publication.documents if item.id == doc.id)
    assert selected.revision == 2 and selected.purpose == "reference"
    assert Milestones(store).get("harbor", milestone.id).documents[0].revision == 1


def test_tools_read_history_and_criteria_without_expanding_worker_authority(tmp_path):
    store = fixture(tmp_path).workspace
    milestone = Milestones(store).create(
        "harbor", MilestoneCreate(title="Outcome", acceptance_criteria="🦉" * 9000)
    )
    attach(store, milestone)

    async def check():
        grant = await workspace_scope(Supervisor(store), "harbor", author="coordinator:fixture")
        edited = await grant.call(
            "edit_milestone",
            {
                "milestone_id": milestone.id,
                "changes": {"expected_revision": 1, "acceptance_criteria": "New"},
            },
        )
        assert not edited.isError
        first = await grant.call(
            "get_milestone_text",
            {"identity": milestone.id, "revision": 1, "field": "acceptance_criteria"},
        )
        assert first.structuredContent["text"] == "🦉" * 4000
        assert first.structuredContent["next_offset"] == 4000
        page = await grant.call(
            "get_milestone_history", {"milestone_id": milestone.key, "limit": 1}
        )
        assert page.structuredContent["next_cursor"] == 2
        impact = await grant.call("get_milestone_impact", {"milestone_id": milestone.id})
        assert impact.structuredContent["items"][0]["prepared_revision"] == 1
        wrong = await grant.call(
            "get_milestone", {"milestone_id": milestone.id, "workspace_id": "other"}
        )
        assert wrong.isError
        grant.revoke()

    asyncio.run(check())
    assert not {item["name"] for item in worker_tools()} & {
        "get_milestone",
        "edit_milestone",
        "get_milestone_history",
    }


def test_upgrade_freezes_effective_source_without_inventing_old_history(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 68)
        )
        execution = fixture(tmp_path)
        store = execution.workspace
        milestone = Milestones(store).create(
            "harbor", MilestoneCreate(title="Original", body="First")
        )
        attach(store, milestone)
        run = execution.claim("harbor", BASE, {BASE: set()})
        frozen = execution.assignment("harbor", run.id)
        Milestones(store).edit(
            "harbor", milestone.id, MilestoneEdit(expected_revision=1, body="At upgrade")
        )
    upgraded = Workspace(store.directory)
    binding = upgraded.task("harbor", "task-0").publication.milestone
    assert binding.revision == 2 and binding.origin == "upgrade"
    assert Execution(upgraded).assignment("harbor", run.id) == frozen
    assert [
        item["revision"] for item in Milestones(upgraded).history("harbor", milestone.id)["items"]
    ] == [2]
    with pytest.raises(ApplicationError, match="not retained"):
        Milestones(upgraded).get("harbor", milestone.id, 1)
    with upgraded.connection() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_failed_agreement_upgrade_restores_original_state(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 68)
        )
        store = fixture(tmp_path).workspace
        milestone = Milestones(store).create("harbor", MilestoneCreate(title="Retain me"))
        attach(store, milestone)
    before = logical_data(store.directory)
    migration = next(m for m in migrations.MIGRATIONS if m.version == 68)

    def fail(db):
        migration.apply(db)
        raise ValueError("Interrupted milestone upgrade")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(68, fail) if m.version == 68 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(store.directory)
    assert logical_data(store.directory) == before
