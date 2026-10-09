"""Offline browser fixture: synthetic ownership/checkpoint, no model or real execution."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from project_fixtures import adopt, task_request

from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, TaskPreparation, Workspace
from flowfield.execution import Execution
from flowfield.execution_models import SettingsEdit
from flowfield.questions import QuestionCreate

workspace = Workspace(Path(sys.argv[1]))
execution = Execution(workspace)
base, checkpoint = "a" * 40, "b" * 40
project = "input-browser"
if sys.argv[2] == "create":
    adopt(workspace, ProjectSetup(path=str(workspace.directory / project), task_prefix="INP"))
    workspace.create_task(
        project,
        task_request(
            id="export",
            title="Export records",
            body="Implement the agreed export.",
            status="up_next",
            preparation=TaskPreparation(completion="report"),
        ),
    )
    execution.configure(
        project,
        SettingsEdit(expected_revision=1, selection=AgentChoice(model="fake", effort="low")),
    )
    settings = execution.settings(project).model_copy(update={"enabled": True})
    with patch.object(execution, "_settings", return_value=settings):
        run = execution.claim(project, base, {base: set()})
    execution.started(project, run.id)
    q = execution.ask_question(
        project,
        run.id,
        QuestionCreate(
            id="which-records",
            task_id=run.task_id,
            question="Which records?",
            context="The export scope is a product choice.",
            recommendation="All records",
            blocking_scope="Export scope",
        ),
    )
    execution.finish(project, run.id, "waiting_for_input", input_checkpoint=checkpoint)
    print(json.dumps({"question_id": q.id, "run_id": run.id}))
else:
    settings = execution.settings(project).model_copy(update={"enabled": True})
    with patch.object(execution, "_settings", return_value=settings):
        run = execution.claim(project, base, {checkpoint: set()})
    assert run
    print(run.model_dump_json())
