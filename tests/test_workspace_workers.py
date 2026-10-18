"""Shared defaults/capacity preserve task overrides and independent repository recovery."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from execution_fixtures import BASE
from project_fixtures import existing_directory
from pydantic import ValidationError
from workspace_workers_fixtures import shared

from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.application import ProjectSetup, Workspace
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit
from flowfield.questions import QuestionAnswer, QuestionCreate, Questions
from flowfield.reads import ContextReads
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_empty_workspace_has_explicit_paused_default_and_guarded_edits(tmp_path):
    spaces = Workspaces(Workspace(tmp_path / "state"))
    space = spaces.create(WorkspaceCreate(name="Product"))
    assert spaces.workers(space.id).selection is None
    assert not spaces.workers(space.id).enabled
    with pytest.raises(ApplicationError, match="Choose a worker"):
        spaces.queue(space.id, QueueEdit(expected_revision=1, enabled=True))
    with pytest.raises(ApplicationError, match="task override"):
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
        identity,
        "worker",
        AgentSettingsEdit(expected_revision=1, selection=AgentChoice(model="special", fast=True)),
        "core",
    )
    core = execution.claim(identity, BASE, {}, task_id="core")
    site = execution.claim(identity, BASE, {}, task_id="site")
    assert core.model == "special" and core.agent_settings.source == "override"
    assert site.model == "shared" and site.agent_settings.source == "workspace"
    saved = spaces.workers(identity)
    changed = spaces.configure_workers(
        identity,
        SettingsEdit(
            expected_revision=saved.revision, selection=AgentChoice(model="updated"), max_parallel=2
        ),
    )
    assert agents.get(identity, "worker", "core").effective.choice.model == "special"
    assert agents.get(identity, "worker", "site").effective.choice.model == "updated"
    reset = agents.edit(
        identity, "worker", AgentSettingsEdit(expected_revision=override.revision), "core"
    )
    assert reset.effective.source == "workspace"
    assert reset.effective.default_revision == changed.revision
    assert execution.get(identity, core.id).agent_settings == core.agent_settings
    assert execution.get(identity, site.id).agent_settings == site.agent_settings
    assert Execution(Workspace(store.directory)).settings(identity).selection.model == "updated"


def test_competing_projects_share_capacity_and_uncertain_ownership(tmp_path):
    store, spaces, identity, execution = shared(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(
            pool.map(lambda p: execution.claim(identity, BASE, {}, task_id=p), ("core", "site"))
        )
    assert sum(run is not None for run in runs) == 1
    run = next(run for run in runs if run is not None)
    other = "site" if run.project_id == "core" else "core"
    assert spaces.occupancy(identity).active == 1
    with store.connection() as db:
        assert ContextReads(store)._worker_briefing(db, identity)["occupied_slots"] == 1
    execution.finish(identity, run.id, "uncertain")
    assert execution.occupancy(identity).uncertain == 1
    assert execution.claim(identity, BASE, {}, task_id=other) is None
    execution.finish(identity, run.id, "stopped")
    assert execution.claim(identity, BASE, {}, task_id=other) is not None


def test_pause_stale_edits_and_repository_problems_are_scoped(tmp_path):
    store, spaces, identity, execution = shared(tmp_path)
    execution.queue_problem(identity, "Core checkout needs repair")
    assert execution.settings(identity).problem == "Core checkout needs repair"
    revision = spaces.workers(identity).revision
    paused = execution.queue(identity, QueueEdit(expected_revision=revision, enabled=False))
    assert not execution.settings(identity).enabled
    assert spaces.workers(identity).revision == paused.revision
    assert execution.claim(identity, BASE, {}, task_id="core") is None
    assert execution.claim(identity, BASE, {}, task_id="site") is None
    with pytest.raises(ApplicationError, match="stale"):
        spaces.queue(identity, QueueEdit(expected_revision=revision, enabled=True))
    spaces.queue(identity, QueueEdit(expected_revision=paused.revision, enabled=True))
    assert execution.claim(identity, BASE, {}, task_id="site") is not None
    other = spaces.create(WorkspaceCreate(name="Other"))
    assert spaces.occupancy(other.id).active == 0
    assert not spaces.workers(other.id).enabled


def test_adoption_cannot_overwrite_shared_workers(tmp_path):
    store, spaces, identity, execution = shared(tmp_path)
    before = spaces.workers(identity)
    path = existing_directory(tmp_path / "extra")
    with pytest.raises(ValidationError, match="Extra inputs"):
        store.setup_project(
            ProjectSetup(path=path, worker=AgentChoice(model="accidental")), workspace_id=identity
        )
    assert spaces.workers(identity) == before
    assert len(spaces.projects(identity)) == 2


def test_answer_delivery_observes_sibling_capacity_and_shared_pause(tmp_path):
    store, spaces, identity, execution = shared(tmp_path)
    run = execution.claim(identity, BASE, {}, task_id="core")
    execution.started(identity, run.id)
    question = execution.ask_question(
        identity,
        run.id,
        QuestionCreate(
            task_id="core",
            question="Which behavior?",
            context="Clarify the result.",
            recommendation="Keep both.",
            blocking_scope="Report scope",
        ),
    )
    questions = Questions(store)
    questions.answer(
        identity,
        question.id,
        QuestionAnswer(expected_revision=question.revision, answer="Keep both."),
    )
    execution.finish(identity, run.id, "waiting_for_input", input_checkpoint=BASE)
    sibling = execution.claim(identity, BASE, {}, task_id="site")
    assert sibling is not None
    assert questions.get(identity, question.id).delivery.state == "Waiting for capacity"
    saved = spaces.workers(identity)
    spaces.queue(identity, QueueEdit(expected_revision=saved.revision, enabled=False))
    assert questions.get(identity, question.id).delivery.state == "Paused"
    assert execution.claim(identity, BASE, {}, task_id="core") is None


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
        assert not db.execute("SELECT 1 FROM projects WHERE id='extra'").fetchone()
