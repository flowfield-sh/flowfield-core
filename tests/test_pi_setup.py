"""Pi joins the shared connection and persisted harness-settings journeys."""

import json

import pytest

from flowfield import migrations
from flowfield.adapters.pi_connection import PiConnection
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessEdit
from flowfield.harness_settings import HarnessSettings, offline_registration


def test_pi_connection_preserves_other_native_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setattr("flowfield.adapters.connection.shutil.which", lambda _: "/native/pi")
    path = tmp_path / "mcp.json"
    data = {"mcpServers": {"other": {"command": "keep"}}, "autoEnableCodemode": False}
    path.write_text(json.dumps(data))
    calls = []

    async def probe(_):
        return ["get_task"]

    def run(self, *args):
        calls.append(args)
        current = json.loads(path.read_text())
        if args[:2] == ("mcp", "add"):
            assert args == ("mcp", "add", "flowfield", "--url", self.url, "--exposure", "direct")
            current["mcpServers"]["flowfield"] = {"url": self.url, "exposure": "direct"}
        else:
            assert args == ("mcp", "remove", "flowfield")
            del current["mcpServers"]["flowfield"]
        path.write_text(json.dumps(current))

    monkeypatch.setattr("flowfield.adapters.connection.probe", probe)
    monkeypatch.setattr(PiConnection, "run", run)
    connection = PiConnection(8765)
    assert connection.connect()["changed"]
    assert not connection.connect()["changed"]
    assert connection.doctor()["harness"] == "pi"
    assert connection.disconnect()["changed"]
    assert not connection.disconnect()["changed"]
    assert json.loads(path.read_text()) == data
    assert len(calls) == 2
    data["mcpServers"]["flowfield"] = {"url": "http://other/mcp"}
    path.write_text(json.dumps(data))
    for action in (connection.connect, connection.doctor, connection.disconnect):
        with pytest.raises(ApplicationError, match="different server"):
            action()
    assert json.loads(path.read_text()) == data


def test_schema_51_upgrade_preserves_native_settings_and_catalog_ownership(tmp_path, monkeypatch):
    directory = tmp_path / "state"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:-1])
        old = Workspace(directory)
        saved = HarnessSettings(old).edit(
            "claude-code", HarnessEdit(expected_revision=1, executable="/native/claude")
        )
        with old.connection(write=True) as db:
            db.execute(
                "INSERT INTO harness_catalogs VALUES ('codex',?)",
                ('{"status":"uncertain","id":"owned"}',),
            )
        assert offline_registration(directory, "pi").revision == 1
    upgraded = Workspace(directory)
    assert upgraded.schema_version == 52
    assert HarnessSettings(upgraded).get("claude-code") == saved
    assert HarnessSettings(upgraded).get("pi").revision == 1
    with upgraded.connection() as db:
        assert (
            db.execute("SELECT data FROM harness_catalogs WHERE harness='codex'").fetchone()[0]
            == '{"status":"uncertain","id":"owned"}'
        )
        assert db.execute("SELECT backup FROM schema_migrations WHERE version=52").fetchone()[0]
