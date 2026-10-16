"""Real service/Git destination isolation; no live models or personal repositories."""

import os
from pathlib import Path

import pytest
from execution_fixtures import fixture
from project_fixtures import fixture_stage_change, task_request
from test_milestone_delivery_git import commit

from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_execution import LocalHost
from flowfield.application import MilestoneCreate, TaskPublish
from flowfield.delivery_models import DeliveryConfigure
from flowfield.errors import ApplicationError
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


def test_owned_task_question_uses_its_milestone_destination(tmp_path):
    from flowfield.questions import QuestionAnswer, QuestionCreate, Questions

    execution, integrations, _, base, milestone, delivery = setup(tmp_path)
    store = execution.workspace
    task = store.create_task(
        "harbor",
        task_request(
            title="Ask for copy",
            body="Ask for the success text, then implement it.",
            milestone_id=milestone.id,
            status="up_next",
        ),
    )
    store.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            expected_revision=task.revision,
            completion="code",
            stages=fixture_stage_change(store, "harbor", task.id),
        ),
    )
    run = execution.claim("harbor", base, {base: set()}, task_id=task.id)
    assert run and run.target_branch == delivery.branch
    execution.started("harbor", run.id)
    question = execution.ask_question(
        "harbor",
        run.id,
        QuestionCreate(
            task_id=task.id,
            question="Which text?",
            context="Choose copy.",
            recommendation="Brief text.",
        ),
    )
    questions = Questions(store)
    assert questions.get("harbor", question.id).delivery.state == "Needs your answer"
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=base)
    questions.answer("harbor", question.id, QuestionAnswer(expected_revision=1, answer="Saved."))
    assert questions.get("harbor", question.id).delivery.state == "Resuming"
    continued = execution.claim("harbor", base, {base: set()}, task_id=task.id)
    assert continued and continued.target_branch == delivery.branch
    assert integrations.settings("harbor").target_branch == "main"


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
            stages=fixture_stage_change(store, "harbor", task.id),
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


def test_dependent_tasks_integrate_into_owned_branch_without_touching_main(tmp_path):
    execution, integrations, repo, base, milestone, delivery = setup(tmp_path)
    (repo / "README.md").write_text("Human unfinished work\n")
    first, _, result = contribute(execution, integrations, repo, milestone, "first.txt")
    integrations.refresh_availability("harbor")
    second, run, _ = contribute(execution, integrations, repo, milestone, "second.txt", [first.id])
    assert run.base_commit == result.candidate_commit
    assert first.status == second.status == "done"
    assert baseline(repo) == base
    assert (repo / "README.md").read_text() == "Human unfinished work\n"
    assert (Path(delivery.path) / "first.txt").read_text() == "first.txt"
    assert (Path(delivery.path) / "second.txt").read_text() == "second.txt"
    assert integrations.settings("harbor").target_branch == "main"


def test_existing_branch_collision_never_adopts_it(tmp_path):
    execution, _, repo, base, _, delivery = setup(tmp_path)
    milestone = Milestones(execution.workspace).create("harbor", MilestoneCreate(title="Other"))
    result = MilestoneDeliveries(execution.workspace).configure(
        "harbor",
        milestone.id,
        DeliveryConfigure(expected_revision=1, project_id="harbor", branch="main"),
    )
    assert result.status == "blocked"
    assert baseline(repo) == base and not Path(result.path).exists()
    assert Path(delivery.path).exists()


def test_creation_recovers_after_checkout_before_sqlite_receipt(tmp_path, monkeypatch):
    execution, _, _, _, _, _ = setup(tmp_path)
    store = execution.workspace
    milestone = Milestones(store).create("harbor", MilestoneCreate(title="Interrupted"))
    owner = MilestoneDeliveries(store)
    original = owner._save

    def crash(value):
        raise RuntimeError("simulated exit before creation receipt")

    monkeypatch.setattr(owner, "_save", crash)
    with pytest.raises(RuntimeError, match="simulated exit"):
        owner.configure(
            "harbor", milestone.id, DeliveryConfigure(expected_revision=1, project_id="harbor")
        )
    intent = owner.get("harbor", milestone.id)
    assert intent.status == "creating" and Path(intent.path).exists()
    monkeypatch.setattr(owner, "_save", original)
    owner.restart()
    recovered = owner.get("harbor", milestone.id)
    assert recovered.status == "ready"
    assert recovered.id == intent.id and recovered.path == intent.path


def test_project_default_cannot_be_changed_to_owned_milestone_branch(tmp_path):
    _, integrations, _, _, _, delivery = setup(tmp_path)
    with pytest.raises(ApplicationError, match="separate"):
        integrations.configure(
            "harbor",
            IntegrationConfig(
                expected_revision=integrations.settings("harbor").revision,
                target_branch=delivery.branch,
                checks=["true"],
            ),
        )
    assert integrations.settings("harbor").target_branch == "main"


def test_other_milestone_cannot_claim_unavailable_prerequisite(tmp_path):
    execution, integrations, repo, _, milestone, _ = setup(tmp_path)
    first, _, version = contribute(execution, integrations, repo, milestone, "first.txt")
    other = Milestones(execution.workspace).create("harbor", MilestoneCreate(title="Other"))
    destination = MilestoneDeliveries(execution.workspace).configure(
        "harbor", other.id, DeliveryConfigure(expected_revision=1, project_id="harbor")
    )
    store = execution.workspace
    task = store.create_task(
        "harbor",
        task_request(
            title="Needs first",
            body="Use first",
            milestone_id=other.id,
            status="up_next",
            dependencies=[first.id],
        ),
    )
    task = store.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            expected_revision=task.revision,
            completion="code",
            stages=fixture_stage_change(store, "harbor", task.id),
        ),
    )
    head = integrations.head("harbor", task.id)
    assert version.source_commit not in integrations.available("harbor", head, destination.branch)
    with pytest.raises(ApplicationError, match="prerequisite code"):
        execution.claim("harbor", head, {head: set()}, task_id=task.id)


@pytest.mark.parametrize("version", [70, 71])
def test_delivery_migrations_preserve_existing_work_and_rollback(tmp_path, monkeypatch, version):
    from storage_fixtures import logical_data

    from flowfield import migrations
    from flowfield.application import Workspace
    from flowfield.migrations import Migration

    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < version)
        )
        store = fixture(tmp_path, count=0).workspace
        milestone = Milestones(store).create("harbor", MilestoneCreate(title="Existing outcome"))
    before = logical_data(store.directory)
    step = next(m for m in migrations.MIGRATIONS if m.version == version)

    def fail(db):
        step.apply(db)
        raise ValueError("Interrupted delivery upgrade")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(
                Migration(version, fail) if m.version == version else m
                for m in migrations.MIGRATIONS
            ),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(store.directory)
    assert logical_data(store.directory) == before
    upgraded = Workspace(store.directory)
    assert Milestones(upgraded).get("harbor", milestone.id) == milestone
    assert MilestoneDeliveries(upgraded).get("harbor", milestone.id) is None
    with upgraded.connection() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT count(*) FROM milestone_promotions").fetchone()[0] == 0


def test_unowned_collision_can_choose_another_name_without_deleting_branch(tmp_path):
    execution, _, repo, base, _, _ = setup(tmp_path)
    owner = MilestoneDeliveries(execution.workspace)
    milestone = Milestones(execution.workspace).create("harbor", MilestoneCreate(title="Collision"))
    blocked = owner.configure(
        "harbor",
        milestone.id,
        DeliveryConfigure(expected_revision=1, project_id="harbor", branch="main"),
    )
    assert blocked.status == "blocked"
    ready = owner.configure(
        "harbor",
        milestone.id,
        DeliveryConfigure(expected_revision=1, project_id="harbor", branch="flowfield/another"),
    )
    assert ready.status == "ready" and ready.id == blocked.id
    assert baseline(repo) == base
