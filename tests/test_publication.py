"""Publication checks current intent without conflating it with priority or execution."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from project_fixtures import adopt, task_request

from flowfield.activity import ActivityCreate
from flowfield.application import (
    ProjectSetup,
    TaskEdit,
    TaskPriority,
    TaskProgress,
    TaskPublish,
    TaskReconcile,
    Workspace,
)
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.questions import QuestionAnswer, QuestionApply, QuestionCreate, Questions
from flowfield.reads import ContextReads


def setup(tmp_path: Path) -> Workspace:
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(tmp_path / "harbor")))
    for identity in ("catalog", "export"):
        workspace.create_task(
            "harbor",
            task_request(
                id=identity,
                title=identity,
                status="up_next",
                body="Write an offline CSV export. Test Unicode and quoted fields.",
            ),
        )
    return workspace


def publish(workspace: Workspace, identity: str = "export"):
    current = workspace.task("harbor", identity)
    return workspace.publish_task(
        "harbor",
        identity,
        TaskPublish(
            completion="report",
            expected_revision=current.revision,
            author="coordinator",
        ),
    )


def test_publication_is_separate_from_dependencies_priority_and_notes(tmp_path: Path) -> None:
    workspace = setup(tmp_path)
    workspace.edit_task("harbor", "export", TaskEdit(expected_revision=1, dependencies=["HAR-1"]))
    checked = publish(workspace)
    assert checked.publication_status == "published" and checked.readiness == "blocked"
    assert checked.status == "up_next" and checked.publication.author == "coordinator"
    workspace.add_activity(
        "harbor", ActivityCreate(task_id="export", body="Observed a slow export.")
    )
    labeled = workspace.edit_task(
        "harbor",
        "export",
        TaskEdit(expected_revision=checked.revision, labels=["bug"], title="CSV export"),
    )
    moved = workspace.prioritize_task(
        "harbor", "export", TaskPriority(expected_revision=labeled.revision, status="backlog")
    )
    assert moved.publication == checked.publication and moved.publication_status == "published"
    changed = workspace.edit_task(
        "harbor",
        "export",
        TaskEdit(expected_revision=moved.revision, body="Export HTML instead. Test escaping."),
    )
    assert changed.publication_status == "draft"
    assert changed.status == moved.status
    assert changed.position == moved.position
    assert changed.agreement_revision == checked.agreement_revision + 1
    refreshed = publish(workspace)
    assert publish(workspace) == refreshed  # identical check does not manufacture another revision
    restored = Workspace(tmp_path / "state").task("harbor", "export")
    assert restored.publication == refreshed.publication
    assert ContextReads(workspace).task("harbor", "HAR-2")["publication_status"] == "published"
    assert BrowserReads(workspace).task("harbor", "HAR-2").publication_status == "published"


def test_questions_must_be_applied_and_edits_race_with_publication(tmp_path: Path) -> None:
    workspace = setup(tmp_path)
    questions = Questions(workspace)
    q = questions.ask(
        "harbor",
        QuestionCreate(
            task_id="export",
            question="Which rows?",
            context="The Description does not say.",
            recommendation="All filtered rows.",
            blocking_scope="Export scope.",
        ),
    )
    questions.answer(
        "harbor", q.id, QuestionAnswer(expected_revision=1, answer="All filtered rows.")
    )
    with pytest.raises(ApplicationError, match="blocking questions"):
        publish(workspace)
    questions.apply(
        "harbor",
        q.id,
        QuestionApply(
            expected_revision=2,
            expected_task_revision=1,
            body="Export all filtered rows to CSV; test Unicode and quoting.",
            decision="All filtered rows.",
        ),
    )
    current = workspace.task("harbor", "export")

    def change(which: str):
        try:
            if which == "publish":
                return workspace.publish_task(
                    "harbor",
                    "export",
                    TaskPublish(
                        completion="report",
                        expected_revision=current.revision,
                    ),
                )
            return workspace.edit_task(
                "harbor",
                "export",
                TaskEdit(
                    expected_revision=current.revision, body="Add a 10,000-row cap; test overflow."
                ),
            )
        except ApplicationError as error:
            assert error.code == "revision_conflict"
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(change, ["publish", "edit"]))
    assert sum(result is not None for result in results) == 1


def test_active_work_requires_reconciliation_without_moving_its_card(tmp_path: Path) -> None:
    workspace = setup(tmp_path)
    checked = publish(workspace)
    active = workspace.record_progress(
        "harbor", "export", TaskProgress(expected_revision=checked.revision, status="in_progress")
    )
    changed = workspace.edit_task(
        "harbor",
        "export",
        TaskEdit(expected_revision=active.revision, body="Export HTML; verify escaped markup."),
    )
    assert changed.status == "in_progress" and changed.readiness == "needs_reconciliation"
    assert changed.status_changed_at == active.status_changed_at
    with pytest.raises(ApplicationError):
        workspace.record_progress(
            "harbor",
            "export",
            TaskProgress(expected_revision=changed.revision, status="done", completion="report"),
        )
    reconciled = workspace.reconcile_task(
        "harbor",
        "export",
        TaskReconcile(
            expected_revision=changed.revision,
            note="Reviewed the changed assignment and remaining work.",
        ),
    )
    assert reconciled.status == "in_progress" and reconciled.publication_status == "published"


def test_project_answer_updates_a_completed_dependency_chain_atomically(tmp_path: Path) -> None:
    from flowfield.questions import QuestionTaskUpdate

    workspace = setup(tmp_path)
    workspace.edit_task("harbor", "export", TaskEdit(expected_revision=1, dependencies=["catalog"]))
    questions = Questions(workspace)
    question = questions.ask(
        "harbor",
        QuestionCreate(
            affected_task_ids=["catalog", "export"],
            question="Handle extra fields?",
            context="Both tasks need the same convention.",
            recommendation="Preserve them.",
        ),
    )
    for identity in ("catalog", "export"):
        task = publish(workspace, identity)
        workspace.record_progress(
            "harbor",
            identity,
            TaskProgress(expected_revision=task.revision, status="done", completion="report"),
        )
    questions.answer(
        "harbor", question.id, QuestionAnswer(expected_revision=1, answer="Preserve extra fields.")
    )
    before = {identity: workspace.task("harbor", identity) for identity in ("catalog", "export")}
    applied = questions.apply(
        "harbor",
        question.id,
        QuestionApply(
            expected_revision=2,
            expected_project_revision=1,
            task_updates=[
                QuestionTaskUpdate(
                    task_id=identity,
                    expected_revision=task.revision,
                    body=task.body + " Preserve extra fields.",
                )
                for identity, task in before.items()
            ],
            decision="Preserve extra fields.",
        ),
    )
    for identity in ("catalog", "export"):
        current = workspace.task("harbor", identity)
        assert current.revision == before[identity].revision + 1
        assert applied.applied_task_revisions[identity] == current.revision
        assert current.readiness == "needs_reconciliation" and current.status == "done"


def test_changed_selected_assignment_requires_republication_before_progress(tmp_path: Path) -> None:
    workspace = setup(tmp_path)
    original = publish(workspace)
    changed = workspace.edit_task(
        "harbor",
        "export",
        TaskEdit(expected_revision=original.revision, body="Export only filtered rows."),
    )
    assert changed.publication_status == "draft"
    assert changed.status == "up_next" and changed.position == original.position
    with pytest.raises(ApplicationError, match="publish"):
        workspace.record_progress(
            "harbor",
            "export",
            TaskProgress(expected_revision=changed.revision, status="in_progress"),
        )
    published = publish(workspace)
    running = workspace.record_progress(
        "harbor", "export", TaskProgress(expected_revision=published.revision, status="in_progress")
    )
    assert running.status == "in_progress" and running.publication_status == "published"
