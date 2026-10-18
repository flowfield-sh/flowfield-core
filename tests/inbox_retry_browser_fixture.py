"""Seed an actionable failed worker without invoking a harness."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from project_fixtures import adopt, task_request

from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, TaskPublish, Workspace
from flowfield.execution import Execution
from flowfield.execution_models import SettingsEdit

state = Path(sys.argv[1])
workspace = Workspace(state)
project = adopt(workspace, ProjectSetup(path=str(state / "inbox-retry")))
task = workspace.create_task(
    project.id,
    task_request(
        title="Recover the import", body="Report verified import behavior.", status="up_next"
    ),
)
workspace.publish_task(
    project.id, task.id, TaskPublish(expected_revision=task.revision, completion="report")
)
execution = Execution(workspace)
settings = execution.configure(
    project.id,
    SettingsEdit(expected_revision=1, selection=AgentChoice(model="offline-fixture")),
)
with patch.object(
    execution, "_settings", return_value=settings.model_copy(update={"enabled": True})
):
    run = execution.claim(project.id, "a" * 40, {})
assert run
execution.finish(project.id, run.id, "failed", problem="A fixture failure needs retry.")
print(json.dumps({"project": project.id, "run": run.id}))
