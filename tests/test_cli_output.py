"""Human output and opt-in JSON across real service response shapes; no native calls."""

import json
import shlex

import pytest
from typer.testing import CliRunner

from flowfield.cli import app
from flowfield.client import Client
from flowfield.errors import ApplicationError

runner = CliRunner()


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
