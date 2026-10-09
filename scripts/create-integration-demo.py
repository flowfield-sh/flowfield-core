"""Create a fresh Harbor integration trial with real Git/checks and offline worker records."""

import argparse
import json
import os
from pathlib import Path

from flowfield.adapters.git_integration import run_checks
from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import MilestoneCreate, ProjectSetup, TaskCreate, TaskPublish, Workspace
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit, WorkerResult
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig
from flowfield.results import Results
from flowfield.stage_models import StageUpdate
from flowfield.stages import Stages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--state", type=Path, help="Use an existing service's disposable state.")
    parser.add_argument(
        "--conflict", action="store_true", help="Seed a blocked result for recovery."
    )
    args = parser.parse_args()
    root = args.directory.expanduser().resolve()
    if root.exists():
        parser.error("Choose a new directory. Existing state is never reset or migrated.")
    root.mkdir(parents=True)
    workspace = Workspace(args.state or root / "state")
    repo = root / "harbor-integration"
    repo.mkdir()
    project = workspace.setup_project(
        ProjectSetup(path=str(repo), name="Harbor · Integration trial", task_prefix="INT"),
    )
    git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("# Harbor\n\nOffline integration fixture; no model calls.\n")
    with (repo / ".gitignore").open("a") as out:
        out.write("\n__pycache__/\n")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Harbor baseline",
    )
    head = baseline(repo)
    integration = Integrations(workspace)
    integration.configure(
        project.id,
        IntegrationConfig(
            runtime="local",
            expected_revision=1,
            target_branch="integration",
            create_from="main",
            checks=["python -m unittest -v"],
        ),
    )
    git(repo, "switch", "integration")
    for identity, title in [("catalog", "Catalog import"), ("reporting", "Reports")]:
        workspace.create_milestone(project.id, MilestoneCreate(id=identity, title=title))
    for identity, title, milestone, dependencies in [
        ("loader", "Load a local JSON catalog", "catalog", []),
        ("report", "Summarize the loaded catalog", "reporting", ["loader"]),
    ]:
        task = workspace.create_task(
            project.id,
            TaskCreate(
                stages=[{"id": "work", "title": title[:80], "outcome": title}],
                id=identity,
                title=title,
                milestone_id=milestone,
                dependencies=dependencies,
                status="up_next",
                body="Load the local catalog and verify behavior with unittest."
                if identity == "loader"
                else "Use the accepted catalog loader to report the number of books. "
                "Add a unittest for an empty and populated catalog.",
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
    execution = Execution(workspace)
    settings = execution.configure(
        project.id,
        SettingsEdit(
            expected_revision=1, selection=AgentChoice(model="offline-fixture", effort="none")
        ),
    )
    execution.queue(project.id, QueueEdit(expected_revision=settings.revision, enabled=True))
    run = execution.claim(project.id, head, {head: set()})
    assert run and run.task_id == "loader"
    execution.started(project.id, run.id)
    env = LocalHost(os.environ).prepare(workspace.directory, repo, run.id, head)
    execution.save_local(
        run.id,
        {
            "runtime_kind": "local",
            **{
                key: str(getattr(env, key)) for key in ("root", "checkout", "runtime", "common_git")
            },
        },
    )
    (env.checkout / "catalog.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "\n"
        "def load_catalog(path):\n"
        "    rows = json.loads(Path(path).read_text())\n"
        "    if not isinstance(rows, list):\n"
        "        raise ValueError('expected a list')\n"
        "    return rows\n"
        ""
    )
    (env.checkout / "test_catalog.py").write_text(
        "import tempfile\n"
        "import unittest\n"
        "from pathlib import Path\n"
        "from catalog import load_catalog\n"
        "\n"
        "class CatalogTests(unittest.TestCase):\n"
        "    def test_empty(self):\n"
        "        with tempfile.TemporaryDirectory() as directory:\n"
        "            path = Path(directory)/'catalog.json'\n"
        "            path.write_text('[]')\n"
        "            self.assertEqual(load_catalog(path), [])\n"
        "    def test_reject_object(self):\n"
        "        with tempfile.TemporaryDirectory() as directory:\n"
        "            path = Path(directory)/'catalog.json'\n"
        "            path.write_text('{}')\n"
        "            with self.assertRaises(ValueError):\n"
        "                load_catalog(path)\n"
        ""
    )
    reports = run_checks(env, ["python -m unittest -v"])
    assert all(check.exit_code == 0 for check in reports)
    commit, _ = env.snapshot(head)
    progress = Stages(workspace).get(project.id, run.task_id)
    Stages(workspace).update(
        project.id,
        run.task_id,
        StageUpdate(
            expected_revision=progress.revision,
            agreement_revision=progress.agreement_revision,
            stages=[stage.model_copy(update={"status": "completed"}) for stage in progress.stages],
            reason="Demo implementation and checks completed",
        ),
        run_id=run.id,
    )
    run = execution.finish(
        project.id,
        run.id,
        "in_review",
        commit=commit,
        result=WorkerResult(
            summary="Load JSON catalogs and reject non-list data.",
            checks="python -m unittest -v: 2 tests passed.",
            limitations="Offline fixture, not model-generated work.",
        ),
    )
    results = Results(workspace)
    if args.conflict:
        target_env = LocalHost(os.environ).prepare(workspace.directory, repo, "target-input", head)
        (target_env.checkout / "catalog.py").write_text(
            "def load_catalog(path):\n    return {'books': []}\n"
        )
        target_commit, _ = target_env.snapshot(head)
        git(repo, "merge", "--ff-only", target_commit)
    results.process(project.id)
    version = results.page(project.id, run.task_id).items[0]
    candidate = integration.get(project.id, version.integration_id)
    assert candidate.status == ("failed" if args.conflict else "ready"), candidate.problem
    # A dirty human checkout makes preservation easy to verify in this trial.
    (repo / "README.md").write_text("# Harbor\n\nUnsaved human planning notes: preserve me.\n")
    with workspace.connection(write=True, project_id=project.id) as db:
        settings = execution._settings(db, project.id)
        settings.enabled, settings.selection = False, None
        execution._save_settings(db, settings)
    print(
        json.dumps(
            {
                "state": str(workspace.directory),
                "repository": str(repo),
                "project": project.id,
                "run": run.id,
                "integration": candidate.id,
                "result": version.id,
                "result_revision": version.revision,
                "revision": candidate.revision,
                "candidate": candidate.candidate_commit,
            }
        )
    )


if __name__ == "__main__":
    main()
