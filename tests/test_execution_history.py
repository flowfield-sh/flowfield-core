"""History separates actual work from human review and preserves result lineage."""

import pytest
from result_fixtures import approve, current, fixture

from flowfield.errors import ApplicationError
from flowfield.execution_history import ExecutionHistory
from flowfield.execution_models import RunAction


def test_history_has_typed_work_and_result_bound_pagination(tmp_path):
    service, _, run = fixture(tmp_path)
    history = ExecutionHistory(service.workspace)
    worker = history.page("harbor", "work").items[0]
    assert worker.kind == "worker" and worker.status == "completed"
    assert worker.result_id == current(service).id
    service.results.process("harbor")
    ready = current(service)
    rows = history.page("harbor", "work").items
    assert {row.kind for row in rows} == {"worker", "validation"}
    validation = next(row for row in rows if row.kind == "validation")
    assert validation.status == "completed"  # Waiting for review isn't active execution.
    completed = validation.ended_at
    approve(service, ready)
    pending = history.page("harbor", "work").items[0]
    assert pending.kind == "delivery" and pending.status == "queued"
    service.results.process("harbor")
    rows = history.page("harbor", "work").items
    assert [row.kind for row in rows] == ["delivery", "validation", "worker"]
    assert rows[0].status == "completed" and rows[1].ended_at == completed
    first = history.page("harbor", "work", limit=1)
    rest = history.page("harbor", "work", offset=first.next_offset)
    assert [*first.items, *rest.items] == rows
    assert history.get("harbor", "work", f"worker:{run.id}") == rows[-1]
    with pytest.raises(ApplicationError, match="not found"):
        history.get("harbor", "work", "worker:missing")


def test_repreparation_preserves_old_evidence_and_changes_current_version(tmp_path):
    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    old = current(service)
    new = service.results.reprepare("harbor", old.id, RunAction(expected_revision=old.revision))
    service.results.process("harbor")
    page = service.results.page("harbor", "work", limit=1)
    assert page.current_id == new.id and page.current_run_id == new.run_id
    previous = service.results.page("harbor", "work", before=page.next_before)
    assert previous.items[0].id == old.id and previous.current_id == new.id
    with pytest.raises(ApplicationError, match="newer result"):
        approve(service, old)
    history = ExecutionHistory(service.workspace).page("harbor", "work").items
    assert len([r for r in history if r.kind == "validation"]) == 2
    assert {r.result_id for r in history} == {old.id, new.id}


def test_validation_cannot_claim_success_when_a_check_changes_source(tmp_path):
    service, _, _ = fixture(tmp_path, checks=["printf modified >> result.txt"])
    service.results.process("harbor")
    history = ExecutionHistory(service.workspace).page("harbor", "work").items
    assert history[0].kind == "validation" and history[0].status == "failed"
    assert current(service).status == "blocked"
