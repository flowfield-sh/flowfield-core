"""The visible workspace order determines eligible starts across independent repositories."""

import asyncio

import pytest
from project_fixtures import fixture_stage_change, task_request
from test_execution import BASE
from test_workspace_workers import shared

from flowfield import migrations
from flowfield.application import TaskPriority, TaskPublish, Workspace
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.supervisor import Supervisor


@pytest.mark.parametrize("blocked_site", [False, True])
def test_scheduler_follows_shared_priority_and_skips_unavailable_repository(
    tmp_path, monkeypatch, blocked_site
):
    store, _, space, execution = shared(tmp_path, cap=2)
    extra = store.create_task(
        "core",
        task_request(title="Another report", body="Investigate another outcome.", status="up_next"),
    )
    store.publish_task(
        "core",
        extra.id,
        TaskPublish(
            expected_revision=extra.revision,
            completion="report",
            stages=fixture_stage_change(store, "core", extra.id),
        ),
    )
    site = store.task("site", "work")
    store.prioritize_task(
        "site",
        site.id,
        TaskPriority(expected_revision=site.revision, status="up_next", before_id="COR-1"),
    )
    current = store.task("core", extra.id)
    store.prioritize_task(
        "core",
        extra.id,
        TaskPriority(expected_revision=current.revision, status="up_next", before_id="COR-1"),
    )
    board = BrowserReads(store).workspace_board(space)
    assert [task.key for task in sorted(board.tasks, key=lambda task: task.position)] == [
        "SIT-1",
        "COR-2",
        "COR-1",
    ]
    assert execution.scheduling_candidates() == [
        ("site", "work"),
        ("core", extra.id),
        ("core", "work"),
    ]
    service = Supervisor(store)
    started = []

    def head(project):
        if blocked_site and project == "site":
            raise ApplicationError("setup_required", "Site needs repository setup.", 409)
        return BASE

    async def execute(run, repository):
        started.append(run.task_key)

    monkeypatch.setattr(service.integrations, "head", head)
    monkeypatch.setattr(service.integrations, "refresh_availability", lambda project: None)
    monkeypatch.setattr(service.results, "process", lambda project: None)
    monkeypatch.setattr(service, "_available", lambda *args: {BASE: set()})
    monkeypatch.setattr(service, "_execute", execute)

    async def exercise():
        await service._schedule_once()
        await asyncio.gather(*service.jobs.values(), *service.delivery_jobs.values())

    asyncio.run(exercise())
    assert started == (["COR-2", "COR-1"] if blocked_site else ["SIT-1", "COR-2"])
    assert len(execution.active()) == 2
    if blocked_site:
        assert execution.settings("site").problem == "Site needs repository setup."
        assert BrowserReads(store).workspace_board(space).project_problems == {
            "site": "Site needs repository setup."
        }


def test_priority_migration_only_changes_order_metadata(tmp_path, monkeypatch):
    history = migrations.MIGRATIONS
    with monkeypatch.context() as old:
        old.setattr(migrations, "MIGRATIONS", tuple(item for item in history if item.version <= 62))
        store, _, space, _ = shared(tmp_path)
        with store.connection() as db:
            before = [
                tuple(row)
                for row in db.execute("SELECT project_id,id,data FROM tasks ORDER BY project_id,id")
            ]
            revisions = [
                tuple(row)
                for row in db.execute(
                    "SELECT * FROM task_revisions ORDER BY project_id,task_id,revision"
                )
            ]
    upgraded = Workspace(store.directory)
    with upgraded.connection() as db:
        assert [
            tuple(row)
            for row in db.execute("SELECT project_id,id,data FROM tasks ORDER BY project_id,id")
        ] == before
        assert [
            tuple(row)
            for row in db.execute(
                "SELECT * FROM task_revisions ORDER BY project_id,task_id,revision"
            )
        ] == revisions
    assert len({task.position for task in BrowserReads(upgraded).workspace_board(space).tasks}) == 2
