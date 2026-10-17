"""Reusable isolated setup and scripted peers; no collected test cases."""

import os

from execution_fixtures import fixture
from milestone_delivery_git_fixtures import commit
from project_fixtures import task_request

from flowfield.adapters.git_workspace import git
from flowfield.adapters.local_execution import LocalHost
from flowfield.application import MilestoneCreate, TaskPublish
from flowfield.delivery_models import DeliveryConfigure
from flowfield.execution_models import WorkerResult
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig
from flowfield.milestone_delivery import MilestoneDeliveries
from flowfield.milestones import Milestones
from flowfield.result_models import ResultReview
from flowfield.results import Results


def setup(tmp_path):
    execution = fixture(tmp_path, count=0)
    store = execution.workspace
    repo = tmp_path / "harbor"
    git(repo, "init", "-b", "main")
    (repo / ".git/info/exclude").write_text(".flowfield/\n")
    base = commit(repo, "README.md", "Existing project\n")
    integrations = Integrations(store)
    integrations.configure(
        "harbor", IntegrationConfig(expected_revision=1, target_branch="main", checks=["true"])
    )
    milestone = Milestones(store).create(
        "harbor", MilestoneCreate(title="Feature", acceptance_criteria="Both changes work.")
    )
    deliveries = MilestoneDeliveries(store)
    delivery = deliveries.configure(
        "harbor", milestone.id, DeliveryConfigure(expected_revision=1, project_id="harbor")
    )
    assert delivery.status == "ready", delivery.problem
    return execution, integrations, repo, base, milestone, delivery


def contribute(execution, integrations, repo, milestone, name, dependencies=()):
    store = execution.workspace
    task = store.create_task(
        "harbor",
        task_request(
            title=name,
            body="Implement " + name,
            milestone_id=milestone.id,
            status="up_next",
            dependencies=list(dependencies),
        ),
    )
    task = store.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            expected_revision=task.revision,
            completion="code",
        ),
    )
    head = integrations.head("harbor", task.id)
    available = integrations.available("harbor", head, task.publication.target_branch)
    run = execution.claim("harbor", head, {head: available}, task_id=task.id)
    assert run
    execution.started("harbor", run.id)
    env = LocalHost(os.environ).prepare(store.directory, repo, run.id, head)
    (env.checkout / name).write_text(name)
    result, _ = env.snapshot(head)
    execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(summary=name, checks="true"),
        commit=result,
    )
    results = Results(store)
    results.process("harbor")
    version = results.page("harbor", task.id).items[0]
    assert version.status == "ready", version.problem
    results.review(
        "harbor",
        version.id,
        ResultReview(
            expected_revision=version.revision,
            candidate_commit=version.candidate_commit,
            action="approve",
        ),
    )
    results.process("harbor")
    version = results.page("harbor", task.id).items[0]
    assert version.status == "delivered", version.problem
    return store.task("harbor", task.id), run, version
