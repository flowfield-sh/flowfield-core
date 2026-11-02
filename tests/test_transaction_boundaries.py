"""External work releases SQLite while exact ownership and crash evidence survive."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from output_fixtures import prepared
from result_fixtures import current, ready
from workspace_fixtures import TEST_AGENTS

from flowfield.adapters import git_integration
from flowfield.application import ProjectSetup, TaskEdit, Workspace
from flowfield.documents import DocumentEdit, Documents
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction, WorkerResult
from flowfield.integration_models import IntegrationConfig
from flowfield.output_models import OutputSelection
from flowfield.outputs import Outputs
from flowfield.workspaces import WorkspaceCreate, WorkspaceEdit, Workspaces


def unrelated_edit(store):
    owner = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Other workspace"))

    def edit():
        saved = Workspaces(store).edit(
            owner.id, WorkspaceEdit(expected_revision=owner.revision, name="Saved while busy")
        )
        assert saved.name == "Saved while busy"

    return edit


def interleave(monkeypatch, owner, method, operation, concurrent):
    entered, release = Event(), Event()
    original = getattr(owner, method)

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(10), "external work was not released"
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, method, paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(operation)
        try:
            assert entered.wait(10), "external boundary was not reached"
            pool.submit(concurrent).result(timeout=3)
        finally:
            release.set()
        return work.result(timeout=10)


def test_delivery_releases_writer_and_preserves_reserved_authority(tmp_path, monkeypatch):
    from flowfield.guidance_reconciliation import prepare

    service, _, run = ready(tmp_path)
    approved = current(service)
    edit = unrelated_edit(service.workspace)

    def during():
        edit()
        task = service.workspace.task("harbor", run.task_id)
        with pytest.raises(ApplicationError, match="working on this task"):
            service.workspace.edit_task(
                "harbor", task.id, TaskEdit(expected_revision=task.revision, title="Changed")
            )
        settings = service.integrations.settings("harbor")
        with pytest.raises(ApplicationError) as busy:
            service.integrations.configure(
                "harbor",
                IntegrationConfig(
                    expected_revision=settings.revision,
                    target_branch=settings.target_branch,
                ),
            )
        assert busy.value.code == "integration_busy"
        docs = Documents(service.workspace)
        guidance = docs.get("harbor", "guidance")
        docs.edit(
            "harbor",
            "guidance",
            DocumentEdit(
                expected_revision=guidance.revision,
                body="Guidance for subsequent work.",
            ),
        )
        assert prepare(service.review_execution.reviews, "harbor", task.id) is None
        service.results.process("harbor")  # A competing scheduler cannot take the Git lock.
        assert current(service).status == "delivering"

    interleave(
        monkeypatch, git_integration, "apply", lambda: service.results.process("harbor"), during
    )
    delivered = current(service)
    assert delivered.status == "delivered", delivered.problem
    assert delivered.approved_at == approved.approved_at
    assert delivered.candidate_commit == approved.candidate_commit


@pytest.mark.parametrize("create", [False, True])
def test_destination_git_work_releases_writer(tmp_path, monkeypatch, create):
    service, _, _ = ready(tmp_path)
    settings = service.integrations.settings("harbor")
    import flowfield.integration as integration

    interleave(
        monkeypatch,
        integration if create else git_integration,
        "git" if create else "target",
        lambda: service.integrations.configure(
            "harbor",
            IntegrationConfig(
                expected_revision=settings.revision,
                target_branch="new-destination" if create else settings.target_branch,
                create_from="main" if create else None,
            ),
        ),
        unrelated_edit(service.workspace),
    )


@pytest.mark.parametrize("change", ["none", "stop", "duplicate"])
def test_capture_releases_writer_and_rechecks_submission(tmp_path, monkeypatch, change):
    execution, run, output, owner = prepared(tmp_path)
    (output / "proof.txt").write_text("retained proof")
    edit = unrelated_edit(execution.workspace)
    report = WorkerResult(
        summary="Complete", checks="Checked", files=[OutputSelection(path="proof.txt")]
    )
    original = Outputs._copy

    def during():
        edit()
        Outputs(execution.workspace).recover()  # Must not reap an active capture.
        if change == "stop":
            latest = execution.get(owner, run.id)
            execution.stop_requested(owner, run.id, RunAction(expected_revision=latest.revision))
        elif change == "duplicate":
            with monkeypatch.context() as patch:
                patch.setattr(Outputs, "_copy", original)
                execution.report_result(owner, run.id, report)

    def submit():
        return interleave(
            monkeypatch,
            Outputs,
            "_copy",
            lambda: execution.report_result(owner, run.id, report),
            during,
        )

    if change == "none":
        submit()
    else:
        with pytest.raises(ApplicationError) as closed:
            submit()
        assert closed.value.code == "worker_scope_closed"
    files = Outputs(execution.workspace).page(owner).items
    assert len(files) == (0 if change == "stop" else 1)
    retained = execution.workspace.directory / "workspaces" / owner / "outputs"
    assert {p.name for p in retained.iterdir()} == {file.id for file in files}
    if files:
        assert Outputs(execution.workspace).content(files[0]).read_text() == "retained proof"


def test_capture_keeps_committed_files_when_notification_fails(tmp_path):
    execution, run, output, owner = prepared(tmp_path)
    (output / "proof.txt").write_text("committed")

    def fail(_):
        raise RuntimeError("notification failed after commit")

    execution.workspace.on_change = fail
    with pytest.raises(RuntimeError, match="notification failed"):
        execution.report_result(
            owner,
            run.id,
            WorkerResult(
                summary="Saved",
                checks="Checked",
                files=[OutputSelection(path="proof.txt")],
            ),
        )
    file = Outputs(execution.workspace).page(owner).items[0]
    assert Outputs(execution.workspace).content(file).read_text() == "committed"


def test_recovery_unlinks_outside_database_transaction(tmp_path, monkeypatch):
    import flowfield.outputs as outputs

    execution, _, _, owner = prepared(tmp_path)
    edit = unrelated_edit(execution.workspace)
    root = execution.workspace.directory / "workspaces" / owner / "outputs"
    root.mkdir(parents=True, exist_ok=True)
    orphan = root / ("e" * 32)
    orphan.write_text("abandoned")
    interleave(
        monkeypatch, outputs.os, "unlink", lambda: Outputs(execution.workspace).recover(), edit
    )
    assert not orphan.exists()


def test_adoption_publishes_config_outside_writer(tmp_path, monkeypatch):
    import flowfield.application as application

    store = Workspace(tmp_path / "state")
    edit = unrelated_edit(store)
    owner = Workspaces(store).page()["items"][0]["id"]
    repository = tmp_path / "adopted"
    repository.mkdir()
    project = interleave(
        monkeypatch,
        application,
        "write_config",
        lambda: store.setup_project(ProjectSetup(path=str(repository)), workspace_id=owner),
        edit,
    )
    assert store.project(project.id) == project
    assert (repository / ".flowfield/config.toml").exists()


def test_retry_git_reads_release_writer(tmp_path, monkeypatch):
    service, repo, _ = ready(tmp_path)
    dirty = repo / "untracked.txt"
    dirty.write_text("local edit")
    service.results.process("harbor")
    blocked = current(service)
    assert blocked.problem_code == "checkout_dirty"
    dirty.unlink()
    resumed = interleave(
        monkeypatch,
        git_integration,
        "target",
        lambda: service.results.retry_delivery(
            "harbor", blocked.id, RunAction(expected_revision=blocked.revision)
        ),
        unrelated_edit(service.workspace),
    )
    assert resumed.status == "delivering" and resumed.approved_at == blocked.approved_at


def test_branch_survives_interrupted_settings_save_and_can_be_selected(tmp_path, monkeypatch):
    service, repo, _ = ready(tmp_path)
    settings = service.integrations.settings("harbor")
    request = IntegrationConfig(
        expected_revision=settings.revision,
        target_branch="reserved-branch",
        create_from="main",
    )

    def fail(*_):
        raise RuntimeError("settings save failed")

    with monkeypatch.context() as patch:
        patch.setattr(service.integrations, "_invalidate_availability", fail)
        with pytest.raises(RuntimeError, match="settings save failed"):
            service.integrations.configure("harbor", request)
    branch = git_integration.target(repo, request.target_branch)
    assert service.integrations.settings("harbor") == settings
    saved = service.integrations.configure(
        "harbor", request.model_copy(update={"create_from": None})
    )
    assert saved.target_branch == request.target_branch
    assert git_integration.target(repo, request.target_branch) == branch


@pytest.mark.parametrize("change", ["none", "stop", "duplicate"])
def test_review_capture_rechecks_owner_and_preserves_decision(tmp_path, monkeypatch, change):
    import asyncio

    from review_execution_fixtures import install, round_trip
    from task_reviews_fixtures import configured

    service, _, run, _, reviews = configured(tmp_path)
    install(monkeypatch, "request_verification")
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.verification
    review = reviews.claim("harbor", review.id, review.revision)
    review = reviews.begin_attempt("harbor", review.id)
    review = reviews.started("harbor", review.id, review.revision)
    runtime = tmp_path / "verification"
    (runtime / "output").mkdir(parents=True)
    (runtime / "output/proof.txt").write_text("review proof")
    reviews.save_attempt(review.attempt_id, {"runtime": str(runtime)})
    decision = {
        "action": "answer",
        "summary": "Verified",
        "evidence": "Proof",
        "files": [{"path": "proof.txt"}],
    }
    edit = unrelated_edit(service.workspace)
    original = Outputs._copy

    def submit():
        reviews.save_decision("harbor", review.id, review.attempt_id, decision)

    def during():
        edit()
        if change == "stop":
            reviews.stop("harbor", review.id, review.revision, cleanup_confirmed=True)
        elif change == "duplicate":
            with monkeypatch.context() as patch:
                patch.setattr(Outputs, "_copy", original)
                submit()

    if change == "stop":
        with pytest.raises(ApplicationError) as stopped:
            interleave(monkeypatch, Outputs, "_copy", submit, during)
        assert stopped.value.code == "review_closed"
    else:
        interleave(monkeypatch, Outputs, "_copy", submit, during)
    files = Outputs(service.workspace).page("harbor").items
    assert len(files) == (0 if change == "stop" else 1)
    data = reviews.attempt(review.attempt_id)
    if files:
        assert data["output_ids"] == [files[0].id]
        assert data["decision"] == decision
        assert Outputs(service.workspace).content(files[0]).read_text() == "review proof"
    else:
        assert not data.get("decision")


def test_handoff_selection_releases_writer(tmp_path, monkeypatch):
    from coordinator_history_fixtures import saved_exchange
    from execution_fixtures import fixture

    import flowfield.coordinator_store as coordinator_store

    store = fixture(tmp_path).workspace
    saved_exchange(store, "Prior context")
    turn = saved_exchange(store, "Next request")
    result = interleave(
        monkeypatch,
        coordinator_store,
        "select",
        lambda: coordinator_store.CoordinatorStore(store).handoff(
            "harbor",
            turn.id,
            budget=10000,
            record=True,
        ),
        unrelated_edit(store),
    )
    assert result["recent_conversation"][0]["human"] == "Prior context"
