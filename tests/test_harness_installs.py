"""Explicit host install ownership and native-readiness revision guards; no network/models."""

import asyncio
from pathlib import Path
from threading import Event

import httpx
import pytest

from flowfield.api import create_app
from flowfield.errors import ApplicationError
from flowfield.harness_installs import HarnessInstalls
from flowfield.harness_models import HarnessEdit
from flowfield.harness_settings import HarnessSettings


def test_install_disconnected_waiters_coalesce_and_shutdown_joins_thread(tmp_path, monkeypatch):
    started, release, ended = Event(), Event(), Event()
    calls = []

    def install(directory):
        calls.append(directory)
        started.set()
        assert release.wait(5)
        ended.set()
        return directory / "immutable-generation"

    monkeypatch.setattr("flowfield.harness_installs.claude_install.download_install", install)
    owner = HarnessInstalls(tmp_path)

    async def exercise():
        first = asyncio.create_task(owner.run("claude-code"))
        await asyncio.to_thread(started.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert owner.active("claude-code")
        second = asyncio.create_task(owner.run("claude-code"))
        await asyncio.sleep(0)
        close = asyncio.create_task(owner.close())
        await asyncio.sleep(0)
        assert not close.done()
        with pytest.raises(ApplicationError, match="service is stopping"):
            await owner.run("codex")
        release.set()
        assert await second == tmp_path / "immutable-generation"
        await close
        assert ended.is_set() and calls == [tmp_path]

    asyncio.run(exercise())


def test_install_failures_are_retryable_only_by_explicit_request(tmp_path, monkeypatch):
    calls = []

    def install(directory):
        calls.append(directory)
        if len(calls) == 1:
            raise OSError("host detail not public")
        return directory / "installed"

    monkeypatch.setattr("flowfield.harness_installs.codex_install.download_install", install)
    owner = HarnessInstalls(tmp_path)

    async def exercise():
        with pytest.raises(ApplicationError, match="Bridge installation failed"):
            await owner.run("codex")
        assert calls == [tmp_path] and not owner.active("codex")
        assert await owner.run("codex") == tmp_path / "installed"
        assert len(calls) == 2
        await owner.close()

    asyncio.run(exercise())


def test_service_stops_agent_owners_before_waiting_for_install(tmp_path, monkeypatch):
    from test_execution import fixture

    from flowfield.supervisor import Supervisor

    service = Supervisor(fixture(tmp_path).workspace)

    async def exercise():
        release = asyncio.Event()
        stopped = asyncio.Event()

        async def install_close():
            await release.wait()

        async def coordinator_close():
            stopped.set()

        monkeypatch.setattr(service.harness_installs, "close", install_close)
        monkeypatch.setattr(service.coordinator, "close", coordinator_close)
        close = asyncio.create_task(service.close())
        try:
            async with asyncio.timeout(2):
                await stopped.wait()
            assert not close.done()
        finally:
            release.set()
            await close

    asyncio.run(exercise())


def test_host_install_and_changed_readiness_check_http(tmp_path, monkeypatch):
    from flowfield.adapters import harness_host

    monkeypatch.setenv("FLOWFIELD_UPDATE_CHECKS", "0")
    monkeypatch.setenv("PATH", "")
    app = create_app(data_dir=tmp_path / "state")
    calls = []

    def install(directory):
        calls.append(Path(directory))
        return directory / "installed"

    monkeypatch.setattr("flowfield.harness_installs.claude_install.download_install", install)

    async def changed(directory, registration, environment):
        service = app.state.supervisor
        HarnessSettings(service.workspace).edit(
            "codex", HarnessEdit(expected_revision=registration.revision)
        )
        return harness_host.status(directory, registration, environment)

    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:
                assert (await client.get("/api/harnesses")).status_code == 200
                assert calls == []
                installed = await client.post("/api/harnesses/claude-code/install")
                assert installed.status_code == 200 and len(calls) == 1
                assert installed.json()["installing"] is False
                monkeypatch.setattr("flowfield.adapters.harness_host.check", changed)
                checked = await client.post("/api/harnesses/codex/check")
                assert (
                    checked.status_code == 409
                    and checked.json()["error"]["code"] == "harness_changed"
                )
                assert not app.state.supervisor.catalogs.jobs and not app.state.supervisor.jobs

    asyncio.run(exercise())
