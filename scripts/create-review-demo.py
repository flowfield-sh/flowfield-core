"""Seed a fresh, offline review workspace. No service or model is launched.

Fixture records simulate worker reports, but Git captures and recorded checks are real.
Run before serving this new state directory; existing directories are never overwritten.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, TaskCreate, TaskPublish, Workspace
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit, Usage, WorkerResult
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig
from flowfield.questions import QuestionCreate, Questions
from flowfield.result_models import ResultReview
from flowfield.results import Results
from flowfield.stage_models import StageUpdate
from flowfield.stages import Stages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="New directory for state and repository")
    args = parser.parse_args()
    root = args.directory.expanduser().resolve()
    if root.exists():
        parser.error("Choose a new directory; existing review state is preserved.")
    root.mkdir(parents=True)
    workspace = Workspace(root / "state")
    repo = root / "harbor-review"
    repo.mkdir()
    project = workspace.setup_project(
        ProjectSetup(path=str(repo), name="Harbor · Review trial", task_prefix="RVW")
    )
    git(repo, "init")
    with (repo / ".gitignore").open("a") as ignored:
        ignored.write("\n__pycache__/\n")
    (repo / "catalog.py").write_text("def load_catalog(path):\n    raise NotImplementedError\n")
    (repo / "README.md").write_text(
        "# Harbor review fixture\n\nA deterministic UI trial; no worker model calls.\n"
    )
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Fixture baseline",
    )
    head = baseline(repo)
    Integrations(workspace).configure(
        project.id,
        IntegrationConfig(
            runtime="local",
            expected_revision=1,
            target_branch="integration",
            create_from="HEAD",
            checks=["python -m unittest -v"],
        ),
    )
    tasks = []
    for identity, title in [
        ("catalog", "Load and validate the catalog"),
        ("csv", "Check CSV export"),
        ("docs", "Choose the documentation scope"),
    ]:
        task = workspace.create_task(
            project.id,
            TaskCreate(
                stages=[{"id": "work", "title": title[:80], "outcome": title}],
                id=identity,
                title=title,
                body="Review fixture: inspect the result and its reported checks.",
                status="up_next",
            ),
        )
        workspace.publish_task(
            project.id,
            task.id,
            TaskPublish(
                completion="code",
                expected_revision=task.revision,
            ),
        )
        tasks.append(task)
    execution = Execution(workspace)
    settings = execution.configure(
        project.id,
        SettingsEdit(
            expected_revision=1, selection=AgentChoice(model="offline-fixture", effort="none")
        ),
    )
    execution.queue(project.id, QueueEdit(expected_revision=settings.revision, enabled=True))

    def capture(revised: bool = False):
        run = execution.claim(project.id, head, {head: set()})
        assert run and run.task_id == "catalog"
        env = LocalHost(os.environ).prepare(root / "state", repo, run.id, run.base_commit)
        execution.save_local(
            run.id,
            {
                "runtime_kind": "local",
                **{
                    key: str(getattr(env, key))
                    for key in ("root", "checkout", "runtime", "common_git")
                },
            },
        )
        execution.started(project.id, run.id)
        prefix = 'f"{path}: expected a list"' if revised else '"expected a list"'
        (env.checkout / "catalog.py").write_text(
            "import json\nfrom pathlib import Path\n\n\ndef load_catalog(path):\n"
            "    rows = json.loads(Path(path).read_text(encoding='utf-8'))\n"
            "    if not isinstance(rows, list):\n"
            f"        raise ValueError({prefix})\n    return rows\n"
        )
        (env.checkout / "test_catalog.py").write_text(
            "import tempfile\nimport unittest\nfrom pathlib import Path\n"
            "from catalog import load_catalog\n\n"
            "class CatalogTests(unittest.TestCase):\n"
            "    def test_empty_catalog(self):\n"
            "        with tempfile.TemporaryDirectory() as directory:\n"
            "            path = Path(directory) / 'books.json'\n"
            "            path.write_text('[]')\n"
            "            self.assertEqual(load_catalog(path), [])\n\n"
            "    def test_reject_object(self):\n"
            "        with tempfile.TemporaryDirectory() as directory:\n"
            "            path = Path(directory) / 'books.json'\n"
            "            path.write_text('{}')\n"
            "            with self.assertRaisesRegex(ValueError, 'expected a list'):\n"
            "                load_catalog(path)\n"
        )
        if not revised:
            (env.checkout / "preview.bin").write_bytes(b"\0fixture image")
            (env.checkout / "large-fixture.txt").write_text("fixture\n" * 40_000)
        subprocess.run([sys.executable, "-m", "unittest", "-v"], cwd=env.checkout, check=True)
        commit, _ = env.snapshot(run.base_commit)
        execution.usage(project.id, run.id, Usage())
        progress = Stages(workspace).get(project.id, run.task_id)
        Stages(workspace).update(
            project.id,
            run.task_id,
            StageUpdate(
                expected_revision=progress.revision,
                agreement_revision=progress.agreement_revision,
                stages=[
                    stage.model_copy(update={"status": "completed"}) for stage in progress.stages
                ],
                reason="Demo implementation and checks completed",
            ),
            run_id=run.id,
        )
        return execution.finish(
            project.id,
            run.id,
            "in_review",
            commit=commit,
            result=WorkerResult(
                summary="Include the input path in invalid-catalog errors."
                if revised
                else "Read UTF-8 JSON catalogs and reject non-list data.",
                checks="`python -m unittest -v`: 2 tests passed in this fixture checkout.",
                limitations=(
                    "Offline fixture, not model-generated work. "
                    "Row-level validation is outside this small trial."
                ),
            ),
        )

    first = capture()
    results = Results(execution.workspace)
    version = results.page(project.id, first.task_id).items[0]
    results.review(
        project.id,
        version.id,
        ResultReview(
            expected_revision=version.revision,
            candidate_commit=first.result_commit,
            action="request_changes",
            note="Include the source path in validation errors.",
            author="fixture-human",
        ),
    )
    current = capture(revised=True)
    Results(workspace).process(project.id)
    failure = execution.claim(project.id, head, {head: set()})
    assert failure and failure.task_id == "csv"
    execution.finish(
        project.id,
        failure.id,
        "failed",
        problem="Simulated fixture failure: the check command exited without a result.",
    )
    Questions(workspace).ask(
        project.id,
        QuestionCreate(
            id="documentation-scope",
            task_id=tasks[2].id,
            question="Which import formats belong in the first guide?",
            context="The first delivery supports a local catalog.",
            recommendation="Document JSON now; CSV can follow.",
            choices=["JSON only", "JSON and CSV"],
            blocking_scope="Documentation scope",
            author="fixture-coordinator",
        ),
    )
    # Fixture-only setup: remove the fake model so the served preview cannot dispatch workers.
    with workspace.connection(write=True, project_id=project.id) as db:
        settings = execution._settings(db, project.id)
        settings.enabled, settings.selection = False, None
        execution._save_settings(db, settings)
    print(
        json.dumps({"state": str(root / "state"), "project": project.id, "review_run": current.id})
    )


if __name__ == "__main__":
    main()
