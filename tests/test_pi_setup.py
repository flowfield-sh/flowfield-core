"""Pi joins the shared connection and persisted harness-settings journeys."""

import json

import pytest

from flowfield.adapters.pi_connection import PiConnection
from flowfield.errors import ApplicationError


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
