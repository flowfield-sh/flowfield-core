"""Automatic destination recovery keeps intent, queue and exact approval boundaries."""

from result_fixtures import approve, current, fixture

from flowfield.adapters.git_workspace import git
from flowfield.execution_models import QueueEdit
from flowfield.integration_models import IntegrationConfig


def advance(repo, name="parallel.txt", body="parallel change\n"):
    (repo / name).write_text(body)
    git(repo, "add", name)
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Parallel work",
    )
    return git(repo, "rev-parse", "HEAD").decode().strip()


def enable(service):
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))


def test_destination_advance_rechecks_without_transferring_approval(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    old = approve(service, current(service))
    target = advance(repo)
    service._process_delivery("harbor")
    assert current(service).id == old.id  # Paused queue only observes staleness.
    assert current(service).problem_code == "target_changed"
    enable(service)
    service._process_delivery("harbor")
    fresh = current(service)
    assert fresh.id != old.id and fresh.status == "ready"
    assert fresh.source_commit == run.result_commit and fresh.target_before == target
    assert fresh.approved_at is None and fresh.approved_by is None
    assert service.results.get("harbor", old.id).approved_at == old.approved_at
    assert git(repo, "rev-parse", "HEAD").decode().strip() == target
    service._process_delivery("harbor")
    assert current(service).id == fresh.id  # No needless duplicate preparation.


def test_advanced_destination_conflict_queues_one_bounded_correction(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    target = advance(repo, "result.txt", "concurrent implementation\n")
    enable(service)
    service._process_delivery("harbor")
    assert current(service).problem_code == "integration_conflict"
    service._process_delivery("harbor")
    corrected = current(service)
    assert corrected.status == "changes_requested"
    assert corrected.correction.source_commit == run.result_commit
    assert corrected.correction.target_commit == target
    assert corrected.approved_at is None
    assert service.workspace.task("harbor", "work").status == "up_next"
    service._process_delivery("harbor")
    assert current(service).revision == corrected.revision
    assert (
        len(service.execution.page("harbor").items) == 1
    )  # Scheduling never launches a model here.


def test_settings_changes_and_original_check_failures_do_not_auto_restart(tmp_path):
    service, _, _ = fixture(tmp_path, checks=["exit 1"])
    service.results.process("harbor")
    original = current(service)
    enable(service)
    service._process_delivery("harbor")
    assert current(service).id == original.id
    assert current(service).status == "blocked"
    settings = service.integrations.settings("harbor")
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=settings.revision, target_branch="integration", checks=["true"]
        ),
    )
    service._process_delivery("harbor")
    assert current(service).id == original.id


def test_unchanged_correction_stops_instead_of_looping(tmp_path):
    from flowfield.execution_models import WorkerResult

    service, repo, original_run = fixture(tmp_path)
    service.results.process("harbor")
    target = advance(repo, "result.txt", "concurrent implementation\n")
    enable(service)
    service._process_delivery("harbor")
    service._process_delivery("harbor")
    run = service.execution.claim("harbor", target, {target: set()})
    assert run and run.correction
    assert run.launch_reason == "correction"
    service.execution.started("harbor", run.id)
    service.execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(summary="Same code", checks="Still conflicts"),
        commit=original_run.result_commit,
    )
    service._process_delivery("harbor")
    service._process_delivery("harbor")
    blocked = current(service)
    assert blocked.problem_code == "correction_no_progress"
    assert "unchanged code" in blocked.problem
    assert blocked.next_action.action == "correct"
    service._process_delivery("harbor")
    assert current(service).revision == blocked.revision


def test_pause_during_correction_preparation_prevents_scheduling(tmp_path, monkeypatch):
    from flowfield.adapters import git_integration

    service, repo, _ = fixture(tmp_path)
    service.results.process("harbor")
    advance(repo, "result.txt", "concurrent implementation\n")
    enable(service)
    service._process_delivery("harbor")
    original = git_integration.correction_seed

    def pause(*args):
        seed = original(*args)
        settings = service.execution.settings("harbor")
        service.execution.queue(
            "harbor", QueueEdit(expected_revision=settings.revision, enabled=False)
        )
        return seed

    monkeypatch.setattr(git_integration, "correction_seed", pause)
    service._process_delivery("harbor")
    assert current(service).status == "blocked"
    assert current(service).correction is None
