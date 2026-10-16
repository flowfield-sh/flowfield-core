"""The worker capability boundary fixes project/task/run identity on the server."""

import asyncio
import json
from pathlib import Path

import pytest
from execution_fixtures import BASE, fixture
from pydantic import ValidationError

from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction
from flowfield.supervisor import WorkerBridge


def test_scope_pages_closed_report_and_late_writes(tmp_path: Path) -> None:
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    execution.started("harbor", run.id)
    bridge = WorkerBridge(execution, run, object())

    async def exercise():
        assert json.loads(await bridge.call("read_context", {"section": "description"}))["text"]
        with pytest.raises(ValidationError):
            await bridge.call("read_context", {"section": "description", "task_id": "other"})
        for name in ["edit_task", "review_run", "set_queue", "create_decision"]:
            with pytest.raises(ApplicationError, match="outside the worker"):
                await bridge.call(name, {})
        await bridge.call(
            "submit_result", {"outcome": "complete", "summary": "Done", "checks": "Actual checks"}
        )
        with pytest.raises(ApplicationError, match="report is saved"):
            await bridge.call("run_command", {"command": "touch late"})
        bridge.result = None
        latest = execution.get("harbor", run.id)
        execution.stop_requested("harbor", run.id, RunAction(expected_revision=latest.revision))
        with pytest.raises(ApplicationError):
            await bridge.call(
                "submit_result", {"outcome": "complete", "summary": "Late", "checks": "Late"}
            )

    asyncio.run(exercise())
