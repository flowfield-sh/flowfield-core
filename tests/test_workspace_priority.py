"""The visible workspace order determines eligible starts across independent repositories."""

import asyncio

import pytest
from execution_fixtures import BASE
from project_fixtures import task_request
from workspace_workers_fixtures import shared

from flowfield.application import TaskPriority, TaskPublish
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.supervisor import Supervisor


@pytest.mark.parametrize("blocked_site", [False, True])
def test_scheduler_follows_shared_priority_and_skips_unavailable_repository(
    tmp_path, monkeypatch, blocked_site
):
    store, _, space, execution = shared(tmp_path, cap=2)
    extra = store.create_task(
        space,
        task_request(
            project_id="core",
            title="Another report",
            body="Investigate another outcome.",
            status="up_next",
        ),
    )
    store.publish_task(
        space,
        extra.id,
        TaskPublish(
            expected_revision=extra.revision,
            completion="report",
        ),
    )
    site = store.task(space, "site")
    store.prioritize_task(
        space,
        site.id,
        TaskPriority(expected_revision=site.revision, status="up_next", before_id="T-1"),
    )
    current = store.task(space, extra.id)
    store.prioritize_task(
        space,
        extra.id,
        TaskPriority(expected_revision=current.revision, status="up_next", before_id="T-1"),
    )
    board = BrowserReads(store).workspace_board(space)
    assert [task.key for task in sorted(board.tasks, key=lambda task: task.position)] == [
        "T-2",
        "T-3",
        "T-1",
    ]
    assert execution.scheduling_candidates() == [
        (space, "site"),
        (space, extra.id),
        (space, "core"),
    ]
    service = Supervisor(store)
    started = []

    def head(project, task_id=None):
        if blocked_site and task_id == "site":
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
    assert started == (["T-3", "T-1"] if blocked_site else ["T-2", "T-3"])
    assert len(execution.active()) == 2
    if blocked_site:
        assert BrowserReads(store).task(space, "site").state.label == "Site needs repository setup."
        assert BrowserReads(store).task(space, "site").state.label == "Site needs repository setup."
