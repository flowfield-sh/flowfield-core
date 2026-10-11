import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from flowfield.application import Workspace
from flowfield.cli import app
from flowfield.updates import INDEX_URL, INTERVAL, Updates, cached_status, latest_release


def index(*versions):
    return {
        "name": "flowfield-core",
        "files": [
            {
                "filename": f"flowfield_core-{v}-py3-none-any.whl",
                "requires-python": ">=3.12",
                "yanked": False,
            }
            for v in versions
        ],
    }


def test_version_ordering_yanks_prereleases_and_python_compatibility():
    payload = index("0.9", "0.10", "1.0rc1", "1.0.dev2", "2.0", "3.0")
    payload["files"][-1]["requires-python"] = ">=3.14"
    payload["files"][-2]["yanked"] = ""
    assert latest_release(payload, "0.1", "3.12.10") == "0.10"
    assert latest_release(payload, "0.1.dev0", "3.12.10") == "1.0rc1"
    assert latest_release(index("1.0"), "1.0", "3.12.10") == "1.0"
    with pytest.raises(ValueError):
        latest_release({"name": "another", "files": []}, "1.0", "3.12")


def test_startup_hourly_manual_coalescing_announcement_and_restart(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOWFIELD_UPDATE_CHECKS")
    clock = [datetime(2026, 10, 2, tzinfo=UTC)]
    calls = []
    versions = ["0.2"]

    async def fetch(request):
        assert str(request.url) == INDEX_URL
        assert request.headers["accept"] == "application/vnd.pypi.simple.v1+json"
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json=index(*versions))

    workspace = Workspace(tmp_path)
    updates = Updates(
        workspace, installed="0.1", transport=httpx.MockTransport(fetch), clock=lambda: clock[0]
    )

    async def scenario():
        assert not updates.trigger("hourly").checking
        assert updates.trigger("startup").checking
        first = updates.task
        updates.trigger("startup")
        updates.trigger("manual", "manual-client")
        updates.trigger("manual", "second-client")
        updates.trigger("hourly")
        assert updates.task is first
        await updates.task
        assert len(calls) == 1
        assert len(updates.status().commands) == 2
        with workspace.connection() as db:
            assert db.execute("SELECT count(*) FROM announcement_events").fetchone()[0] == 1
            event = json.loads(db.execute("SELECT data FROM announcement_events").fetchone()[0])
            assert event["exclude_clients"] == ["manual-client", "second-client"]
        clock[0] += timedelta(seconds=30)
        updates.trigger("manual")
        await updates.task
        assert len(calls) == 2
        with workspace.connection() as db:
            assert db.execute("SELECT count(*) FROM announcement_events").fetchone()[0] == 1
        clock[0] += timedelta(seconds=INTERVAL - 1)
        updates.trigger("hourly")
        assert len(calls) == 2
        versions.append("0.3")
        clock[0] += timedelta(seconds=1)
        updates.trigger("hourly")
        await updates.task
        assert len(calls) == 3
        with workspace.connection() as db:
            event = json.loads(
                db.execute("SELECT data FROM announcement_events ORDER BY id DESC").fetchone()[0]
            )
            assert event["exclude_clients"] == []
            assert (
                db.execute(
                    "SELECT source_key FROM announcement_events ORDER BY id DESC"
                ).fetchone()[0]
                == "update:0.3"
            )
        assert Updates(Workspace(tmp_path), installed="0.1").status().available_version == "0.3"
        upgraded = Updates(workspace, installed="0.3")
        assert upgraded.status().available_version is None
        await updates.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["timeout", "malformed", "oversized", "missing", "server"])
def test_failures_preserve_last_success_and_notice(tmp_path, failure):
    clock = [datetime(2026, 10, 2, tzinfo=UTC)]
    broken = [False]

    async def fetch(request):
        if not broken[0]:
            return httpx.Response(200, json=index("0.2"))
        if failure == "timeout":
            raise httpx.ReadTimeout("offline")
        if failure == "malformed":
            return httpx.Response(200, text="not json")
        if failure == "oversized":
            return httpx.Response(200, content=b" " * 2_000_001)
        return httpx.Response(404 if failure == "missing" else 500)

    updates = Updates(
        Workspace(tmp_path),
        installed="0.1",
        transport=httpx.MockTransport(fetch),
        clock=lambda: clock[0],
    )

    async def scenario():
        updates.trigger()
        await updates.task
        good = updates.status()
        broken[0] = True
        clock[0] += timedelta(seconds=10)
        updates.trigger()
        await updates.task
        bad = updates.status()
        assert bad.error and bad.last_success == good.last_success
        assert bad.available_version == "0.2" and bad.last_attempt != good.last_attempt
        assert len(bad.commands) == 2

    asyncio.run(scenario())


def test_disable_automatic_checks_still_allows_explicit_check(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 2, tzinfo=UTC)]
    updates = Updates(
        Workspace(tmp_path),
        installed="0.1",
        clock=lambda: clock[0],
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=index("0.2"))),
    )

    async def scenario():
        assert updates.status().disabled_by_environment
        assert not updates.trigger("startup").checking
        monkeypatch.delenv("FLOWFIELD_UPDATE_CHECKS")
        updates.configure(False)
        clock[0] += timedelta(hours=2)
        assert not updates.trigger("hourly").checking
        assert not updates.trigger("startup").checking
        assert updates.trigger("manual").checking
        await updates.task
        assert updates.status().available_version == "0.2"
        assert not Updates(Workspace(tmp_path)).status().automatic

    asyncio.run(scenario())


def test_offline_cli_never_initializes_or_contacts_registry(tmp_path, monkeypatch):
    from flowfield.client import Client
    from flowfield.errors import ApplicationError

    def unavailable(*args, **kwargs):
        raise ApplicationError("service_unavailable", "stopped")

    monkeypatch.setattr(Client, "request", unavailable)
    directory = tmp_path / "missing"
    result = CliRunner().invoke(app, ["--data-dir", str(directory), "update", "status", "--json"])
    assert result.exit_code == 0 and not result.stderr
    assert json.loads(result.stdout)["cached"]
    assert not directory.exists()
    Workspace(directory)
    before = (directory / "workspace.sqlite3").read_bytes()
    assert cached_status(directory).last_success is None
    assert (directory / "workspace.sqlite3").read_bytes() == before
    result = CliRunner().invoke(app, ["--data-dir", str(directory), "update", "check", "--json"])
    assert result.exit_code == 1 and not result.stdout
    assert json.loads(result.stderr)["error"]["code"] == "service_unavailable"


def test_service_check_api_and_cli_share_status_without_polluting_json(tmp_path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from flowfield.api import create_app
    from flowfield.client import Client

    api = create_app(data_dir=tmp_path)
    monkeypatch.delenv("FLOWFIELD_UPDATE_CHECKS")
    with TestClient(api, base_url="http://localhost") as http:
        api.state.notifications.updates.transport = httpx.MockTransport(
            lambda _: httpx.Response(200, json=index("9.0"))
        )
        assert http.post("/api/updates/check", json={"reason": "startup"}).status_code == 200
        deadline = time.monotonic() + 3
        while (saved := http.get("/api/updates").json())["checking"]:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert saved["available_version"] == "9.0" and not saved["error"]
        assert http.post("/api/updates/check", json={"reason": "invalid"}).status_code == 422
        assert (
            http.put("/api/updates/settings", json={"automatic": False}).json()["automatic"]
            is False
        )

        def local_request(self, method, path, body=None, **kwargs):
            return http.request(method, "/api/" + path, json=body).json()

        monkeypatch.setattr(Client, "request", local_request)
        runner = CliRunner()
        machine = runner.invoke(app, ["update", "check", "--json"])
        assert machine.exit_code == 0 and not machine.stderr
        assert json.loads(machine.stdout)["available_version"] == "9.0"
        human = runner.invoke(app, ["update", "status"])
        assert human.exit_code == 0 and "available" not in human.stdout
        assert "9.0 is available" in human.stderr
        assert "uv tool upgrade" in human.stderr and "python -m pip" in human.stderr


def test_restart_reports_interrupted_check(tmp_path):
    from flowfield.updates import save_state, status_from

    workspace = Workspace(tmp_path)
    with workspace.connection(write=True) as db:
        save_state(db, {"last_attempt": "2026-10-02T12:00:00+00:00", "latest_version": "0.2"})
    updates = Updates(workspace, installed="0.1")
    assert "did not complete" in updates.status().error
    assert status_from({}, "0.1").error is None
