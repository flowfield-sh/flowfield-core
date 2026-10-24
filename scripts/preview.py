"""Run this checkout with a persistent, populated, isolated example; never start models."""

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import uvicorn

from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.api import create_app
from flowfield.application import TaskCreate, TaskPreparation, Workspace
from flowfield.coordinator_settings import CoordinatorSettings
from flowfield.documents import DocumentCreate, Documents
from flowfield.execution import Execution
from flowfield.execution_models import SettingsEdit
from flowfield.questions import QuestionCreate, Questions
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.workspaces import Workspaces


def seed(root: Path, agents: dict) -> dict:
    """Use the existing real-Git result fixture, then populate planning and questions."""
    output = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("create-integration-demo.py")), str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    info = json.loads(output.stdout.strip().splitlines()[-1])
    store = Workspace(Path(info["state"]))
    space = Workspaces(store).for_project(info["project"])
    # The integration fixture intentionally dirties its disposable repository. A
    # general UI preview starts clean so the sample result can actually be delivered.
    subprocess.run(["git", "-C", info["repository"], "restore", "README.md"], check=True)
    execution = Execution(store)
    settings = execution.settings(space.id)
    execution.configure(
        space.id,
        SettingsEdit(
            expected_revision=settings.revision,
            selection=AgentChoice.model_validate(agents["worker"]),
            auto_start_queue=False,
        ),
    )
    coordinator = CoordinatorSettings(store)
    saved = coordinator.get(space.id)
    coordinator.edit(
        space.id,
        AgentSettingsEdit(
            expected_revision=saved.revision,
            selection=AgentChoice.model_validate(agents["coordinator"]),
        ),
    )
    reviewers = Reviewers(store)
    saved_review = reviewers.get(space.id)
    reviewers.configure(
        space.id,
        ReviewerEdit(
            expected_revision=saved_review.revision,
            enabled=False,
            selection=AgentChoice.model_validate(agents["reviewer"]),
        ),
    )
    Documents(store).create(
        space.id,
        DocumentCreate(
            title="Harbor release guidelines",
            body=(
                "# Release planning\n\nKeep the release overview readable on small screens. "
                "Explain blockers with a next action. Validate delivery before "
                "announcing availability.\n"
            ),
        ),
    )
    for title, body, status in [
        (
            "Make the release filters easier to scan",
            "Group filters by audience and preserve selections after navigation.",
            "backlog",
        ),
        (
            "Improve empty search results",
            "Explain why no release matches and provide a clear reset action.",
            "backlog",
        ),
        (
            "Show release owners in the overview",
            "Add an owner label to each release without crowding its title.",
            "up_next",
        ),
        (
            "Keep the mobile header compact",
            "Keep the title readable and actions reachable on a narrow display.",
            "up_next",
        ),
    ]:
        store.create_task(
            space.id,
            TaskCreate(
                title=title,
                body=body,
                status=status,
                project_id=info["project"],
                preparation=TaskPreparation(completion="code"),
            ),
        )
    question = store.create_task(
        space.id,
        TaskCreate(
            title="Choose the pilot audience",
            body="Agree who should receive the first release announcement.",
            preparation=TaskPreparation(completion="report"),
        ),
    )
    Questions(store).ask(
        space.id,
        QuestionCreate(
            task_id=question.id,
            question="Who should receive the first release announcement?",
            context="The release overview is ready for a small pilot.",
            recommendation="Start with the internal team.",
        ),
    )
    info["workspace"] = space.id
    (root / "preview.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        required=True,
        help="New preview directory, or this command's previous preview.",
    )
    parser.add_argument("--port", type=int, default=0, help="0 reserves an available local port.")
    parser.add_argument(
        "--agents",
        type=Path,
        help="Explicit coordinator/worker/reviewer choices; otherwise offline placeholders.",
    )
    args = parser.parse_args()
    root = args.directory.expanduser().resolve()
    receipt = root / "preview.json"
    agents = (
        json.loads(args.agents.read_text())
        if args.agents
        else {
            role: {"harness": "codex", "model": "offline-preview"}
            for role in ("coordinator", "worker", "reviewer")
        }
    )
    if receipt.exists():
        info = json.loads(receipt.read_text())
        if Path(info["state"]).resolve() != root / "state":
            parser.error("Preview receipt does not belong to this directory.")
    elif root.exists():
        parser.error(
            "Existing directory is not a preview. Choose a new directory; nothing was changed."
        )
    else:
        info = seed(root, agents)
    os.environ["FLOWFIELD_UPDATE_CHECKS"] = "0"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", args.port))
        sock.listen(128)
        port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}/workspaces/{info['workspace']}/tasks"
        print(
            f"Preview: {url}\nState: {info['state']}\n"
            "Press Ctrl-C to stop. Preview edits are retained.",
            flush=True,
        )
        server = uvicorn.Server(
            uvicorn.Config(create_app(data_dir=Path(info["state"])), host="127.0.0.1", port=port)
        )
        server.run(sockets=[sock])


if __name__ == "__main__":
    main()
