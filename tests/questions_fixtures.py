"""Reusable isolated setup and scripted peers; no collected test cases."""

from pathlib import Path

from project_fixtures import adopt, task_request

from flowfield.application import ProjectSetup, Workspace
from flowfield.questions import (
    QuestionCreate,
    Questions,
)


def setup(tmp_path: Path):
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(tmp_path / "harbor")))
    workspace.create_task(
        "harbor", task_request(id="serializer", title="Serialize CSV", status="up_next")
    )
    workspace.create_task(
        "harbor",
        task_request(
            id="download",
            title="Download",
            body="Export CSV",
            dependencies=["T-1"],
            status="up_next",
        ),
    )
    return workspace, Questions(workspace)


def ask(questions: Questions, identity: str = "scope", blocking: bool = True):
    return questions.ask(
        "harbor",
        QuestionCreate(
            id=identity,
            task_id="T-2",
            question="Which rows?",
            context="The table is paginated.",
            recommendation="All filtered rows, capped at 10,000.",
            choices=["All filtered rows", "Visible page"],
            blocking_scope="Choosing the export contract" if blocking else None,
        ),
    )
