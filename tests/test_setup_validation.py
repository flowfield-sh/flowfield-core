"""Model-free Local setup lifecycle; no harness or model is involved."""

import asyncio
from pathlib import Path

import pytest
from test_guidance import setup

from flowfield.adapters import local_checks
from flowfield.adapters.git_workspace import git
from flowfield.errors import ApplicationError
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig
from flowfield.setup_validation import SetupCheckRequest, SetupValidation


def configured(tmp_path, monkeypatch, **changes):
    root, guidance = setup(tmp_path)
    git(root, "init", "-b", "main")
    (root / "base.txt").write_text("base")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@invalid", "commit", "-m", "base")
    service = SetupValidation(guidance.workspace)
    settings = Integrations(guidance.workspace).configure(
        "project",
        IntegrationConfig(
            runtime="local",
            expected_revision=1,
            target_branch="delivery",
            create_from="HEAD",
            checks=changes.pop("checks", ["test -f base.txt"]),
            **changes,
        ),
    )
    return root, service, settings


def test_setup_uses_host_tools_and_retains_evidence_without_enabling_queue(tmp_path, monkeypatch):
    tools = tmp_path / "host-bin"
    tools.mkdir()
    tool = tools / "compiler"
    tool.write_text("#!/bin/sh\nprintf 'custom tool works\\n'\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + ":" + __import__("os").environ["PATH"])
    root, service, settings = configured(
        tmp_path,
        monkeypatch,
        setup_commands=['compiler; test -n "$FLOWFIELD_RUNTIME_DIR"'],
    )
    before = git(root, "rev-parse", "HEAD")
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    assert value.status == "passed", value.problem
    assert "custom tool works" in value.setup[0].output
    assert Path(value.workspace).is_dir() and not value.stale
    assert git(root, "rev-parse", "HEAD") == before
    from flowfield.execution import Execution

    assert not Execution(service.workspace).settings("project").enabled
    assert "Switch the project checkout" in value.checkout_problem
    git(root, "switch", "delivery")
    assert service.get("project").checkout_problem is None
    (root / "human.txt").write_text("Preserve my work")
    assert "local edits or untracked" in service.get("project").checkout_problem
    assert service.get("project").status == "passed"


def test_library_failure_names_host_remedy_without_changing_settings(tmp_path, monkeypatch):
    _, service, settings = configured(
        tmp_path,
        monkeypatch,
        setup_commands=["printf 'Library not loaded: /example/libthing.dylib' >&2; exit 126"],
    )
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    assert value.status == "failed" and not value.checks
    assert "library dependencies on the service host" in value.problem
    assert "/example/libthing.dylib" in value.setup[0].output
    assert Integrations(service.workspace).settings("project") == settings


def test_duplicate_validation_and_midflight_settings_change(tmp_path, monkeypatch):
    _, service, settings = configured(tmp_path, monkeypatch)
    actual = local_checks.run_checks

    async def scenario():
        started, resume = asyncio.Event(), asyncio.Event()

        async def controlled(*args, **kwargs):
            started.set()
            await resume.wait()
            return await actual(*args, **kwargs)

        monkeypatch.setattr(local_checks, "run_checks", controlled)
        request = SetupCheckRequest(expected_revision=settings.revision)
        job = asyncio.create_task(service.check("project", request))
        await asyncio.wait_for(started.wait(), 10)
        with pytest.raises(ApplicationError, match="already running"):
            await service.check("project", request)
        Integrations(service.workspace).configure(
            "project",
            IntegrationConfig(
                expected_revision=settings.revision, target_branch="delivery", checks=["false"]
            ),
        )
        resume.set()
        value = await job
        assert value.status == "passed" and value.stale

    asyncio.run(scenario())


def test_interrupted_process_evidence_blocks_duplicate_execution(tmp_path, monkeypatch):
    _, service, settings = configured(tmp_path, monkeypatch)
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    value.status, value.pid = "checking", 12345
    service._save(value)
    restarted = SetupValidation(service.workspace)
    assert restarted.get("project").status == "uncertain"
    with pytest.raises(ApplicationError, match="interrupted"):
        asyncio.run(
            restarted.check("project", SetupCheckRequest(expected_revision=settings.revision))
        )


def test_cli_check_changes_preserve_other_settings(monkeypatch):
    from typer.testing import CliRunner

    from flowfield.cli import app

    current = {
        "revision": 2,
        "target_branch": "delivery",
        "checks": ["old"],
    }
    writes = []

    def request(self, method, path, data=None):
        if method == "PUT":
            writes.append(data)
        return current

    monkeypatch.setattr("flowfield.client.Client.request", request)
    result = CliRunner().invoke(
        app,
        [
            "project",
            "integration",
            "configure",
            "--project",
            "example",
            "--target",
            "delivery",
            "--check",
            "new",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert writes[0]["target_branch"] == "delivery"
    assert writes[0]["checks"] == ["new"]
    assert writes[0]["runtime"] is None and writes[0]["checks"] == ["new"]
    assert "setup_commands" not in writes[0]
    assert "setup_timeout_seconds" not in writes[0]
    assert "check_timeout_seconds" not in writes[0]
    cleared = CliRunner().invoke(
        app,
        [
            "project",
            "integration",
            "configure",
            "--project",
            "example",
            "--target",
            "delivery",
            "--check",
            "new",
            "--clear-setup",
            "--json",
        ],
    )
    assert cleared.exit_code == 0, cleared.output
    assert writes[-1]["setup_commands"] == []


def test_revalidation_preserves_exact_failed_evidence_and_settings(tmp_path, monkeypatch):
    _, service, settings = configured(tmp_path, monkeypatch, checks=["printf ECONNREFUSED; exit 1"])
    failed = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    assert failed.status == "failed" and "required service was unreachable" in failed.problem
    assert failed.settings == settings
    updated = Integrations(service.workspace).configure(
        "project",
        IntegrationConfig(
            expected_revision=settings.revision,
            target_branch="delivery",
            checks=["test -f base.txt"],
        ),
    )
    assert service.get("project").stale
    passed = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=updated.revision))
    )
    assert passed.status == "passed" and passed.previous_check_id == failed.id
    restarted = SetupValidation(service.workspace)
    historical = restarted.get("project", failed.id)
    assert historical.status == "failed" and historical.stale
    assert historical.settings == settings and historical.checks == failed.checks
    assert historical.workspace != passed.workspace
    assert restarted.get("project", passed.id) == restarted.get("project")
    with pytest.raises(ApplicationError, match="Invalid setup check"):
        restarted.get("project", "../setup-project")
    with pytest.raises(ApplicationError, match="not found"):
        restarted.get("project", "a" * 32)


def test_legacy_setup_evidence_survives_first_revalidation(tmp_path, monkeypatch):
    import json

    _, service, settings = configured(tmp_path, monkeypatch)
    old = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    data = old.model_dump(exclude={"settings", "previous_check_id"})
    service._path("project").write_text(json.dumps(data))
    fresh = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    assert fresh.previous_check_id == old.id
    assert service.get("project", old.id).settings is None
    assert service.get("project", old.id).checks == old.checks


@pytest.mark.parametrize(
    ("command", "hint"),
    [
        ("flowfield_intentionally_missing_tool_84912", "Install the tool on the service host"),
        ("printf 'Permission denied'; exit 126", "service user's access"),
        ("printf 'Connection refused'; exit 1", "required service was unreachable"),
    ],
)
def test_setup_failure_keeps_output_and_actionable_recovery(tmp_path, monkeypatch, command, hint):
    _, service, settings = configured(tmp_path, monkeypatch, setup_commands=[command])
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    assert value.status == "failed" and not value.checks
    assert hint in value.problem and value.setup[0].exit_code


def test_changing_checks_preserves_unspecified_setup_and_timeouts(tmp_path, monkeypatch):
    _, service, settings = configured(
        tmp_path,
        monkeypatch,
        setup_commands=["printf setup"],
        setup_timeout_seconds=35,
        check_timeout_seconds=17,
    )
    integration = Integrations(service.workspace)
    changed = integration.configure(
        "project",
        IntegrationConfig(
            expected_revision=settings.revision,
            target_branch="delivery",
            checks=["printf new-check"],
        ),
    )
    assert changed.setup_commands == settings.setup_commands
    assert changed.setup_timeout_seconds == 35 and changed.check_timeout_seconds == 17
    cleared = integration.configure(
        "project",
        IntegrationConfig(
            expected_revision=changed.revision,
            target_branch="delivery",
            checks=changed.checks,
            setup_commands=[],
            setup_timeout_seconds=120,
            check_timeout_seconds=60,
        ),
    )
    assert cleared.setup_commands == []
    assert cleared.setup_timeout_seconds == 120 and cleared.check_timeout_seconds == 60
