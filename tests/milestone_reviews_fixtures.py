"""Reusable isolated setup and scripted peers; no collected test cases."""

import asyncio
import json

import pytest
from delegated_integration_fixtures import assess, policy, prepared
from fake_worker import FakeWorker

from flowfield.adapters.git_workspace import git
from flowfield.agent_models import AgentChoice
from flowfield.milestone_promotion import MilestonePromotions
from flowfield.milestone_verification import MilestoneVerifications
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.supervisor import Supervisor
from flowfield.task_reviews import TaskReviews
from flowfield.verification_plans import VerificationPlanEdit, VerificationPlans


def fixture(tmp_path):
    store, repo, base, milestone, delivery, results, result = prepared(tmp_path)
    plans = VerificationPlans(store)
    plans.save(
        "harbor",
        milestone.id,
        VerificationPlanEdit(
            expected_revision=1,
            milestone_revision=1,
            checks=["Combined feature meets the milestone criteria"],
            instructions="Inspect the exact combined files and configured check evidence.",
        ),
    )
    Reviewers(store).configure(
        "harbor",
        "milestone",
        ReviewerEdit(
            expected_revision=1, enabled=True, selection=AgentChoice(model="milestone-fixture")
        ),
    )
    policy(store, milestone, delivery, True)
    review = assess(store, result)
    TaskReviews(store).delegate("harbor", review.id)
    results.process("harbor")
    assert results.get("harbor", result.id).status == "delivered"
    service = Supervisor(store)
    return service, repo, base, milestone, delivery, result


class OutcomeReviewer(FakeWorker):
    action = "pass"
    calls = 0

    async def run(self, model, effort, prompt, tools):
        type(self).calls += 1
        brief = json.loads(prompt)
        assert brief["purpose"] == "milestone_reviewer"
        assert {t["name"] for t in tools} == {"read_context", "submit_milestone_review"}
        assert git(self.cwd, "rev-parse", "HEAD").decode().strip() == brief["candidate_commit"]
        body = json.loads(await self.on_tool("read_context", {"section": "verification"}))
        value = json.loads(body["text"])
        assert value["purpose"] == "review" and value["task_id"] is None
        decision = {
            "action": "report",
            "summary": "Combined outcome assessed",
            "evidence": "Inspected the combined candidate and validation evidence.",
            "report": {
                "verification_id": value["id"],
                "coverage": "complete",
                "coverage_notes": "All agreed criteria assessed.",
                "findings": [
                    {
                        "check": 1,
                        "verdict": "passed" if self.action == "pass" else "failed",
                        "evidence": "Exact feature inspected.",
                    }
                ],
            },
            "dependency_assessments": [
                {
                    "task_id": task["task_id"],
                    "check": 1,
                    "evidence": "Usage still matches the feature under its unchanged agreement.",
                }
                for task in value["tasks"]
                if task.get("reconciliation_reason") and self.action == "pass"
            ],
        }
        if self.action == "question":
            decision = {
                "action": "ask_human",
                "summary": "Clarify the expected boundary",
                "evidence": "The agreed criterion leaves the boundary unclear.",
                "request": "Clarify the existing intent or reconcile the milestone.",
            }
        elif self.action in ("correct", "recover_correction"):
            decision.update(
                correction_task_id=value["tasks"][0]["task_id"],
                correction_check=1,
                request="Correct the feature text to meet its existing agreement.",
            )
            if self.action == "recover_correction":
                incomplete = {key: val for key, val in decision.items() if key != "request"}
                # FakeWorker asserts successful MCP replies; inspect the rejected reply
                # before retrying within this same bound review attempt.
                with pytest.raises(
                    AssertionError, match="State the question or bounded correction"
                ) as error:
                    await self.on_tool("submit_milestone_review", incomplete)
                assert "input_value" not in str(error.value)
                assert "Inspected the combined" not in str(error.value)
        result = await self.on_tool("submit_milestone_review", decision)
        assert "retained" in result.lower(), result
        return {"status": "completed"}


def install(monkeypatch, action="pass"):
    OutcomeReviewer.action, OutcomeReviewer.calls = action, 0
    monkeypatch.setattr(
        "flowfield.native_review_execution.create",
        lambda choice, directory, cwd, environment, **kw: OutcomeReviewer(
            directory, cwd, environment, **kw
        ),
    )


async def step(service):
    await service.milestone_review_execution.schedule()
    await asyncio.gather(*list(service.jobs.values()))
    await asyncio.to_thread(MilestonePromotions(service.workspace).process, "harbor")
    await service.milestone_review_execution.schedule()
    await asyncio.gather(*list(service.jobs.values()))


def latest(service, milestone):
    with service.workspace.connection() as db:
        row = db.execute(
            "SELECT id FROM milestone_verifications WHERE milestone_id=? "
            "ORDER BY number DESC LIMIT 1",
            (milestone.id,),
        ).fetchone()
    return MilestoneVerifications(service.workspace).get("harbor", row[0])
