"""Service restart is distinct from read/reconnect and explicit session replacement."""

import time

import pytest
from coordinator_fixtures import message, setup
from coordinator_restart_fixtures import completed, methods, running_service

from flowfield.application import Workspace
from flowfield.coordinator_store import CoordinatorStore
from flowfield.execution_models import QueueEdit


def isolated(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    queue = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=queue.revision, enabled=False))
    native = tmp_path / "native"
    native.mkdir()
    return service.workspace.directory, conversation, native


def test_idle_service_restart_resumes_without_replay_or_recovery(tmp_path, monkeypatch):
    directory, conversation, native = isolated(tmp_path, monkeypatch)
    first = message().model_dump(mode="json")
    with running_service(directory, native, tmp_path / "first.log") as client:
        done = completed(client, first)
        assert done["status"] == "completed", done
        assert done["session"] == "new"
    before = methods(native)
    store = CoordinatorStore(Workspace(directory))
    with store.workspace.connection() as db:
        original, fresh = store.sessions.current(db, "harbor")
        assert original and not fresh
    # Separate operating-system service processes, opening the same isolated store.
    for index in range(2):
        with running_service(directory, native, tmp_path / f"restart-{index}.log") as client:
            for _ in range(3):
                page = client.get(f"/api/workspaces/harbor/coordinator/{conversation.id}").json()
                assert page["session_recovery_turn_id"] is None and page["active"] is None
                assert page["items"][0] == done
            assert methods(native) == before  # Reads/startup never launch or replay.
            assert completed(client, first) == done  # Reconciled receipt is not resent.
            assert methods(native) == before
            resumed = completed(client, message("Check continuity").model_dump(mode="json"))
            assert resumed["status"] == "completed", resumed
            assert resumed["session"] == "resumed"
            assert all(item["key"] != "session" for item in resumed["activity"]["items"])
        before = methods(native)
    assert before.count("thread/start") == 1
    assert before.count("thread/resume") == 2
    assert before.count("turn/start") == 3
    with store.workspace.connection() as db:
        current, fresh = store.sessions.current(db, "harbor")
        assert current.id == original.id and current.session_id == original.session_id
        assert not fresh


@pytest.mark.parametrize("invalid,native_started", [(False, False), (True, False), (False, True)])
def test_unavailable_session_recovery_survives_interrupted_retry_and_reset_restart(
    tmp_path, monkeypatch, invalid, native_started
):
    directory, conversation, native = isolated(tmp_path, monkeypatch)
    with running_service(directory, native, tmp_path / "first.log") as client:
        assert completed(client, message().model_dump(mode="json"))["session"] == "new"
    # Missing or invalid persisted history in the controlled peer, not a flag
    # that rejects every resume (including an explicitly selected replacement).
    if invalid:
        (native / "session").write_text("invalid-native-identity")
    else:
        (native / "session").unlink()
    with running_service(directory, native, tmp_path / "missing.log") as client:
        failed = completed(client, message().model_dump(mode="json"))
        assert failed["status"] == "failed" and failed["session"] == "unavailable"
        assert "Nothing was replayed" in failed["notice"]
    before = methods(native)
    store = CoordinatorStore(Workspace(directory))
    # Deterministic crash boundary: accepted input with/without native ownership.
    pending, _ = store.reserve("harbor", conversation.id, message("Retry after fixing the host"))
    if native_started:
        with store.workspace.connection(write=True) as db:
            current, _ = store.sessions.current(db, "harbor")
            store.sessions.prepare(
                db,
                "harbor",
                pending.id,
                pending.settings.choice,
                current.cwd,
                current.launch,
                project_directories=current.project_directories,
            )
            pending.native_started = True
            store._save(db, pending)
        with running_service(directory, native, tmp_path / "uncertain.log") as client:
            page = client.get("/api/workspaces/harbor/coordinator").json()
            assert page["active"]["status"] == "uncertain"
            assert page["session_recovery_turn_id"] is None
            assert (
                client.post(
                    "/api/workspaces/harbor/coordinator/messages",
                    json=message().model_dump(mode="json"),
                ).status_code
                == 409
            )
            assert (
                client.post(
                    f"/api/workspaces/harbor/coordinator/turns/{failed['id']}/reset-session"
                ).status_code
                == 409
            )
            assert methods(native) == before
    with running_service(directory, native, tmp_path / "recovery.log") as client:
        page = client.get("/api/workspaces/harbor/coordinator").json()
        assert page["items"][-1]["id"] == pending.id
        if native_started:
            assert page["active"]["status"] == "uncertain"
            assert page["session_recovery_turn_id"] is None
            assert (
                client.post(
                    f"/api/workspaces/harbor/coordinator/turns/{pending.id}/confirm-stopped"
                ).status_code
                == 200
            )
            page = client.get("/api/workspaces/harbor/coordinator").json()
        assert page["items"][-1]["status"] == "interrupted"
        assert page["session_recovery_turn_id"] == failed["id"]
        assert methods(native) == before
        reset = f"/api/workspaces/harbor/coordinator/turns/{failed['id']}/reset-session"
        assert client.post(reset).status_code == 204
        assert client.post(reset).status_code == 409
    with running_service(directory, native, tmp_path / "reset.log") as client:
        page = client.get("/api/workspaces/harbor/coordinator").json()
        assert page["session_recovery_turn_id"] is None
        assert len(page["items"]) == 3 and methods(native) == before
        fresh = completed(client, message("Continue with saved chat").model_dump(mode="json"))
        assert fresh["session"] == "new" and fresh["status"] == "completed", fresh
        assert "history" in fresh["activity"]["items"][0]["text"]
    with running_service(directory, native, tmp_path / "after-reset.log") as client:
        resumed = completed(client, message("Check continuity").model_dump(mode="json"))
        assert resumed["session"] == "resumed" and resumed["status"] == "completed", resumed
        assert (
            client.get("/api/workspaces/harbor/coordinator").json()["session_recovery_turn_id"]
            is None
        )
    assert methods(native).count("turn/start") == 3
    assert methods(native).count("thread/start") == 2


def test_graceful_restart_during_active_turn_does_not_replay(tmp_path, monkeypatch):
    directory, _, native = isolated(tmp_path, monkeypatch)
    monkeypatch.setenv("FLOWFIELD_TEST_SCENARIO", "wait")
    request = message().model_dump(mode="json")
    with running_service(directory, native, tmp_path / "active.log") as client:
        assert (
            client.post("/api/workspaces/harbor/coordinator/messages", json=request).status_code
            == 202
        )
        deadline = time.monotonic() + 15
        while True:
            page = client.get("/api/workspaces/harbor/coordinator").json()
            if any(item["text"] == "started" for item in page["items"][0]["activity"]["items"]):
                break
            assert time.monotonic() < deadline, page
            time.sleep(0.02)
    before = methods(native)
    monkeypatch.setenv("FLOWFIELD_TEST_SCENARIO", "normal")
    with running_service(directory, native, tmp_path / "continued.log") as client:
        page = client.get("/api/workspaces/harbor/coordinator").json()
        assert page["active"] is None and page["items"][0]["status"] == "stopped"
        assert methods(native) == before
        assert completed(client, request)["status"] == "stopped"
        assert methods(native) == before
        resumed = completed(client, message("Check continuity").model_dump(mode="json"))
        assert resumed["status"] == "completed" and resumed["session"] == "resumed", resumed
    assert methods(native).count("turn/start") == 2


def test_host_repair_and_successful_resume_resolve_recovery(tmp_path, monkeypatch):
    directory, conversation, native = isolated(tmp_path, monkeypatch)
    with running_service(directory, native, tmp_path / "first.log") as client:
        assert completed(client, message().model_dump(mode="json"))["session"] == "new"
    (native / "session").unlink()
    with running_service(directory, native, tmp_path / "missing.log") as client:
        failed = completed(client, message().model_dump(mode="json"))
        assert failed["session"] == "unavailable"
    # Restoring the same controlled peer's history is an alternative to reset.
    (native / "session").write_text("test-session")
    with running_service(directory, native, tmp_path / "repaired.log") as client:
        assert (
            completed(client, message("Check continuity").model_dump(mode="json"))["session"]
            == "resumed"
        )
        assert (
            client.post(
                f"/api/workspaces/harbor/coordinator/turns/{failed['id']}/reset-session"
            ).status_code
            == 409
        )
    store = CoordinatorStore(Workspace(directory))
    store.reserve("harbor", conversation.id, message("Not dispatched"))
    with running_service(directory, native, tmp_path / "later.log") as client:
        page = client.get("/api/workspaces/harbor/coordinator").json()
        assert page["session_recovery_turn_id"] is None
        assert page["items"][-1]["status"] == "interrupted"
    assert methods(native).count("thread/start") == 1
    assert methods(native).count("turn/start") == 2
