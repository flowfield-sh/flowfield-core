"""Reusable isolated setup and scripted peers; no collected test cases."""

import sys
from pathlib import Path

from execution_fixtures import fixture

from flowfield.adapters.git_workspace import git
from flowfield.agent_models import AgentChoice
from flowfield.application import TaskEdit
from flowfield.execution_models import SettingsEdit
from flowfield.integration_models import IntegrationConfig
from flowfield.supervisor import Supervisor

FAKE = Path(__file__).with_name("fake_native_codex.py")


def configured(tmp_path, monkeypatch, *, count=1, scenario="normal", flags=()):
    execution = fixture(tmp_path, count=count, cap=count)
    repo = tmp_path / "harbor"
    git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@invalid", "commit", "-m", "base")
    monkeypatch.setenv("FLOWFIELD_TEST_SCENARIO", scenario)
    monkeypatch.setenv("CODEX_PATH", sys.executable)  # Native discovery remains model-free in CI.
    monkeypatch.setenv("FLOWFIELD_TEST_SECRET", "do-not-persist-host-secrets")
    monkeypatch.setattr(
        "flowfield.adapters.codex_agent.command",
        lambda directory, env: (
            [sys.executable, str(FAKE), "managed", "cleanup", "close-session", *flags],
            dict(env),
        ),
    )
    service = Supervisor(execution.workspace)
    settings = service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=1,
            target_branch="main",
            runtime="local",
            checks=["test -f base.txt"],
            setup_commands=['test -n "$HOME" && test -n "$FLOWFIELD_RUNTIME_DIR"'],
        ),
    )
    workers = execution.settings("harbor")
    execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=workers.revision,
            max_parallel=count,
            selection=AgentChoice(model="test-model", effort="low", mode="workspace-write"),
        ),
    )
    for task in execution.workspace.tasks("harbor"):
        execution.workspace.edit_task(
            "harbor",
            task.id,
            TaskEdit(
                completion="code",
                expected_revision=task.revision,
            ),
        )
    return service, repo, settings
