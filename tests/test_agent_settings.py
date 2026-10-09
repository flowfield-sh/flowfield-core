"""Settings inheritance, frozen attempts, concurrent edits and migration preservation."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from test_execution import BASE, fixture

from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import SettingsEdit


def edit(revision=1, model="other", effort="high"):
    return AgentSettingsEdit(
        expected_revision=revision, selection=AgentChoice(model=model, effort=effort)
    )


def test_inheritance_override_reset_and_frozen_claim(tmp_path):
    execution = fixture(tmp_path, count=2, cap=2)
    settings = AgentSettings(execution.workspace)
    inherited = settings.get("harbor", "worker", "task-0")
    assert inherited.selection is None
    assert inherited.effective.choice.model == "test-model"
    changed = settings.edit("harbor", "worker", edit(), "task-0")
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run.task_id == "task-0" and run.model == "other" and run.effort == "high"
    assert run.agent_settings.model_copy(update={"registration": None}) == changed.effective
    default = execution.settings("harbor")
    execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=default.revision,
            max_parallel=2,
            selection=AgentChoice(model="new", effort="low"),
        ),
    )
    assert settings.get("harbor", "worker", "task-0").effective.choice.model == "other"
    assert settings.get("harbor", "worker", "task-1").effective.choice.model == "new"
    reset = settings.edit(
        "harbor", "worker", AgentSettingsEdit(expected_revision=changed.revision), "task-0"
    )
    assert reset.effective.choice.model == "new" and reset.effective.source == "project"
    assert execution.get("harbor", run.id).agent_settings == run.agent_settings
    assert execution.get("harbor", run.id).model == "other"
    with pytest.raises(ApplicationError):
        settings.edit("harbor", "worker", edit(), "task-0")
    with pytest.raises(ApplicationError):
        settings.edit("harbor", "worker", edit(), "missing")


def test_coordinator_has_one_selection_independent_of_workers(tmp_path):
    execution = fixture(tmp_path)
    settings = AgentSettings(execution.workspace)
    assert settings.get("harbor", "coordinator").effective is None
    saved = settings.edit("harbor", "coordinator", edit())
    assert saved.effective.choice.model == "other"
    settings.edit("harbor", "coordinator", edit(revision=2, model="updated"))
    assert settings.get("harbor", "coordinator").effective.choice.model == "updated"
    assert settings.get("harbor", "worker").effective.choice.model == "test-model"
    restored = AgentSettings(Workspace(execution.workspace.directory))
    assert restored.get("harbor", "coordinator") == settings.get("harbor", "coordinator")


def test_concurrent_edits_have_one_winner(tmp_path):
    settings = AgentSettings(fixture(tmp_path).workspace)

    def save(model):
        try:
            return settings.edit("harbor", "worker", edit(model=model), "task-0").selection.model
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["first", "second"]))
    assert results.count("revision_conflict") == 1
    assert settings.get("harbor", "worker", "task-0").revision == 2


def test_worker_retry_resolves_override_again(tmp_path):
    from flowfield.execution_models import RunAction

    execution = fixture(tmp_path)
    settings = AgentSettings(execution.workspace)
    settings.edit("harbor", "worker", edit(), "task-0")
    run = execution.claim("harbor", BASE, {})
    assert run.purpose == "work" and run.model == "other"
    execution.finish("harbor", run.id, "stopped")
    stopped = execution.get("harbor", run.id)
    settings.edit("harbor", "worker", edit(2, "retry-model"), "task-0")
    execution.retry("harbor", run.id, RunAction(expected_revision=stopped.revision))
    retried = execution.claim("harbor", BASE, {})
    assert retried.model == "retry-model"
    assert execution.get("harbor", run.id).model == "other"
