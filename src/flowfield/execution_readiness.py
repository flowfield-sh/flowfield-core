"""Current role requirements shared by scheduling and transactional reservation."""

import sqlite3
from dataclasses import dataclass

from flowfield.agent_models import AgentChoice
from flowfield.agent_settings import AgentSettings
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessRegistration
from flowfield.harness_settings import HarnessSettings
from flowfield.reviewer_settings import Reviewers


@dataclass(frozen=True)
class TaskAgent:
    choice: AgentChoice
    registration: HarnessRegistration


def task_agents(
    store: Workspace, db: sqlite3.Connection, workspace: str, task: str
) -> dict[str, TaskAgent]:
    agents = AgentSettings(store)
    coordinator = agents.resolve(db, workspace, "coordinator").effective
    worker = agents.resolve(db, workspace, "worker", task).effective
    reviewer = Reviewers(store).resolve(db, workspace, task).effective
    choices = {"Coordinator": coordinator, "Worker": worker}
    if reviewer.enabled:
        choices["Reviewer"] = reviewer.agent
    result = {}
    for role, agent in choices.items():
        if agent is None:
            raise ApplicationError(
                "agent_required", f"Choose {role.lower()} settings before starting this task.", 409
            )
        result[role] = TaskAgent(agent.choice, HarnessSettings.read(db, agent.choice.harness))
    return result
