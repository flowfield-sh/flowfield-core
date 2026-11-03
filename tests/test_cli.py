import json

from typer.testing import CliRunner

from flowfield import __version__
from flowfield.cli import app

runner = CliRunner()


def test_cli_help_and_versions() -> None:
    assert runner.invoke(app, ["--help"]).exit_code == 0
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
    result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"version": __version__}


def test_serve_rejects_invalid_port() -> None:
    assert runner.invoke(app, ["serve", "--port", "0"]).exit_code == 2


def test_unknown_command_fails() -> None:
    assert runner.invoke(app, ["checkout"]).exit_code == 2


def test_guidance_install_has_outcome_files_and_next_steps(monkeypatch):
    from flowfield.client import Client

    payload = {
        "revision": "a" * 64,
        "status": "installed",
        "message": "Project guidance installed.",
        "changed_files": ["AGENTS.md", ".agents/skills/flowfield-coordinator/SKILL.md"],
        "notices": ["Review additional instruction files: src/AGENTS.override.md."],
        "next_steps": ["Review and commit guidance.", "Start a fresh Codex conversation."],
        "instruction_files": ["AGENTS.md", "src/AGENTS.override.md"],
        "baseline": "not_committed",
        "baseline_ref": "HEAD",
    }
    monkeypatch.setattr(Client, "request", lambda *args, **kwargs: payload)
    result = runner.invoke(app, ["integration", "guidance", "install", "--project", "harbor"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("Project guidance installed.\n\nChanged files:\n- AGENTS.md")
    assert "\nReview:\n- Review additional" in result.output
    assert "\nNext:\n- Review and commit" in result.output
    assert "Worker baseline:" not in result.output and "Instruction files:" not in result.output
    shown = runner.invoke(app, ["integration", "guidance", "show", "--project", "harbor"])
    assert "Worker baseline:" in shown.output and "Instruction files:" in shown.output


def test_cli_boundary_excludes_application_management():
    for command in ("workspace", "project", "task", "inbox", "status"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 2 and "No such command" in result.output
    assert runner.invoke(app, ["harness", "configure"]).exit_code == 2
    assert runner.invoke(app, ["integration", "guidance", "--help"]).exit_code == 0
