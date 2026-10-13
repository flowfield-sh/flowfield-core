"""Whole-outcome evidence and exact human review; deterministic report-task execution."""

import json

import pytest
from project_fixtures import task_request
from test_execution import BASE, RESULT, fixture, result, validate_report
from test_worker_documents import prepare

from flowfield.application import MilestoneCreate, MilestoneEdit, TaskEdit, Workspace
from flowfield.attention import attention_page, workspace_attention_counts
from flowfield.errors import ApplicationError
from flowfield.execution_models import WorkerResult
from flowfield.milestone_verification import MilestoneVerifications
from flowfield.milestones import Milestones
from flowfield.verification_models import (
    VerificationPrepare,
    VerificationReport,
    VerificationReview,
)


def setup(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    milestone = Milestones(store).create(
        "harbor",
        MilestoneCreate(
            title="Discovery",
            acceptance_criteria="- Discover the harness.\n- Explain setup errors.",
        ),
    )
    task = store.task("harbor", "task-0")
    store.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, milestone_id=milestone.id)
    )
    prepare(store)
    result(execution)
    verifier = store.create_task(
        "harbor",
        task_request(
            title="Verify discovery",
            body="Check the full milestone outcome",
            dependencies=["task-0"],
            milestone_id=milestone.id,
            status="up_next",
        ),
    )
    records = MilestoneVerifications(store)
    verification = records.prepare(
        "harbor",
        milestone.id,
        VerificationPrepare(
            expected_revision=1,
            project_id="harbor",
            task_id=verifier.id,
            expected_task_revision=verifier.revision,
            checks=["Discover the harness", "Explain setup errors"],
        ),
    )
    return execution, records, verification


def complete(execution, verification, *, verdict="passed", coverage="complete", omit=False):
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run and run.task_id == verification.task_id
    frozen = json.loads(execution.assignment("harbor", run.id)["milestone_verification"])
    assert frozen["id"] == verification.id
    execution.started("harbor", run.id)
    report = VerificationReport(
        verification_id=verification.id,
        findings=[
            {"check": i, "verdict": verdict, "evidence": "Observed the fixture result"}
            for i in ([1] if omit else [1, 2])
        ],
        coverage=coverage,
        coverage_notes="Reviewed both requirements, including setup failure.",
    )
    submission = WorkerResult(
        summary="Verification completed",
        limitations="Synthetic fixtures only",
        checks="Discovery and setup exercised",
        verification=report,
    )
    execution.report_result("harbor", run.id, submission)
    execution.finish("harbor", run.id, "in_review", result=submission, commit=RESULT)
    validate_report(execution)
    return run


def test_passing_report_opens_one_review_and_acceptance_survives_restart(tmp_path):
    execution, records, verification = setup(tmp_path)
    complete(execution, verification)
    value = records.get("harbor", verification.id)
    assert value.status == "ready"
    assert value.limitations == "Synthetic fixtures only"
    assert execution.workspace.task("harbor", verification.task_id).status == "done"
    assert [
        i.id
        for i in attention_page(execution.workspace, None, "action", workspace_id="harbor").items
        if i.kind == "milestone"
    ] == [value.id]
    with execution.workspace.connection() as db:
        assert workspace_attention_counts(db, "harbor")["action"] == 1
    accepted = records.review(
        "harbor", value.id, VerificationReview(expected_revision=value.revision, action="accept")
    )
    assert accepted.status == "accepted" and accepted.reviewed_by == "human"
    restarted = MilestoneVerifications(Workspace(execution.workspace.directory))
    assert restarted.get("harbor", value.id) == accepted
    assert not [
        i
        for i in attention_page(execution.workspace, None, "action", workspace_id="harbor").items
        if i.kind == "milestone"
    ]
    with pytest.raises(ApplicationError):
        records.review(
            "harbor",
            value.id,
            VerificationReview(expected_revision=value.revision, action="accept"),
        )


@pytest.mark.parametrize(
    "verdict,coverage",
    [("failed", "complete"), ("unverified", "complete"), ("passed", "incomplete")],
)
def test_successful_verifier_is_not_a_passed_milestone(tmp_path, verdict, coverage):
    execution, records, verification = setup(tmp_path)
    complete(execution, verification, verdict=verdict, coverage=coverage)
    value = records.get("harbor", verification.id)
    assert value.status == "failed"
    assert execution.workspace.task("harbor", verification.task_id).status == "done"
    with pytest.raises(ApplicationError, match="Only current passing"):
        records.review(
            "harbor",
            value.id,
            VerificationReview(expected_revision=value.revision, action="accept"),
        )


def test_missing_check_is_rejected_at_worker_boundary(tmp_path):
    execution, _, verification = setup(tmp_path)
    with pytest.raises(ApplicationError, match="exactly once"):
        complete(execution, verification, omit=True)


@pytest.mark.parametrize("accepted", [False, True])
def test_changed_criteria_invalidates_current_evidence_but_preserves_receipt(tmp_path, accepted):
    execution, records, verification = setup(tmp_path)
    complete(execution, verification)
    value = records.get("harbor", verification.id)
    if accepted:
        value = records.review(
            "harbor",
            value.id,
            VerificationReview(expected_revision=value.revision, action="accept"),
        )
    Milestones(execution.workspace).edit(
        "harbor",
        value.milestone_id,
        MilestoneEdit(expected_revision=1, acceptance_criteria="Also support another case"),
    )
    changed = records.get("harbor", value.id)
    assert not changed.current
    assert changed.status == ("accepted" if accepted else "stale")
    assert changed.criteria == value.criteria and changed.report == value.report
    with pytest.raises(ApplicationError):
        records.review(
            "harbor",
            value.id,
            VerificationReview(expected_revision=value.revision, action="accept"),
        )


def test_request_changes_is_retained_without_reopening_unrelated_work(tmp_path):
    execution, records, verification = setup(tmp_path)
    complete(execution, verification)
    value = records.get("harbor", verification.id)
    with pytest.raises(ApplicationError, match="Describe"):
        records.review(
            "harbor",
            value.id,
            VerificationReview(expected_revision=value.revision, action="request_changes"),
        )
    changed = records.review(
        "harbor",
        value.id,
        VerificationReview(
            expected_revision=value.revision,
            action="request_changes",
            note="The cold setup case was omitted",
        ),
    )
    assert changed.review_note == "The cold setup case was omitted"
    assert (
        records.history("harbor", value.milestone_id)["items"][0]["status"] == "changes_requested"
    )
    assert execution.workspace.task("harbor", "task-0").status == "done"


def test_new_archived_unfinished_task_invalidates_evidence(tmp_path):
    execution, records, verification = setup(tmp_path)
    complete(execution, verification)
    task = execution.workspace.create_task(
        "harbor",
        task_request(
            title="Missing case", body="A required outcome", milestone_id=verification.milestone_id
        ),
    )
    execution.workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, archived=True)
    )
    assert records.get("harbor", verification.id).status == "stale"


def test_empty_milestone_cannot_verify_itself(tmp_path):
    execution = fixture(tmp_path, count=0)
    store = execution.workspace
    milestone = Milestones(store).create(
        "harbor", MilestoneCreate(title="Empty", acceptance_criteria="Works")
    )
    task = store.create_task(
        "harbor", task_request(title="Verifier", body="Verify", milestone_id=milestone.id)
    )
    with pytest.raises(ApplicationError, match="contributing tasks"):
        MilestoneVerifications(store).prepare(
            "harbor",
            milestone.id,
            VerificationPrepare(
                expected_revision=1,
                project_id="harbor",
                task_id=task.id,
                expected_task_revision=task.revision,
                checks=["Works"],
            ),
        )


def test_changed_inputs_block_unstarted_verifier(tmp_path):
    execution, records, verification = setup(tmp_path)
    Milestones(execution.workspace).edit(
        "harbor",
        verification.milestone_id,
        MilestoneEdit(expected_revision=1, body="Changed scope"),
    )
    task = execution.workspace.task("harbor", verification.task_id)
    assert task.readiness == "blocked"
    assert "new milestone verification" in task.preparation_issue
    assert execution.claim("harbor", BASE, {BASE: set()}) is None


def test_reopened_contribution_invalidates_review(tmp_path):
    execution, records, verification = setup(tmp_path)
    complete(execution, verification)
    task = execution.workspace.task("harbor", "task-0")
    execution.workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(expected_revision=task.revision, body="Also cover the missing case"),
    )
    assert records.get("harbor", verification.id).status == "stale"


def test_schema_68_upgrade_and_failure_recovery(tmp_path, monkeypatch):
    from test_storage_migrations import logical_data

    from flowfield import migrations
    from flowfield.migrations import Migration

    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 69)
        )
        store = fixture(tmp_path).workspace
    before = logical_data(store.directory)
    migration = next(m for m in migrations.MIGRATIONS if m.version == 69)

    def fail(db):
        migration.apply(db)
        raise ValueError("Interrupted verification upgrade")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(69, fail) if m.version == 69 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(store.directory)
    assert logical_data(store.directory) == before
    upgraded = Workspace(store.directory)
    with upgraded.connection() as db:
        assert db.execute("SELECT count(*) FROM milestone_verifications").fetchone()[0] == 0
    assert upgraded.task("harbor", "task-0").publication.verification_id is None


def test_duplicate_delivery_announces_review_once_and_scopes_reads(tmp_path):
    execution, records, verification = setup(tmp_path)
    complete(execution, verification)
    validate_report(execution)
    with execution.workspace.connection(write=True) as db:
        db.execute("UPDATE storage_metadata SET revision=revision+1")
    with execution.workspace.connection() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM announcement_events WHERE source_key=?",
                (f"workspace:harbor:milestone:{verification.id}",),
            ).fetchone()[0]
            == 1
        )
    with pytest.raises(ApplicationError, match="not found"):
        records.get("another-workspace", verification.id)


def test_coordinator_can_read_verification_but_worker_cannot_accept(tmp_path):
    import asyncio

    from flowfield.agent_tools import workspace_scope
    from flowfield.supervisor import Supervisor
    from flowfield.worker_tools import worker_tools

    execution, records, verification = setup(tmp_path)

    async def exercise():
        grant = await workspace_scope(Supervisor(execution.workspace), "harbor")
        result = await grant.call(
            "get_milestone_verification", {"verification_id": verification.id}
        )
        assert not result.isError
        assert result.structuredContent["id"] == verification.id
        assert "review_milestone" not in grant.tools
        assert "prepare_milestone_verification" in grant.tools
        assert (
            "workspace_id"
            not in grant.tools["prepare_milestone_verification"].inputSchema["properties"]
        )
        assert not any("milestone" in tool["name"] for tool in worker_tools())

    asyncio.run(exercise())


def test_cross_project_contributions_are_frozen_as_scoped_prerequisites(tmp_path):
    from pathlib import Path
    from unittest.mock import patch

    from project_fixtures import fixture_stage_change

    from flowfield.application import ProjectSetup, TaskPreparation
    from flowfield.results import Results
    from flowfield.workspaces import Workspaces

    execution, records, previous = setup(tmp_path)
    store = execution.workspace
    other = Path(tmp_path) / "client"
    other.mkdir()
    Workspaces(store).adopt("harbor", ProjectSetup(path=str(other), task_prefix="CLI"))
    contribution = store.create_task(
        "client",
        task_request(
            title="Client discovery",
            body="Client contribution",
            milestone_id=previous.milestone_id,
            status="up_next",
            preparation=TaskPreparation(completion="report"),
        ),
    )
    run = execution.claim("client", BASE, {BASE: set()})
    assert run
    execution.started("client", run.id)
    execution.finish(
        "client",
        run.id,
        "in_review",
        result=WorkerResult(summary="Client behavior delivered", checks="Client fixture passed"),
        commit=RESULT,
    )
    with patch("flowfield.results.git", return_value=b"same-tree"):
        Results(store).process("client")
    task = store.task("harbor", previous.task_id)
    store.edit_task(
        "harbor",
        task.id,
        TaskEdit(
            expected_revision=task.revision,
            dependencies=["task-0", contribution.key],
            stages=fixture_stage_change(store, "harbor", task.id),
        ),
    )
    task = store.task("harbor", task.id)
    value = records.prepare(
        "harbor",
        previous.milestone_id,
        VerificationPrepare(
            expected_revision=1,
            project_id="harbor",
            task_id=task.id,
            expected_task_revision=task.revision,
            checks=previous.checks,
        ),
    )
    assert {t.project_id for t in value.tasks} == {"harbor", "client"}
    verifier = execution.claim("harbor", BASE, {BASE: set()})
    assert verifier
    assignment = execution.assignment("harbor", verifier.id)
    prerequisites = json.loads(assignment["prerequisites"])
    assert (
        next(p for p in prerequisites if p["project_id"] == "client")["repository_scope"]
        == "other_repository"
    )
    assert json.loads(assignment["milestone_verification"])["id"] == value.id
    assert records.get("harbor", previous.id).status == "stale"


def test_adjacent_coordinator_context_keeps_the_exact_verification(tmp_path):
    from flowfield.coordinator_context import freeze_view
    from flowfield.coordinator_models import CoordinatorViewSelection

    execution, records, verification = setup(tmp_path)
    complete(execution, verification)
    value = records.get("harbor", verification.id)
    with execution.workspace.connection() as db:
        frozen = freeze_view(
            execution.workspace,
            db,
            "harbor",
            CoordinatorViewSelection(
                surface="milestone",
                milestone_id=value.milestone_id,
                milestone_revision=1,
                verification_id=value.id,
            ),
            None,
        )
    records.review(
        "harbor", value.id, VerificationReview(expected_revision=value.revision, action="accept")
    )
    assert frozen.verification_id == value.id
    assert frozen.verification_revision == value.revision and frozen.verification_status == "ready"
