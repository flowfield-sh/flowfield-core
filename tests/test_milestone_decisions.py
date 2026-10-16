"""Decision validation is pure; delivery/scope/recovery retain separate real-Git journeys."""

import pytest

from flowfield.errors import ApplicationError
from flowfield.verification_models import DeliveredTask, MilestoneVerification


@pytest.mark.parametrize(
    "invalid", [None, "missing", "duplicate", "other_task", "other_check", "blank", "intent"]
)
def test_passing_summary_requires_exact_dependency_assessments(invalid):
    from flowfield.milestone_reviews import MilestoneVerdict, validate_decision
    from flowfield.publication import DEPENDENCY_CHANGED

    value = MilestoneVerification(
        id="verification",
        workspace_id="workspace",
        milestone_id="milestone",
        milestone_key="M-1",
        milestone_revision=1,
        title="Combined outcome",
        criteria="Feature works",
        checks=["Feature"],
        project_id="harbor",
        created_at="2026-10-16T00:00:00Z",
        updated_at="2026-10-16T00:00:00Z",
        author="coordinator",
        tasks=[
            DeliveredTask(
                project_id="harbor",
                task_id="task",
                task_key="HRB-1",
                revision=1,
                agreement_revision=1,
                result_id="result",
                result_revision=1,
                settings_revision=1,
                commit="a" * 40,
                target_branch="integration",
            )
        ],
    )
    task = value.tasks[0]
    task.reconciliation_reason = DEPENDENCY_CHANGED
    assessment = {
        "task_id": task.task_id,
        "check": 1,
        "evidence": "The exact changed prerequisite leaves this contribution correct.",
    }
    assessments = [assessment]
    if invalid == "missing":
        assessments = []
    elif invalid == "duplicate":
        assessments.append(dict(assessment))
    elif invalid == "other_task":
        assessment["task_id"] = "outside-the-assignment"
    elif invalid == "other_check":
        assessment["check"] = 2
    elif invalid == "blank":
        assessment["evidence"] = " "
    elif invalid == "intent":
        task.reconciliation_reason = (
            "Requirements changed. Review recorded work with the coordinator."
        )
    decision = MilestoneVerdict.model_validate(
        {
            "action": "report",
            "summary": "All good",
            "evidence": "Generic passing claim",
            "dependency_assessments": assessments,
            "report": {
                "verification_id": value.id,
                "coverage": "complete",
                "coverage_notes": "All checked",
                "findings": [{"check": 1, "verdict": "passed", "evidence": "Feature checked"}],
            },
        }
    )
    if invalid is None:
        validate_decision(value, decision)
    else:
        with pytest.raises(ApplicationError, match="Assess each changed prerequisite"):
            validate_decision(value, decision)
