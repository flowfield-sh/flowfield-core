"""Model-free whole-milestone review fixture using the production delivery lifecycle."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from project_fixtures import adopt, task_request

from flowfield.agent_models import AgentChoice
from flowfield.application import MilestoneCreate, ProjectSetup, TaskPreparation, Workspace
from flowfield.execution import Execution
from flowfield.execution_models import SettingsEdit, WorkerResult
from flowfield.milestone_verification import MilestoneVerifications
from flowfield.milestones import Milestones
from flowfield.results import Results
from flowfield.verification_models import (
    VerificationPrepare,
    VerificationReport,
    VerificationReview,
)


def seed(
    directory: Path,
    project: str,
    verdict: str = "passed",
    title: str = "Verify installed harness discovery",
    rounds: int = 1,
) -> dict[str, str]:
    store = Workspace(directory)
    adopt(store, ProjectSetup(path=str(directory.parent / project), id=project))
    milestone = Milestones(store).create(
        project,
        MilestoneCreate(
            title=title,
            body="A fixture for whole-outcome review; no harness is invoked.",
            acceptance_criteria=(
                "- Find an installed harness.\n- Give useful setup errors when it is unavailable."
            ),
        ),
    )
    task = store.create_task(
        project,
        task_request(
            title="Deliver discovery and setup behavior",
            body="Implement discovery and actionable setup errors.",
            milestone_id=milestone.id,
            status="up_next",
            preparation=TaskPreparation(completion="report"),
        ),
    )
    execution = Execution(store)
    execution.configure(
        project,
        SettingsEdit(
            expected_revision=execution.settings(project).revision,
            selection=AgentChoice(model="fixture", effort="low"),
        ),
    )
    settings = execution.settings(project).model_copy(update={"enabled": True})
    base = "a" * 40

    def deliver(result: WorkerResult):
        with patch.object(execution, "_settings", return_value=settings):
            run = execution.claim(project, base, {base: set()})
        assert run
        execution.started(project, run.id)
        execution.report_result(project, run.id, result)
        execution.finish(project, run.id, "in_review", result=result, commit=base)
        with patch("flowfield.results.git", return_value=b"same-tree"):
            Results(store).process(project)

    deliver(
        WorkerResult(
            summary="Discovery and setup behavior delivered",
            checks="Synthetic fixture evidence; no live harness",
        )
    )
    records = MilestoneVerifications(store)
    for cycle in range(rounds):
        verifier = store.create_task(
            project,
            task_request(
                title="Verify the complete discovery journey",
                body="Evaluate both acceptance criteria and identify omitted requirements.",
                milestone_id=milestone.id,
                dependencies=[task.key],
                status="up_next",
            ),
        )
        verification = MilestoneVerifications(store).prepare(
            project,
            milestone.id,
            VerificationPrepare(
                expected_revision=milestone.revision,
                project_id=project,
                task_id=verifier.id,
                expected_task_revision=verifier.revision,
                checks=[
                    "Discover an installed harness",
                    "Explain missing executable and setup errors",
                ],
            ),
        )
        report = VerificationReport.model_validate(
            {
                "verification_id": verification.id,
                "findings": [
                    {
                        "check": 1,
                        "verdict": "passed",
                        "evidence": "Fixture: discovery returned the configured executable.",
                    },
                    {
                        "check": 2,
                        "verdict": verdict,
                        "evidence": "The error names the executable and explains configuration."
                        if verdict == "passed"
                        else "Fixture: the missing executable error has no recovery instructions.",
                    },
                ],
                "coverage": "complete",
                "coverage_notes": (
                    "Both criteria were evaluated using synthetic fixtures. "
                    "This walkthrough exercises Flowfield review; it is not evidence "
                    "of actual fx integration."
                ),
            }
        )
        deliver(
            WorkerResult(
                summary="Whole-outcome verification completed",
                limitations="Synthetic walkthrough findings; no live harness was invoked.",
                checks="Two synthetic fixture checks",
                verification=report,
            )
        )
        if cycle < rounds - 1:
            current = records.get(project, verification.id)
            records.review(
                project,
                current.id,
                VerificationReview(
                    expected_revision=current.revision,
                    action="request_changes",
                    note=f"Review round {cycle + 1}: include the recovery walkthrough.",
                ),
            )
    return {
        "workspace_id": project,
        "project_id": project,
        "milestone_id": milestone.id,
        "milestone_key": milestone.key,
        "verification_id": verification.id,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            seed(
                Path(sys.argv[1]),
                sys.argv[2],
                sys.argv[3] if len(sys.argv) > 3 else "passed",
                rounds=int(sys.argv[4]) if len(sys.argv) > 4 else 1,
            )
        )
    )
