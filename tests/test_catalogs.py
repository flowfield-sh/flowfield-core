"""Discovery admission, cancellation/restart, native cleanup and scoped cache bounds."""

import asyncio

import pytest
from test_execution import fixture

from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.catalogs import Catalogs
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption
from flowfield.harness_models import HarnessEdit
from flowfield.harness_settings import HarnessSettings


def test_caller_cancellation_keeps_native_owner_and_coalesces_requests(tmp_path, monkeypatch):
    workspace = fixture(tmp_path).workspace
    catalogs = Catalogs(workspace)
    registration = HarnessSettings(workspace).get("codex")
    release = asyncio.Event()
    calls = []

    async def discover(directory, *, registration, cwd, on_cleanup):
        calls.append((registration.harness, cwd))
        assert catalogs.ownership(registration.harness).status == "running"
        on_cleanup(False)
        await release.wait()
        on_cleanup(True)
        return [ModelOption(id="exact", name="Exact", efforts=["low"])]

    monkeypatch.setattr("flowfield.catalogs.model_options", discover)

    async def exercise():
        waiter = asyncio.create_task(catalogs.run(registration, project_id="harbor"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        identity = catalogs.ownership("codex").id
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert catalogs.ownership("codex").id == identity
        with pytest.raises(ApplicationError, match="still owned"):
            catalogs.confirm_stopped("codex", identity)
        next_waiter = asyncio.create_task(catalogs.run(registration, project_id="harbor"))
        other_scope = asyncio.create_task(catalogs.run(registration))
        await asyncio.sleep(0)
        assert not other_scope.done() and len(calls) == 1
        release.set()
        models = await next_waiter
        assert len(calls) == 1 and str(calls[0][1]) == workspace.project("harbor").path
        await other_scope
        assert len(calls) == 2 and calls[1][1] is None
        assert catalogs.ownership("codex") is None
        models[0].id = "mutated"
        assert (await catalogs.run(registration, project_id="harbor"))[0].id == "exact"
        await catalogs.close()

    asyncio.run(exercise())


def test_uncertain_cleanup_blocks_retry_and_survives_restart_until_exact_confirmation(
    tmp_path, monkeypatch
):
    workspace = fixture(tmp_path).workspace
    catalogs = Catalogs(workspace)
    registration = HarnessSettings(workspace).get("codex")
    calls = 0

    async def discover(directory, *, on_cleanup, **kwargs):
        nonlocal calls
        calls += 1
        on_cleanup(False)
        return [ModelOption(id="exact", name="Exact", efforts=["low"])]

    monkeypatch.setattr("flowfield.catalogs.model_options", discover)

    async def exercise():
        with pytest.raises(ApplicationError, match="cleanup is unconfirmed"):
            await catalogs.run(registration)
        hold = catalogs.ownership("codex")
        assert hold.status == "uncertain"
        restored = Catalogs(workspace)
        restored.restart()
        for owner in (catalogs, restored):
            with pytest.raises(ApplicationError, match="cleanup is unconfirmed"):
                await owner.run(registration, refresh=True)
        assert calls == 1
        with pytest.raises(ApplicationError, match="Reload harness status"):
            restored.confirm_stopped("codex", "stale")
        assert restored.ownership("codex").id == hold.id
        restored.confirm_stopped("codex", hold.id)
        assert restored.ownership("codex") is None
        await catalogs.close()
        await restored.close()

    asyncio.run(exercise())


def test_host_changes_during_discovery_do_not_return_stale_choices(tmp_path, monkeypatch):
    workspace = fixture(tmp_path).workspace
    catalogs = Catalogs(workspace)
    registration = HarnessSettings(workspace).get("codex")

    async def discover(directory, *, on_cleanup, **kwargs):
        HarnessSettings(workspace).edit("codex", HarnessEdit(expected_revision=1))
        on_cleanup(True)
        return [ModelOption(id="exact", name="Exact", efforts=["low"])]

    monkeypatch.setattr("flowfield.catalogs.model_options", discover)

    async def exercise():
        with pytest.raises(ApplicationError, match="Host settings changed"):
            await catalogs.run(registration)
        assert catalogs.ownership("codex") is None
        await catalogs.close()

    asyncio.run(exercise())


def test_two_harnesses_have_independent_bounded_owners(tmp_path, monkeypatch):
    workspace = fixture(tmp_path).workspace
    catalogs = Catalogs(workspace)
    release = asyncio.Event()
    started = set()

    async def discover(directory, *, registration, on_cleanup, **kwargs):
        on_cleanup(False)
        started.add(registration.harness)
        await release.wait()
        on_cleanup(True)
        return [ModelOption(id=registration.harness, name="Native", efforts=[])]

    monkeypatch.setattr("flowfield.catalogs.model_options", discover)

    async def exercise():
        tasks = [
            asyncio.create_task(catalogs.run(item)) for item in HarnessSettings(workspace).all()
        ]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert started == {"codex", "claude-code"}
        assert len(catalogs.jobs) == 2
        assert all(catalogs.ownership(kind).status == "running" for kind in started)
        release.set()
        values = await asyncio.gather(*tasks)
        assert {item[0].id for item in values} == started
        assert all(catalogs.ownership(kind) is None for kind in started)
        await catalogs.close()

    asyncio.run(exercise())


def test_catalog_cache_has_eight_entries_and_revision_changes_invalidate_it(tmp_path, monkeypatch):
    workspace = fixture(tmp_path).workspace
    catalogs = Catalogs(workspace)
    settings = HarnessSettings(workspace)
    calls = 0

    async def discover(directory, *, on_cleanup, **kwargs):
        nonlocal calls
        calls += 1
        on_cleanup(True)
        return [ModelOption(id="exact", name="Native", efforts=[])]

    monkeypatch.setattr("flowfield.catalogs.model_options", discover)

    async def exercise():
        for _ in range(10):
            registration = settings.get("codex")
            await catalogs.run(registration)
            settings.edit("codex", HarnessEdit(expected_revision=registration.revision))
        assert len(catalogs.cache) == 8 and calls == 10
        with pytest.raises(ApplicationError, match="Host settings changed"):
            await catalogs.run(registration)
        assert catalogs.ownership("codex") is None
        await catalogs.close()

    asyncio.run(exercise())


def test_commands_and_models_share_one_native_discovery_owner(tmp_path, monkeypatch):
    workspace = fixture(tmp_path).workspace
    catalogs = Catalogs(workspace)
    registration = HarnessSettings(workspace).get("codex")
    started = asyncio.Event()
    release = asyncio.Event()

    async def discover(directory, cwd, choice, *, on_cleanup, **kwargs):
        record = catalogs.ownership("codex")
        assert record.operation == "commands" and record.project_id == "harbor"
        on_cleanup(False)
        started.set()
        await release.wait()
        # Scripted peer cannot confirm native cleanup after command discovery.
        return [AgentCommand(name="status", description="Status")]

    monkeypatch.setattr("flowfield.catalogs.command_options", discover)

    async def exercise():
        task = asyncio.create_task(
            catalogs.commands(registration, "harbor", AgentChoice(model="exact", effort="low"))
        )
        await started.wait()
        with pytest.raises(ApplicationError, match="still running"):
            await catalogs.run(registration)
        release.set()
        with pytest.raises(ApplicationError, match="cleanup is unconfirmed"):
            await task
        assert catalogs.ownership("codex").status == "uncertain"
        with pytest.raises(ApplicationError, match="cleanup is unconfirmed"):
            await catalogs.run(registration, refresh=True)
        await catalogs.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("confirmed", [True, False])
def test_service_shutdown_waits_for_cleanup_and_preserves_uncertainty(
    tmp_path, monkeypatch, confirmed
):
    workspace = fixture(tmp_path).workspace
    catalogs = Catalogs(workspace)
    registration = HarnessSettings(workspace).get("codex")
    started = asyncio.Event()
    finished = asyncio.Event()

    async def discover(directory, *, on_cleanup, **kwargs):
        on_cleanup(False)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)
            on_cleanup(confirmed)
            finished.set()

    monkeypatch.setattr("flowfield.catalogs.model_options", discover)

    async def exercise():
        waiter = asyncio.create_task(catalogs.run(registration))
        await started.wait()
        await catalogs.close()
        assert finished.is_set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        record = catalogs.ownership("codex")
        assert record is None if confirmed else record.status == "uncertain"

    asyncio.run(exercise())


@pytest.mark.parametrize("cleaned", [True, False])
def test_waiting_scope_does_not_inherit_other_error_or_bypass_cleanup(
    tmp_path, monkeypatch, cleaned
):
    workspace = fixture(tmp_path).workspace
    catalogs = Catalogs(workspace)
    registration = HarnessSettings(workspace).get("codex")
    release = asyncio.Event()
    calls = []

    async def discover(directory, *, cwd, on_cleanup, **kwargs):
        calls.append(cwd)
        if cwd is not None:
            await release.wait()
            on_cleanup(cleaned)
            raise ApplicationError("unavailable_here", "Unavailable for first project", 409)
        on_cleanup(True)
        return [ModelOption(id="other", name="Other", efforts=[])]

    monkeypatch.setattr("flowfield.catalogs.model_options", discover)

    async def exercise():
        first = asyncio.create_task(catalogs.run(registration, project_id="harbor"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        second = asyncio.create_task(catalogs.run(registration))
        await asyncio.sleep(0)
        assert len(calls) == 1 and not second.done()
        release.set()
        with pytest.raises(ApplicationError, match="Unavailable for first"):
            await first
        if cleaned:
            assert (await second)[0].id == "other"
            assert len(calls) == 2
        else:
            with pytest.raises(ApplicationError, match="cleanup is unconfirmed"):
                await second
            assert len(calls) == 1
        await catalogs.close()

    asyncio.run(exercise())
