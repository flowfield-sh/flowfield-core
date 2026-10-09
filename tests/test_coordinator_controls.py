"""Native command and access-mode journeys, using scripted native processes."""

import asyncio

import pytest
from test_coordinator import message, settled, setup
from test_permissions import pending

from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.errors import ApplicationError
from flowfield.permission_models import PermissionAnswer


def test_discovery_is_shared_model_free_and_dispatch_rechecks_current_commands(
    tmp_path, monkeypatch
):
    flags = []
    service, conversation = setup(tmp_path, monkeypatch, flags=flags)

    async def exercise():
        results = await asyncio.gather(*(service.coordinator.commands("harbor") for _ in range(3)))
        assert results[0] == results[1] == results[2]
        assert len(service.coordinator.command_jobs) == 1
        commands = {item.name: item for item in results[0]}
        assert set(commands) == {"compact", "status", "mcp", "skills"}
        assert not service.coordinator.store.page("harbor").items
        assert (
            service.coordinator.store.session(
                "harbor", "codex", service.workspace.project("harbor").path
            )
            is None
        )
        status = service.coordinator.send("harbor", conversation.id, message("/status"))
        assert (await settled(service, status)).status == "completed"
        assert (
            service.coordinator.store.session(
                "harbor", "codex", service.workspace.project("harbor").path
            )
            is None
        )
        first = service.coordinator.send("harbor", conversation.id, message())
        assert (await settled(service, first)).session == "new"
        command = service.coordinator.send("harbor", conversation.id, message("/compact"))
        done = await settled(service, command)
        assert done.status == "completed" and done.session == "resumed"
        assert any(item.text == "Native command: /compact" for item in done.activity.items)
        for text in (
            "/plan",
            "/logout",
            "/goal continue",
            "/absent",
            "/rename New name",
            "/codex status",
            "/status extra",
        ):
            rejected = await settled(
                service, service.coordinator.send("harbor", conversation.id, message(text))
            )
            assert rejected.status == "failed" and not rejected.activity.items
        flags.append("no-compact")
        stale = service.coordinator.send("harbor", conversation.id, message("/compact"))
        assert (await settled(service, stale)).status == "failed"
        await service.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "mode,answer", [("read-only", "deny"), ("agent", "allow"), ("workspace-write", "stop")]
)
def test_coordinator_native_mode_and_permission_lifetime(tmp_path, monkeypatch, mode, answer):
    service, conversation = setup(tmp_path, monkeypatch, scenario="permission")
    monkeypatch.setenv("FLOWFIELD_TEST_MODE", mode)
    settings = AgentSettings(service.workspace)
    saved = settings.edit(
        "harbor",
        "coordinator",
        AgentSettingsEdit(
            expected_revision=2, selection=AgentChoice(model="test-model", effort="low", mode=mode)
        ),
    )

    async def exercise():
        turn = service.coordinator.send(
            "harbor", conversation.id, message("Set up this environment")
        )
        record = await pending(service.permissions)
        assert record.role == "coordinator" and record.conversation_id == conversation.id
        assert not service.permissions.page("harbor", role="worker").pending
        assert service.permissions.page("harbor", role="coordinator").pending[0].id == record.id
        settings.edit(
            "harbor",
            "coordinator",
            AgentSettingsEdit(
                expected_revision=saved.revision,
                selection=AgentChoice(model="test-model", effort="low", mode="read-only"),
            ),
        )
        if answer == "stop":
            await service.coordinator.stop("harbor", turn.id)
            with pytest.raises(ApplicationError):
                service.permissions.answer(
                    "harbor", record.id, PermissionAnswer(expected_revision=1, option_id="allow")
                )
        else:
            service.permissions.answer(
                "harbor", record.id, PermissionAnswer(expected_revision=1, option_id=answer)
            )
        done = await settled(service, turn)
        assert done.applied.choice.mode == mode
        assert done.settings.choice.mode == mode
        assert done.status == ("stopped" if answer == "stop" else "completed")
        assert not service.permissions.page("harbor", role="coordinator").pending
        await service.close()

    asyncio.run(exercise())
