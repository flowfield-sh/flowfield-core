"""Shared defaults/capacity preserve task overrides and independent repository recovery."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from project_fixtures import existing_directory, fixture_stage_change, task_request
from test_execution import BASE, fixture

from flowfield import migrations, storage
from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.application import ProjectSetup, TaskPublish, Workspace
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit
from flowfield.migrations import Migration
from flowfield.questions import QuestionAnswer, QuestionCreate, Questions
from flowfield.reads import ContextReads
from flowfield.workspaces import WorkspaceCreate, Workspaces


def shared(tmp_path, *, cap=1):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    for name in ("core", "site"):
        spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / name)))
        task = store.create_task(
            name,
            task_request(
                id="work", title="Work", body="Investigate and report findings.", status="up_next"
            ),
        )
        store.publish_task(
            name,
            task.id,
            TaskPublish(
                expected_revision=task.revision,
                completion="report",
                stages=fixture_stage_change(store, name, task.id),
            ),
        )
    saved = spaces.configure_workers(
        space.id,
        SettingsEdit(expected_revision=1, selection=AgentChoice(model="shared"), max_parallel=cap),
    )
    spaces.queue(space.id, QueueEdit(expected_revision=saved.revision, enabled=True))
    return store, spaces, space.id, Execution(store)


def test_empty_workspace_has_explicit_paused_default_and_guarded_edits(tmp_path):
    spaces = Workspaces(Workspace(tmp_path / "state"))
    space = spaces.create(WorkspaceCreate(name="Product"))
    assert spaces.workers(space.id).selection is None
    assert not spaces.workers(space.id).enabled
    with pytest.raises(ApplicationError, match="Choose a worker"):
        spaces.queue(space.id, QueueEdit(expected_revision=1, enabled=True))
    with pytest.raises(ApplicationError, match="individual task"):
        spaces.configure_workers(
            space.id, SettingsEdit(expected_revision=1, selection=AgentChoice(model="x", fast=True))
        )
    assert spaces.workers(space.id).revision == 1
    with pytest.raises(ApplicationError, match="Workspace not found"):
        spaces.workers("missing")
    assert spaces.occupancy(space.id).active == 0


def test_defaults_overrides_reset_and_frozen_provenance(tmp_path):
    store, spaces, identity, execution = shared(tmp_path, cap=2)
    agents = AgentSettings(store)
    override = agents.edit(
        "core",
        "worker",
        AgentSettingsEdit(expected_revision=1, selection=AgentChoice(model="special", fast=True)),
        "work",
    )
    core = execution.claim("core", BASE, {})
    site = execution.claim("site", BASE, {})
    assert core.model == "special" and core.agent_settings.source == "override"
    assert site.model == "shared" and site.agent_settings.source == "workspace"
    saved = spaces.workers(identity)
    changed = spaces.configure_workers(
        identity,
        SettingsEdit(
            expected_revision=saved.revision, selection=AgentChoice(model="updated"), max_parallel=2
        ),
    )
    assert agents.get("core", "worker", "work").effective.choice.model == "special"
    assert agents.get("site", "worker", "work").effective.choice.model == "updated"
    reset = agents.edit(
        "core", "worker", AgentSettingsEdit(expected_revision=override.revision), "work"
    )
    assert reset.effective.source == "workspace"
    assert reset.effective.default_revision == changed.revision
    assert execution.get("core", core.id).agent_settings == core.agent_settings
    assert execution.get("site", site.id).agent_settings == site.agent_settings
    assert Execution(Workspace(store.directory)).settings("site").selection.model == "updated"


def test_competing_projects_share_capacity_and_uncertain_ownership(tmp_path):
    store, spaces, identity, execution = shared(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda p: execution.claim(p, BASE, {}), ("core", "site")))
    assert sum(run is not None for run in runs) == 1
    run = next(run for run in runs if run is not None)
    other = "site" if run.project_id == "core" else "core"
    assert spaces.occupancy(identity).active == 1
    with store.connection() as db:
        assert ContextReads(store)._worker_briefing(db, other)["occupied_slots"] == 1
    execution.finish(run.project_id, run.id, "uncertain")
    assert execution.occupancy(other).uncertain == 1
    assert execution.claim(other, BASE, {}) is None
    execution.finish(run.project_id, run.id, "stopped")
    assert execution.claim(other, BASE, {}) is not None


def test_pause_stale_edits_and_repository_problems_are_scoped(tmp_path):
    store, spaces, identity, execution = shared(tmp_path)
    revision = spaces.workers(identity).revision
    execution.queue_problem("core", "Core checkout needs repair")
    assert execution.settings("core").problem == "Core checkout needs repair"
    assert execution.settings("site").problem is None
    assert spaces.workers(identity).revision == revision
    paused = execution.queue("site", QueueEdit(expected_revision=revision, enabled=False))
    assert not execution.settings("core").enabled
    assert spaces.workers(identity).revision == paused.revision
    assert execution.claim("core", BASE, {}) is None
    assert execution.claim("site", BASE, {}) is None
    with pytest.raises(ApplicationError, match="stale"):
        spaces.queue(identity, QueueEdit(expected_revision=revision, enabled=True))
    spaces.queue(identity, QueueEdit(expected_revision=paused.revision, enabled=True))
    assert execution.claim("site", BASE, {}) is not None
    other = spaces.create(WorkspaceCreate(name="Other"))
    assert spaces.occupancy(other.id).active == 0
    assert not spaces.workers(other.id).enabled


def test_adoption_cannot_overwrite_shared_workers(tmp_path):
    store, spaces, identity, execution = shared(tmp_path)
    before = spaces.workers(identity)
    path = existing_directory(tmp_path / "extra")
    with pytest.raises(ApplicationError, match="Configure workspace workers separately"):
        store.setup_project(
            ProjectSetup(path=path, worker=AgentChoice(model="accidental")), workspace_id=identity
        )
    assert spaces.workers(identity) == before
    assert len(spaces.projects(identity)) == 2


def test_answer_delivery_observes_sibling_capacity_and_shared_pause(tmp_path):
    store, spaces, identity, execution = shared(tmp_path)
    run = execution.claim("core", BASE, {})
    execution.started("core", run.id)
    question = execution.ask_question(
        "core",
        run.id,
        QuestionCreate(
            task_id="work",
            question="Which behavior?",
            context="Clarify the result.",
            recommendation="Keep both.",
            blocking_scope="Report scope",
        ),
    )
    questions = Questions(store)
    questions.answer(
        "core",
        question.id,
        QuestionAnswer(expected_revision=question.revision, answer="Keep both."),
    )
    execution.finish("core", run.id, "waiting_for_input", input_checkpoint=BASE)
    sibling = execution.claim("site", BASE, {})
    assert sibling is not None
    assert questions.get("core", question.id).delivery.state == "Waiting for capacity"
    saved = spaces.workers(identity)
    spaces.queue(identity, QueueEdit(expected_revision=saved.revision, enabled=False))
    assert questions.get("core", question.id).delivery.state == "Paused"
    assert execution.claim("core", BASE, {}) is None


def test_failed_adoption_rolls_back_membership_and_agent_settings(tmp_path, monkeypatch):
    store, spaces, identity, execution = shared(tmp_path)
    before = spaces.workers(identity)
    path = existing_directory(tmp_path / "extra")

    def fail(*args):
        raise OSError("fixture configuration failure")

    monkeypatch.setattr("flowfield.application.write_config", fail)
    with pytest.raises(ApplicationError, match="fixture configuration failure"):
        spaces.adopt(identity, ProjectSetup(path=path))
    assert spaces.workers(identity) == before
    assert len(spaces.projects(identity)) == 2
    with store.connection() as db:
        assert not db.execute("SELECT 1 FROM agent_settings WHERE project_id='extra'").fetchone()


def test_transitional_project_cannot_silently_lose_defaults_on_adoption(tmp_path):
    store, spaces, identity, execution = shared(tmp_path)
    path = existing_directory(tmp_path / "extra")
    request = ProjectSetup(path=path, worker=AgentChoice(model="private-default"))
    store.setup_project(request)
    with pytest.raises(ApplicationError, match="registered without a workspace"):
        spaces.adopt(identity, ProjectSetup(path=path))
    assert execution.settings("extra").selection.model == "private-default"


@pytest.mark.parametrize("enabled", [True, False])
def test_migration_preserves_defaults_overrides_problem_and_frozen_attempt(
    tmp_path, monkeypatch, enabled
):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 55)
        )
        execution = fixture(tmp_path, count=2)
        agents = AgentSettings(execution.workspace)
        agents.edit(
            "harbor",
            "worker",
            AgentSettingsEdit(expected_revision=1, selection=AgentChoice(model="special")),
            "task-1",
        )
        run = execution.claim("harbor", BASE, {})
        execution.queue_problem("harbor", "Keep recovery")
        execution.queue(
            "harbor",
            QueueEdit(expected_revision=execution.settings("harbor").revision, enabled=enabled),
        )
        execution.queue_problem("harbor", "Keep recovery")
        before = execution.settings("harbor")
    store = Workspace(execution.workspace.directory)
    spaces = Workspaces(store)
    upgraded = Execution(store)
    assert upgraded.settings("harbor") == before
    assert spaces.workers("harbor").enabled == enabled
    assert spaces.workers("harbor").revision == before.revision
    assert upgraded.get("harbor", run.id) == run
    assert run.agent_settings.source == "project"
    assert (
        AgentSettings(store).get("harbor", "worker", "task-1").effective.choice.model == "special"
    )
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM worker_settings").fetchone()[0] == 0
    assert spaces.occupancy("harbor").active == 1
    assert len(storage.backups(store.directory)) == 1


def test_failed_settings_migration_restores_prior_owner(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 55)
        )
        execution = fixture(tmp_path)
        before = execution.settings("harbor")
    original = next(m for m in migrations.MIGRATIONS if m.version == 56)

    def fail(db):
        original.apply(db)
        raise ValueError("worker settings migration failure")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(56, fail) if m.version == 56 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(execution.workspace.directory)
    with storage.connect(execution.workspace.database) as db:
        assert storage.version(db) == 54
        assert db.execute("SELECT count(*) FROM worker_settings").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name='workspace_worker_settings'"
            ).fetchone()
            is None
        )
    upgraded = Execution(Workspace(execution.workspace.directory))
    assert upgraded.settings("harbor") == before
