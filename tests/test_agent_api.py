"""HTTP choices and permission delivery use the same service owner as harness callbacks."""

import asyncio

import httpx
from test_execution import BASE, fixture
from test_permissions import OPTIONS, pending

from flowfield.api import create_app
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption, QueueEdit
from flowfield.harness_models import HarnessEdit
from flowfield.harness_settings import HarnessSettings
from flowfield.supervisor import Supervisor


def test_settings_validation_and_permission_http_journey(tmp_path, monkeypatch):
    async def models(self, **kwargs):
        return [ModelOption(id="supported", name="Supported", efforts=["low", "high"])]

    monkeypatch.setattr(Supervisor, "model_options", models)
    monkeypatch.setenv("FLOWFIELD_UPDATE_CHECKS", "0")
    execution = fixture(tmp_path)
    app = create_app(data_dir=execution.workspace.directory)

    async def exercise():
        async with app.router.lifespan_context(app):
            service = app.state.supervisor
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:
                path = "/api/projects/harbor/tasks/task-0/agent-settings"
                initial = (await client.get(path)).json()
                assert initial["effective"]["choice"]["model"] == "test-model"
                bad = await client.put(
                    path,
                    json={
                        "expected_revision": 1,
                        "selection": {"model": "missing", "effort": "low"},
                    },
                )
                assert bad.status_code == 409
                extra = await client.put(
                    path,
                    json={
                        "expected_revision": 1,
                        "selection": {"model": "supported", "effort": "low", "permission": "full"},
                    },
                )
                assert extra.status_code == 422
                saved = await client.put(
                    path,
                    json={
                        "expected_revision": 1,
                        "selection": {"model": "supported", "effort": "high"},
                    },
                )
                assert saved.status_code == 200
                assert (
                    await client.put(path, json={"expected_revision": 1, "selection": None})
                ).status_code == 409
                coordinator = await client.put(
                    "/api/projects/harbor/coordinator-settings",
                    json={
                        "expected_revision": 1,
                        "selection": {"model": "supported", "effort": "low"},
                    },
                )
                assert coordinator.status_code == 200
                settings = service.execution.settings("harbor")
                service.execution.queue(
                    "harbor", QueueEdit(expected_revision=settings.revision, enabled=True)
                )
                run = service.execution.claim("harbor", BASE, {BASE: set()})
                service.execution.started("harbor", run.id)
                assert run.model == "supported" and run.effort == "high"
                async with service.permissions.turn(
                    "harbor", "worker", session_id="fake-native", turn_id="turn", run_id=run.id
                ) as turn:
                    callback = asyncio.create_task(
                        turn.request("check", "Run project check", OPTIONS)
                    )
                    record = await pending(service.permissions)
                    page = (await client.get("/api/projects/harbor/permissions")).json()
                    assert page["pending"][0]["id"] == record.id
                    response = await client.post(
                        f"/api/projects/harbor/permissions/{record.id}/answer",
                        json={"expected_revision": 1, "option_id": "no"},
                    )
                    assert response.status_code == 200
                    assert await callback == "no"
                    assert (
                        await client.get(f"/api/projects/harbor/permissions/{record.id}")
                    ).json()["answer"] == "no"
                assert (
                    await client.post("/api/projects/harbor/permissions", json={})
                ).status_code == 404
                assert service.execution.get("harbor", run.id).status == "running"
                service.execution.finish("harbor", run.id, "stopped")

    asyncio.run(exercise())


def test_model_catalog_coalesces_caches_refreshes_and_retries(tmp_path, monkeypatch):
    calls = 0
    fail = False

    async def discover(directory, *, registration=None, cwd=None, on_cleanup=None):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        on_cleanup(True)
        if fail:
            raise RuntimeError("discovery failed")
        return [ModelOption(id="supported", name="Supported", efforts=["low"])]

    monkeypatch.setattr("flowfield.catalogs.model_options", discover)
    service = Supervisor(fixture(tmp_path).workspace)

    async def exercise():
        nonlocal fail
        first, second = await asyncio.gather(service.model_options(), service.model_options())
        assert first == second and calls == 1
        assert await service.model_options() == first and calls == 1
        assert await service.model_options(refresh=True) == first and calls == 2
        next(iter(service.catalogs.cache.values())).at -= 301
        assert await service.model_options() == first and calls == 3
        fail = True
        try:
            await service.model_options(refresh=True)
        except ApplicationError:
            pass
        else:
            raise AssertionError("Discovery failure was hidden")
        fail = False
        assert await service.model_options() == first and calls == 5
        HarnessSettings(service.workspace).edit("codex", HarnessEdit(expected_revision=1))
        assert await service.model_options() == first and calls == 6
        await service.close()

    asyncio.run(exercise())
