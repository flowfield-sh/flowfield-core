"""Reusable isolated setup and scripted peers; no collected test cases."""

import asyncio
import json

from fake_worker import FakeWorker

from flowfield.adapters.git_workspace import git


class Reviewer(FakeWorker):
    decision = "approve"
    observed = []

    async def run(self, model, effort, prompt, tools):
        brief = json.loads(prompt)
        self.observed.append(brief)
        names = {tool["name"] for tool in tools}
        assert "submit_result" not in names and "run_project_checks" not in names
        assert "review" not in names
        assert git(self.cwd, "rev-parse", "HEAD").decode().strip() == brief["candidate_commit"]
        context = json.loads(await self.on_tool("read_context", {"section": "result"}))
        assert len(context["text"]) <= 6000
        action = self.decision if brief["purpose"] == "reviewer" else "answer"
        await self.on_tool(
            "submit_review" if brief["purpose"] == "reviewer" else "answer_review",
            {
                "action": action,
                "summary": "Independent finding",
                "evidence": "Read the exact candidate and agreement.",
                "request": ""
                if action in ("approve", "answer")
                else "Explain the agreed edge case.",
            },
        )
        return {"status": "completed"}


def install(monkeypatch, decision="approve"):
    Reviewer.decision = decision
    Reviewer.observed = []
    monkeypatch.setattr(
        "flowfield.native_review_execution.create",
        lambda choice, directory, cwd, environment, **kw: Reviewer(
            directory, cwd, environment, **kw
        ),
    )


async def round_trip(service):
    await service.review_execution.schedule()
    await asyncio.gather(*list(service.jobs.values()))
