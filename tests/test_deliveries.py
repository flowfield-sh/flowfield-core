"""Durable delivery paging is independent of reading, edits and active requests."""

import pytest
from file_delivery_fixtures import deliver, setup, task

from flowfield.application import TaskEdit
from flowfield.deliveries import delivery_page
from flowfield.errors import ApplicationError
from flowfield.execution_models import WorkerResult


def test_deliveries_page_by_delivery_time_and_exclude_unfinished_archived(tmp_path):
    store, _, scope = setup(tmp_path / "state")
    first = task(store, scope, title="First report")
    one = deliver(store, scope, first.id, {f"part-{n}.txt": "Evidence" for n in range(5)})
    second = task(store, scope, title="Second report")
    two = deliver(store, scope, second.id, {"result.txt": "New evidence"})
    pending = task(store, scope, title="Unfinished report")
    deliver(
        store,
        scope,
        pending.id,
        report=WorkerResult(
            outcome="partial", summary="Draft", checks="Read", remaining_work="Finish"
        ),
    )
    current = store.task(scope, first.id)
    store.edit_task(scope, first.id, TaskEdit(expected_revision=current.revision, labels=[]))
    page = delivery_page(store, scope, limit=1)
    assert [item.id for item in page.items] == [two.id]
    assert delivery_page(store, scope, limit=1) == page
    third = task(store, scope, title="New arrival")
    three = deliver(store, scope, third.id)
    older = delivery_page(store, scope, before=page.next_cursor, limit=1)
    assert [item.id for item in older.items] == [one.id]
    assert older.next_cursor is None
    assert older.items[0].file_count == 5 and len(older.items[0].files) == 3
    assert [item.id for item in delivery_page(store, scope).items] == [three.id, two.id, one.id]
    current = store.task(scope, second.id)
    store.edit_task(scope, second.id, TaskEdit(expected_revision=current.revision, archived=True))
    assert [item.id for item in delivery_page(store, scope).items] == [three.id, one.id]
    assert not delivery_page(store, scope, project_id="unrelated").items
    with pytest.raises(ApplicationError, match="cursor"):
        delivery_page(store, scope, before='[4, "id"]')
    with pytest.raises(ApplicationError):
        delivery_page(store, "other-workspace")
