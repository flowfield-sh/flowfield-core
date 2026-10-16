"""Reusable isolated test support; importing this module never collects a test suite."""

from pathlib import Path
from unittest.mock import patch

from project_fixtures import adopt, fixture_stage_change, task_request

from flowfield.agent_models import AgentChoice
from flowfield.application import (
    ProjectSetup,
    TaskPublish,
    Workspace,
)
from flowfield.execution import Execution
from flowfield.execution_models import (
    QueueEdit,
    SettingsEdit,
    WorkerResult,
)
from flowfield.results import Results

BASE, RESULT = "a" * 40, "b" * 40


def fixture(tmp_path: Path, *, count: int = 1, cap: int = 1, stages=None) -> Execution:
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(tmp_path / "harbor")))
    for index in range(count):
        task = workspace.create_task(
            "harbor",
            task_request(
                **({"stages": stages} if stages is not None else {}),
                id=f"task-{index}",
                title=f"Task {index}",
                body="Implement and test the assigned behavior.",
                status="up_next",
            ),
        )
        workspace.publish_task(
            "harbor",
            task.id,
            TaskPublish(
                stages=fixture_stage_change(workspace, "harbor", task.id),
                completion="report",
                expected_revision=task.revision,
            ),
        )
    execution = Execution(workspace)
    configured = execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=1,
            max_parallel=cap,
            selection=AgentChoice(model="test-model", effort="low"),
        ),
    )
    execution.queue("harbor", QueueEdit(expected_revision=configured.revision, enabled=True))
    return execution


def result(execution: Execution, *, process=True, partial=False):
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    execution.started("harbor", run.id)
    finished = execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(
            summary="Implemented",
            checks="unittest passed",
            outcome="partial" if partial else "complete",
            remaining_work="Add a check" if partial else "",
        ),
        commit=RESULT,
    )
    if process:
        validate_report(execution)
    return finished


def validate_report(execution):
    # Transaction/ownership tests use synthetic commits; real tree checks are in test_results.
    with patch("flowfield.results.git", return_value=b"same-tree"):
        Results(execution.workspace).process("harbor")
