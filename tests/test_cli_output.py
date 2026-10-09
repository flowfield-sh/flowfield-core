"""Human output and opt-in JSON across real service response shapes; no native calls."""

import json
import shlex

import pytest
from project_fixtures import existing_directory
from test_connection import running_service
from typer.testing import CliRunner

from flowfield.cli import app
from flowfield.client import Client
from flowfield.errors import ApplicationError

runner = CliRunner()


def test_human_reads_cover_real_service_shapes_and_json(tmp_path):
    with running_service(tmp_path / "state") as base:
        prefix = ["--port", base.rsplit(":", 1)[1]]

        def invoke(*args):
            result = runner.invoke(app, [*prefix, *args])
            assert result.exit_code == 0, (args, result.output, result.exception)
            return result

        invoke("project", "init", existing_directory(str(tmp_path / "harbor")))
        guidance = invoke("project", "guidance", "install", "--project", "harbor")
        assert "Project guidance installed." in guidance.stdout
        assert "Changed files:" in guidance.stdout and "AGENTS.md" in guidance.stdout
        assert "Next:" in guidance.stdout
        guidance_json = invoke("project", "guidance", "install", "--project", "harbor", "--json")
        assert json.loads(guidance_json.stdout)["saved"]
        assert "changed_files" not in json.loads(guidance_json.stdout)
        guidance = invoke("project", "guidance", "remove", "--project", "harbor")
        assert "removed" in guidance.stdout
        invoke("task", "create", "--project", "harbor", "--id", "export", "--title", "CSV export")
        invoke("task", "note", "export", "--project", "harbor", "--body", "CSV evidence")
        invoke("milestone", "create", "--project", "harbor", "--id", "launch", "--title", "Launch")
        invoke(
            "harness",
            "configure",
            "codex",
            "--revision",
            "1",
            "--executable",
            str(tmp_path / "missing-codex"),
            "--config-directory",
            str(tmp_path),
        )
        cases = [
            (["project", "list"], "harbor"),
            (["project", "show"], "Description:"),
            (["project", "guidance", "show"], "Worker baseline:"),
            (["status"], "Backlog"),
            (["task", "list"], "CSV export"),
            (["task", "show", "export"], "CSV export"),
            (["task", "stages", "export"], "1. CSV export · Planned"),
            (["task", "activity", "export"], "CSV evidence"),
            (["task", "revisions", "export"], "Revision 1"),
            (["task", "relationships", "export"], "No items yet."),
            (["project", "search", "CSV", "--limit", "1"], "More available: --before"),
            (["project", "search", "unfindable"], "No matching evidence."),
            (["project", "coordinator-history"], "No saved coordinator exchanges."),
            (["milestone", "list"], "Launch"),
            (["milestone", "show", "launch"], "Launch"),
            (["inbox", "list"], "No questions in this view."),
            (["project", "workers", "show"], "Queue paused"),
            (["task", "runs", "list"], "No attempts yet."),
            (["task", "results", "list", "export"], "No results yet."),
            (["project", "integration", "list"], "No integrations yet."),
            (["project", "integration", "show"], "Target:"),
        ]
        for args, expected in cases:
            args += ["--project", "harbor"] if args != ["project", "list"] else []
            human = invoke(*args)
            assert expected in human.stdout, (args, human.stdout)
            assert not human.stdout.lstrip().startswith(("{", "[")), (args, human.stdout)
            machine = invoke(*args, "--json")
            assert not machine.stderr
            assert isinstance(json.loads(machine.stdout), (dict, list))
        for command in ("status", "settings"):
            human = invoke(
                *(["--data-dir", str(tmp_path / "state")] if command == "status" else []),
                "harness",
                command,
                "codex",
            )
            assert "Executable: Not found" in human.stdout
            assert "Sign-in: Not checked" in human.stdout
        machine = invoke("harness", "configure", "codex", "--revision", "2", "--json")
        assert json.loads(machine.stdout) == {"saved": True, "revision": 3}
        invalid_args = [
            *prefix,
            "harness",
            "configure",
            "codex",
            "--revision",
            "3",
            "--executable",
            "relative-path",
        ]
        invalid = runner.invoke(app, invalid_args)
        assert invalid.exit_code == 1 and not invalid.stdout
        assert "Invalid request:\n- executable:" in invalid.stderr
        assert "'input':" not in invalid.stderr
        machine = runner.invoke(app, [*invalid_args, "--json"])
        assert machine.exit_code == 1 and not machine.stdout
        assert json.loads(machine.stderr)["error"]["message"] == invalid.stderr.strip()


@pytest.mark.parametrize("command", ["status", "check"])
def test_offline_updates_are_actionable_and_json_is_explicit(tmp_path, monkeypatch, command):
    from urllib.error import URLError

    class Offline:
        def open(self, *args, **kwargs):
            raise URLError("connection refused")

    monkeypatch.setattr("flowfield.client.build_opener", lambda *args: Offline())
    state = tmp_path / "space in path"
    args = ["--data-dir", str(state), "--port", "8770", "update", command]
    human = runner.invoke(app, args)
    assert human.exit_code == (0 if command == "status" else 1)
    assert "Keep that terminal open" in human.output
    start = next(
        line.removeprefix("Start it with: ")
        for line in human.output.splitlines()
        if line.startswith("Start it with: ")
    )
    if start:
        assert shlex.split(start) == [
            "flowfield",
            "--data-dir",
            str(state),
            "serve",
            "--port",
            "8770",
        ]
    assert not state.exists()
    machine = runner.invoke(app, [*args, "--json"])
    payload = json.loads(machine.stdout if command == "status" else machine.stderr)
    assert (
        payload.get("cached")
        if command == "status"
        else payload["error"]["code"] == "service_unavailable"
    )
    assert not (machine.stderr if command == "status" else machine.stdout)


def test_model_choices_need_no_json_and_preserve_machine_payload(monkeypatch):
    value = [
        {
            "id": "native-model",
            "name": "Native model",
            "efforts": [],
            "modes": [
                {"id": "acceptEdits", "name": "Accept edits", "description": "Allow file edits."}
            ],
            "fast": False,
        }
    ]
    monkeypatch.setattr(Client, "request", lambda *args, **kwargs: value)
    args = ["project", "workers", "models", "--project", "harbor"]
    human = runner.invoke(app, args)
    assert human.exit_code == 0 and "Effort: Not configurable" in human.stdout
    assert "Access: Accept edits (--mode acceptEdits)" in human.stdout
    assert json.loads(runner.invoke(app, [*args, "--json"]).stdout) == value
    value.clear()
    assert runner.invoke(app, args).stdout.strip() == "No models available."


def test_confirmation_formats_actual_bounded_receipt_and_errors(monkeypatch):
    # Client.request reduces a HarnessStatus mutation to this receipt.
    monkeypatch.setattr(Client, "request", lambda *args, **kwargs: {"saved": True})
    args = ["harness", "confirm-stopped", "codex", "--discovery", "discovery-one"]
    human = runner.invoke(app, args)
    assert human.exit_code == 0
    assert human.stdout.strip() == "Confirmed discovery discovery-one stopped for codex."
    assert json.loads(runner.invoke(app, [*args, "--json"]).stdout) == {"saved": True}

    def conflict(*args, **kwargs):
        raise ApplicationError("catalog_changed", "Discovery changed. Reload its status.", 409)

    monkeypatch.setattr(Client, "request", conflict)
    human = runner.invoke(app, args)
    assert human.exit_code == 1 and not human.stdout
    assert human.stderr.strip() == "Discovery changed. Reload its status."
    machine = runner.invoke(app, [*args, "--json"])
    assert machine.exit_code == 1 and not machine.stdout
    assert json.loads(machine.stderr)["error"]["code"] == "catalog_changed"


def test_results_keep_pagination_visible_without_json(monkeypatch):
    payload = {
        "items": [
            {
                "task_key": "HAR-1",
                "version": 2,
                "status": "ready",
                "id": "result-one",
                "revision": 3,
                "candidate_commit": "abc123",
            }
        ],
        "next_before": 2,
    }
    monkeypatch.setattr(Client, "request", lambda *args, **kwargs: payload)
    args = ["task", "results", "list", "HAR-1", "--project", "harbor"]
    human = runner.invoke(app, args)
    assert human.exit_code == 0
    assert "Result v2" in human.stdout and "More: --before 2" in human.stdout
    assert json.loads(runner.invoke(app, [*args, "--json"]).stdout) == payload


def test_uncertain_harness_reports_exact_recovery_on_selected_port(tmp_path, monkeypatch):
    from flowfield.adapters.harness_host import status
    from flowfield.harness_models import HarnessRegistration

    payload = status(tmp_path, HarnessRegistration(harness="codex"), {"PATH": ""}).model_dump()
    payload["catalog_ownership"] = {"id": "discovery-one", "status": "uncertain"}
    payload["problems"] = ["catalog_uncertain"]
    monkeypatch.setattr(Client, "request", lambda *args, **kwargs: payload)
    args = ["--port", "8770", "harness", "settings", "codex"]
    human = runner.invoke(app, args)
    assert human.exit_code == 0
    assert "Discovery: discovery-one · uncertain" in human.stdout
    assert (
        "flowfield --port 8770 harness confirm-stopped codex --discovery discovery-one"
        in human.stdout
    )
    assert json.loads(runner.invoke(app, [*args, "--json"]).stdout) == payload
