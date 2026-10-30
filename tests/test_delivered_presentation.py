"""Delivery is historical; reuse eligibility still requires exact current validation."""

import pytest
from project_fixtures import task_request
from result_fixtures import approve, current, fixture
from result_recovery_fixtures import target_change

from flowfield.adapters.git_workspace import git
from flowfield.application import TaskEdit
from flowfield.attention import attention_page
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, RunAction
from flowfield.integration_models import IntegrationConfig
from flowfield.reads import ContextReads
from flowfield.thread_view import ThreadView


def assert_delivered(service, original, *, available=False):
    version = current(service)
    assert version.status == "delivered"
    assert version.next_action.label == "Delivered"
    assert version.next_action.action == "none"
    assert version.reuse_action.action == ("none" if available else "revalidate")
    for field in (
        "approved_at",
        "approved_by",
        "approval_note",
        "completed_at",
        "integration_id",
        "candidate_commit",
        "source_commit",
        "target_before",
        "settings_revision",
        "report",
    ):
        assert getattr(version, field) == getattr(original, field)
    board = BrowserReads(service.workspace).workspace_board("harbor")
    card = next(t for t in board.tasks if t.id == "work")
    assert card.status == "done"
    assert (card.state.label, card.state.tone) == ("Done", "complete")
    assert "T-1" not in board.attention_work_keys
    assert not attention_page(service.workspace, "harbor", "action").items
    feed = ThreadView(service.workspace).item_view("harbor", "work", "result:" + version.id)
    assert (feed.state.label, feed.state.tone) == ("Delivered", "complete")
    brief = ContextReads(service.workspace).task("harbor", "work")["proposed_result"]
    assert brief["next_action"] == "none"
    assert brief["code_available"] == available
    assert brief["reuse_action"]["action"] == ("none" if available else "revalidate")
    return board


@pytest.mark.parametrize("movement", ["branch", "settings"])
@pytest.mark.parametrize("dependent", [False, True])
def test_stale_delivery_stays_complete_and_refresh_releases_only_validated_code(
    tmp_path, movement, dependent
):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    approve(service, current(service))
    service.results.process("harbor")
    original = current(service)
    if dependent:
        task = service.workspace.create_task(
            "harbor",
            task_request(
                project_id="harbor",
                id="dependent",
                title="Use delivered code",
                body="Build on the prerequisite",
                dependencies=["T-1"],
                status="up_next",
            ),
        )
        service.workspace.edit_task(
            "harbor",
            task.id,
            TaskEdit(
                expected_revision=task.revision,
                completion="code",
            ),
        )
    if movement == "branch":
        target_change(service, repo, service.integrations.head("harbor", "work"))
    else:
        settings = service.integrations.settings("harbor")
        service.integrations.configure(
            "harbor",
            IntegrationConfig(
                expected_revision=settings.revision,
                runtime="local",
                target_branch="integration",
                checks=["test -f result.txt", "true"],
            ),
        )
    service.integrations.refresh_availability("harbor")
    board = assert_delivered(service, original)
    head = service.integrations.head("harbor", "work")
    if dependent:
        pending = board.pending_code["T-2"]
        assert [p.key for p in pending] == ["T-1"]
        assert pending[0].result_href.endswith("/results/" + original.id)
        assert pending[0].reuse_action.action == "revalidate"
        assert "Delivery succeeded" in pending[0].reuse_action.reason
        scoped = ContextReads(service.workspace).task("harbor", "dependent")
        assert scoped["pending_code"][0] == pending[0].model_dump()
        card = next(t for t in board.tasks if t.id == "dependent")
        assert card.state.reason == "prerequisites"
        settings = service.execution.settings("harbor")
        service.execution.queue(
            "harbor", QueueEdit(expected_revision=settings.revision, enabled=True)
        )
        with pytest.raises(ApplicationError, match="prerequisite code"):
            service.execution.claim(
                "harbor", head, service._available("harbor", "harbor", repo, head)
            )
    version = current(service)
    service.results.revalidate("harbor", version.id, RunAction(expected_revision=version.revision))
    board = assert_delivered(service, original, available=True)
    assert git(repo, "rev-parse", "integration").decode().strip() == head
    if dependent:
        assert board.pending_code["T-2"] == []
        claimed = service.execution.claim(
            "harbor", head, service._available("harbor", "harbor", repo, head)
        )
        assert claimed and claimed.task_id == "dependent"


def test_failed_refresh_and_missing_code_keep_delivery_but_block_reuse(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    approve(service, current(service))
    service.results.process("harbor")
    original = current(service)
    settings = service.integrations.settings("harbor")
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=settings.revision,
            runtime="local",
            target_branch="integration",
            checks=["exit 9"],
        ),
    )
    checked = service.results.revalidate(
        "harbor", original.id, RunAction(expected_revision=original.revision)
    )
    assert service.integrations.get("harbor", checked.availability_id).status == "failed"
    assert_delivered(service, original)
    git(repo, "update-ref", "refs/heads/integration", run.base_commit)
    service.integrations.refresh_availability("harbor")
    with pytest.raises(ApplicationError, match="lost delivered code"):
        service.results.revalidate(
            "harbor", checked.id, RunAction(expected_revision=checked.revision)
        )
    assert_delivered(service, original)


def test_report_delivery_has_no_reuse_action_and_failed_result_still_needs_attention(tmp_path):
    report, _, _ = fixture(tmp_path / "report", completion="report", changed=False)
    report.results.process("harbor")
    version = current(report)
    assert version.status == "delivered"
    assert version.reuse_action is None
    assert version.next_action.label == "Delivered"
    assert not attention_page(report.workspace, "harbor", "action").items
    failed, _, _ = fixture(tmp_path / "failed", checks=["exit 7"])
    failed.results.process("harbor")
    version = current(failed)
    assert version.status == "blocked"
    assert version.reuse_action is None
    assert attention_page(failed.workspace, "harbor", "action").items[0].state.tone == "attention"
