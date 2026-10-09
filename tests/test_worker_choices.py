"""Unified role defaults, absent controls and actual supported-state choice migration."""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_execution import BASE, fixture

from flowfield import migrations, storage
from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption, SettingsEdit
from flowfield.supervisor import Supervisor


def test_worker_cli_uses_one_choice_and_does_not_carry_access_between_harnesses(monkeypatch):
    from typer.testing import CliRunner

    from flowfield.cli import app
    from flowfield.client import Client

    calls = []
    current = {
        "revision": 7,
        "project_id": "harbor",
        "enabled": False,
        "max_parallel": 1,
        "selection": dict(harness="codex", model="old", effort="low", mode="read-only", fast=False),
    }

    def request(self, method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if method == "PUT":
            current.update(body)
        return current

    monkeypatch.setattr(Client, "request", request)
    result = CliRunner().invoke(
        app,
        [
            "project",
            "workers",
            "configure",
            "--project",
            "harbor",
            "--harness",
            "claude-code",
            "--model",
            "native",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[1] == (
        "PUT",
        "projects/harbor/workers",
        {
            "expected_revision": 7,
            "max_parallel": 1,
            "selection": dict(
                harness="claude-code", model="native", effort=None, mode=None, fast=False
            ),
        },
    )
    result = CliRunner().invoke(
        app,
        [
            "project",
            "workers",
            "models",
            "--project",
            "harbor",
            "--harness",
            "claude-code",
            "--refresh",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[-1][1] == "worker-models?harness=claude-code&project_id=harbor&refresh=true"


def test_worker_defaults_preserve_harness_optional_effort_and_override_provenance(tmp_path):
    execution = fixture(tmp_path, count=2, cap=2)
    choices = AgentSettings(execution.workspace)
    before = execution.settings("harbor")
    native = AgentChoice(harness="claude-code", model="native-no-effort", mode="default")
    saved = execution.configure(
        "harbor", SettingsEdit(expected_revision=before.revision, selection=native, max_parallel=2)
    )
    assert saved.selection == native.model_copy(update={"fast": False})
    effective = choices.get("harbor", "worker").effective
    assert effective.choice == saved.selection and effective.default_revision == saved.revision
    override = AgentChoice(model="codex-other", effort="high", mode="read-only", fast=True)
    choices.edit(
        "harbor", "worker", AgentSettingsEdit(expected_revision=1, selection=override), "task-1"
    )
    run = execution.claim("harbor", BASE, {})
    assert run.agent_settings.choice == saved.selection and run.effort is None
    assert run.agent_settings.registration.harness == "claude-code"
    other = execution.claim("harbor", BASE, {})
    assert other.agent_settings.choice == override and other.agent_settings.source == "override"
    assert other.agent_settings.registration.harness == "codex"
    # Later changes affect future attempts, never already frozen records.
    execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=saved.revision,
            selection=override.model_copy(update={"fast": False}),
            max_parallel=2,
        ),
    )
    assert execution.get("harbor", run.id).agent_settings == run.agent_settings
    with pytest.raises(ApplicationError, match="individual task"):
        execution.configure(
            "harbor", SettingsEdit(expected_revision=saved.revision + 1, selection=override)
        )


def test_absent_effort_is_valid_only_when_catalog_offers_no_efforts(tmp_path, monkeypatch):
    service = Supervisor(fixture(tmp_path).workspace)
    offered = []

    async def models(*, project_id, harness):
        assert project_id == "harbor" and harness == "codex"
        return [ModelOption(id="native", name="Native", efforts=offered)]

    monkeypatch.setattr(service, "model_options", models)

    async def exercise():
        await service.validate_agent_choice(AgentChoice(model="native"), "harbor")
        with pytest.raises(ApplicationError, match="supported controls"):
            await service.validate_agent_choice(AgentChoice(model="native", effort="low"), "harbor")
        offered.append("low")
        with pytest.raises(ApplicationError, match="supported controls"):
            await service.validate_agent_choice(AgentChoice(model="native"), "harbor")
        await service.validate_agent_choice(AgentChoice(model="native", effort="low"), "harbor")
        await service.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["codex", "claude-code"])
@pytest.mark.parametrize("absent", [True, False])
def test_native_adapters_apply_absent_effort_only_when_control_is_absent(
    tmp_path, monkeypatch, kind, absent
):
    from test_claude_agent import CHOICE, installed_candidate

    from flowfield.adapters.codex_agent import CodexAgent

    flags = ["no-effort"] if absent else []
    if kind == "claude-code":
        agent = installed_candidate(tmp_path, *flags)
        choice = CHOICE.model_copy(update={"effort": None})
        agent.choice = choice
        metadata = json.loads(agent.environment["FLOWFIELD_TEST_META"])
        metadata["claudeCode"]["options"].pop("effort")
        agent.environment["FLOWFIELD_TEST_META"] = json.dumps(metadata)
    else:
        monkeypatch.setattr(
            "flowfield.adapters.codex_agent.command",
            lambda directory, env: (
                [
                    sys.executable,
                    str(Path(__file__).with_name("fake_acp.py")),
                    "managed",
                    "cleanup",
                    "close-session",
                    *flags,
                ],
                dict(env),
            ),
        )
        agent = CodexAgent(tmp_path / "state", tmp_path, {})
        choice = AgentChoice(model="test-model", mode="read-only", fast=False)

    async def exercise():
        try:
            await agent.start([], persistent=kind == "claude-code")
            if absent:
                assert (await agent.configure(choice)).effort is None
            else:
                with pytest.raises(ApplicationError):
                    await agent.configure(choice)
        finally:
            await agent.close()
        assert agent.cleanup_confirmed
        assert not (tmp_path / "prompt-marker").exists()

    asyncio.run(exercise())


@pytest.mark.parametrize("fail_first", [False, True])
def test_real_worker_choice_migration_preserves_and_restores_legacy_defaults(
    tmp_path, monkeypatch, fail_first
):
    previous = tuple(m for m in migrations.MIGRATIONS if m.version <= 46)
    migration = next(m for m in migrations.MIGRATIONS if m.version == 47)
    with monkeypatch.context() as baseline:
        baseline.setattr(migrations, "MIGRATIONS", previous)
        workspace = fixture(tmp_path).workspace
        legacy = dict(
            project_id="harbor",
            revision=17,
            model="saved",
            effort="high",
            mode="read-only",
            max_parallel=4,
            enabled=True,
            problem="Keep this blocker",
        )
        with workspace.connection(write=True) as db:
            db.execute("UPDATE worker_settings SET data=?", (json.dumps(legacy),))
    if fail_first:

        def fail(db):
            migration.apply(db)
            raise RuntimeError("after worker choice rewrite")

        with monkeypatch.context() as broken:
            broken.setattr(migrations, "MIGRATIONS", (*previous, replace(migration, apply=fail)))
            with pytest.raises(ApplicationError, match="rolled back"):
                Workspace(workspace.directory)
        with storage.connect(workspace.database) as db:
            assert storage.version(db) == 46
            assert (
                json.loads(db.execute("SELECT data FROM worker_settings").fetchone()[0]) == legacy
            )
    upgraded = Workspace(workspace.directory)
    with upgraded.connection() as db:
        data = json.loads(db.execute("SELECT data FROM worker_settings").fetchone()[0])
    assert data == {
        **{key: value for key, value in legacy.items() if key not in {"model", "effort", "mode"}},
        "selection": dict(
            harness="codex", model="saved", effort="high", mode="read-only", fast=False
        ),
    }
    assert AgentSettings(upgraded).get("harbor", "worker").effective.choice.model == "saved"
    backup = storage.backups(upgraded.directory)[0]
    storage.restore(upgraded.directory, backup["id"])
    with storage.connect(workspace.database) as db:
        assert storage.version(db) == 46
        assert json.loads(db.execute("SELECT data FROM worker_settings").fetchone()[0]) == legacy
    assert Workspace(workspace.directory).schema_version == migrations.current_version()
