"""Delivery is contextual and at-most-once; work remains the durable recovery path."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from project_fixtures import adopt, task_request

from flowfield.announcements import (
    FRESHNESS,
    MAX_CLIENTS,
    PRESENCE_TTL,
    RETAINED_EVENTS,
    Announcements,
    AnnouncementSettings,
    Presence,
    announce_update,
    reconcile_attention,
)
from flowfield.api import create_app
from flowfield.application import ProjectSetup, Workspace
from flowfield.attention import attention_counts
from flowfield.questions import QuestionCreate, Questions, QuestionWithdraw


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr("flowfield.announcements.time.time", lambda: clock[0])
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(tmp_path / "repo"), id="project"))
    workspace.create_task("project", task_request(id="task", title="Work"))
    owner = Announcements(workspace, lambda: clock[0])
    owner.start()
    return workspace, owner, clock


def client(identity="active", active=True, **kw):
    return Presence(client_id=identity, active=active, **kw)


def ask(workspace, identity="choice", project="project", task="task"):
    return Questions(workspace).ask(
        project,
        QuestionCreate(
            id=identity,
            task_id=task,
            question=f"Which {identity}?",
            context="Context",
            recommendation="Small",
            blocking_scope="Scope",
        ),
    )


def test_active_toast_exclusive_claim_and_restart(delivery):
    workspace, owner, clock = delivery
    owner.poll(client())
    owner.poll(client("background", False, permitted=True))
    owner.configure(AnnouncementSettings(system_enabled=True))
    q = ask(workspace)
    clock[0] += 3
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: owner.poll(client()), range(4)))
    items = [item for result in results for item in result.items]
    assert len(items) == 1 and items[0].source_key == f"project:question:{q.id}"
    assert all(result.channel == "toast" for result in results)
    assert not owner.poll(client("background", False, permitted=True)).items
    with workspace.connection() as db:
        assert attention_counts(db, "project")["action"] == 1
    owner.start()
    assert not owner.poll(client()).items
    assert Announcements(Workspace(workspace.directory)).settings().system_enabled


def test_burst_neighbors_share_the_first_events_settling_window(delivery):
    workspace, owner, clock = delivery
    owner.poll(client())
    ask(workspace, "first")
    clock[0] += 1.9
    ask(workspace, "second")
    assert not owner.poll(client()).items
    clock[0] += 0.2
    assert len(owner.poll(client()).items) == 2
    assert not owner.poll(client()).items


def test_background_native_preference_and_permission(delivery):
    workspace, owner, clock = delivery
    owner.configure(AnnouncementSettings(system_enabled=True))
    browser = client("browser", False, permitted=True)
    native = client("native", False, permitted=True, system="native")
    owner.poll(browser)
    owner.poll(native)
    ask(workspace)
    clock[0] += 3
    assert not owner.poll(browser).items
    result = owner.poll(native)
    assert result.channel == "system" and len(result.items) == 1
    assert not owner.poll(browser).items


def test_focus_handoff_stale_client_and_no_replay_after_uncertain_attempt(delivery):
    workspace, owner, clock = delivery
    first, second = client("first"), client("second")
    owner.poll(first)
    clock[0] += 1
    owner.poll(second)
    ask(workspace)
    clock[0] += 3
    assert not owner.poll(first).items
    assert len(owner.poll(second).items) == 1
    # No acknowledgement or retry: second may have displayed before crashing.
    clock[0] += PRESENCE_TTL + 1
    assert not owner.poll(first).items
    ask(workspace, "later")
    clock[0] += 3
    assert len(owner.poll(first).items) == 1


def test_visible_item_suppression_does_not_resolve_work(delivery):
    workspace, owner, clock = delivery
    owner.poll(client())
    ask(workspace)
    clock[0] += 3
    assert not owner.poll(client(visible_sources=["project:question:choice"])).items
    assert not owner.poll(client()).items
    with workspace.connection() as db:
        assert attention_counts(db, "project")["action"] == 1
        assert db.execute("SELECT state FROM announcement_events").fetchone()[0] == "suppressed"


def test_transactional_resolution_reopen_and_content_edits(delivery):
    workspace, owner, clock = delivery
    owner.poll(client())
    q = ask(workspace)
    Questions(workspace).withdraw(
        "project", q.id, QuestionWithdraw(expected_revision=q.revision, reason="Done")
    )
    clock[0] += 3
    assert not owner.poll(client()).items
    # A status transition back to actionable state is a fresh occurrence, even between polls.
    with workspace.connection(write=True, project_id="project") as db:
        db.execute("UPDATE questions SET status='open' WHERE id=?", (q.id,))
    with workspace.connection(write=True, project_id="project") as db:
        db.execute(
            "UPDATE questions SET data=json_set(data,'$.question','Edited') WHERE id=?", (q.id,)
        )
    clock[0] += 3
    assert len(owner.poll(client()).items) == 1
    with workspace.connection() as db:
        assert db.execute("SELECT count(*) FROM announcement_events").fetchone()[0] == 2


def test_enable_and_reconnect_baselines_permission_denial_and_expiry(delivery):
    workspace, owner, clock = delivery
    ask(workspace, "before-open")
    assert not owner.poll(client()).items
    inactive = client("active", False, permitted=False)
    owner.poll(inactive)
    ask(workspace, "blocked")
    clock[0] += 3
    assert not owner.poll(inactive).items
    owner.configure(AnnouncementSettings(system_enabled=True))
    assert not owner.poll(client(permitted=True)).items
    ask(workspace, "old")
    clock[0] += FRESHNESS + 1
    assert not owner.poll(client()).items
    ask(workspace, "fresh")
    clock[0] += 3
    assert len(owner.poll(client()).items) == 1


def test_disabled_categories_never_hide_pending_work(delivery):
    workspace, owner, clock = delivery
    owner.configure(AnnouncementSettings(work_enabled=False, updates_enabled=False))
    owner.poll(client())
    ask(workspace)
    with workspace.connection(write=True, notify=False) as db:
        announce_update(db, "9.1", clock[0])
    clock[0] += 3
    assert not owner.poll(client()).items
    owner.configure(AnnouncementSettings())
    assert not owner.poll(client()).items


def test_complete_projection_more_than_100_and_later_projects(delivery, tmp_path):
    workspace, owner, clock = delivery
    adopt(workspace, ProjectSetup(path=str(tmp_path / "other"), id="z-last"))
    owner.poll(client())
    for i in range(120):
        ask(workspace, f"q-{i}")
    workspace.create_task("z-last", task_request(id="task", title="Other"))
    ask(workspace, "last", "z-last")
    clock[0] += 3
    items = owner.poll(client()).items
    assert len(items) == 121 and any(item.project_id == "z-last" for item in items)
    with workspace.connection(write=True, notify=False) as db:
        reconcile_attention(db)
        assert db.execute("SELECT count(*) FROM attention_sources").fetchone()[0] == 121
    assert not owner.poll(client()).items


def test_manual_update_excludes_requesting_client_only(delivery):
    workspace, owner, clock = delivery
    owner.poll(client())
    with workspace.connection(write=True, notify=False) as db:
        announce_update(db, "9.1", clock[0], ["active"])
    clock[0] += 3
    assert not owner.poll(client()).items
    with workspace.connection(write=True, notify=False) as db:
        announce_update(db, "9.2", clock[0])
    clock[0] += 3
    assert owner.poll(client()).items[0].href == "/settings/updates"


def test_bounded_clients_and_receipts_do_not_revive_active_sources(delivery):
    workspace, owner, clock = delivery
    owner.poll(client())
    ask(workspace)
    clock[0] += 3
    owner.poll(client())
    with workspace.connection(write=True, notify=False) as db:
        for i in range(RETAINED_EVENTS + 10):
            announce_update(db, str(i), clock[0])
    clock[0] += 3
    owner.poll(client())
    for i in range(MAX_CLIENTS + 5):
        owner.poll(client(f"client-{i}", False))
    with workspace.connection(write=True, notify=False) as db:
        reconcile_attention(db)
        assert (
            db.execute("SELECT count(*) FROM announcement_events").fetchone()[0] <= RETAINED_EVENTS
        )
        assert db.execute("SELECT count(*) FROM announcement_clients").fetchone()[0] == MAX_CLIENTS
        assert db.execute("SELECT count(*) FROM attention_sources").fetchone()[0] == 1
    assert not owner.poll(client()).items


def test_api_retires_history_and_validates_protocol(tmp_path):
    with TestClient(create_app(data_dir=tmp_path), base_url="http://localhost") as api:
        assert api.get("/api/notifications").status_code == 404
        assert api.post("/api/notifications/operations", json={}).status_code == 404
        assert api.post("/api/announcements/poll", json={}).status_code == 422
        assert (
            api.patch("/api/announcements/settings", json={"browser_enabled": True}).status_code
            == 422
        )
        assert api.get("/api/announcements/settings").json()["system_enabled"] is False
        assert api.post("/api/announcements/poll", json=client().model_dump()).json()["items"] == []


def test_background_recipient_takes_over_after_focused_client_expires(delivery):
    workspace, owner, clock = delivery
    owner.configure(AnnouncementSettings(system_enabled=True))
    owner.poll(client())
    background = client("background", False, permitted=True)
    owner.poll(background)
    ask(workspace)
    clock[0] += 6
    assert not owner.poll(background).items
    clock[0] += 5
    result = owner.poll(background)
    assert result.channel == "system" and len(result.items) == 1


def test_permission_grant_and_focus_return_do_not_replay_backlog(delivery):
    workspace, owner, clock = delivery
    owner.configure(AnnouncementSettings(system_enabled=True))
    owner.poll(client(active=False))
    ask(workspace, "before-permission")
    clock[0] += 3
    assert not owner.poll(client(active=False, permitted=True)).items
    ask(workspace, "before-focus")
    clock[0] += 3
    assert not owner.poll(client(active=True, permitted=True)).items
    ask(workspace, "after-focus")
    clock[0] += 3
    assert len(owner.poll(client(active=True, permitted=True)).items) == 1


def test_partial_preferences_preserve_other_fields_and_unrelated_pending_events(delivery):
    workspace, owner, clock = delivery
    owner.poll(client())
    ask(workspace)
    owner.configure(AnnouncementSettings(system_enabled=True), partial=True)
    owner.configure(AnnouncementSettings(updates_enabled=False), partial=True)
    assert owner.settings() == AnnouncementSettings(system_enabled=True, updates_enabled=False)
    clock[0] += 3
    assert len(owner.poll(client()).items) == 1
