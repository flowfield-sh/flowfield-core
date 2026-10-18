"""Drive deterministic report fixtures through the real execution and delivery owners."""

from unittest.mock import patch

from flowfield.agent_models import AgentChoice
from flowfield.application import TaskEdit, TaskPriority, TaskPublish, TaskReopen
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit, WorkerResult
from flowfield.results import Results
from flowfield.reviewer_settings import ReviewerEdit, Reviewers


def run_report(
    store, owner, identity, *, expected_revision, status, completion=None, author="fixture"
):
    task = store.task(owner, identity)
    store._current(task.revision, expected_revision)
    if task.status == "done" and status != "done":
        task = store.reopen_task(
            owner,
            identity,
            TaskReopen(
                expected_revision=task.revision, note="Verify revised findings", author=author
            ),
        )
        task = store.publish_task(
            owner,
            identity,
            TaskPublish(expected_revision=task.revision, completion="report", author=author),
        )
    if status in ("backlog", "up_next"):
        return store.prioritize_task(
            owner,
            identity,
            TaskPriority(expected_revision=task.revision, status=status, author=author),
        )
    if task.blocked_by:
        raise ApplicationError(
            "blocked", "Blocked by: " + ", ".join(item.title for item in task.blocked_by)
        )
    if task.reconciliation_reason:
        raise ApplicationError("reconcile_task", "Please reconcile the task before execution.")
    if task.publication and task.publication_status != "published":
        raise ApplicationError(
            "publication_required", "Republish the changed assignment before execution."
        )
    if task.publication and task.publication.completion == "code":
        raise ApplicationError(
            "code_review_required", "Code requires exact result approval and delivery."
        )
    from pathlib import Path

    from flowfield.adapters.git_workspace import git

    baseline = ""
    if task.project_id:
        repository = Path(store.project(task.project_id).path)
        if not (repository / ".git").exists():
            git(repository, "init", "-b", "main")
            git(
                repository,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "Report fixture",
            )
        baseline = git(repository, "rev-parse", "HEAD").decode().strip()
    execution = Execution(store)
    active = [
        run for run in execution.active() if run.workspace_id == owner and run.task_id == task.id
    ]
    if active:
        run = active[0]
    else:
        if not task.body:
            task = store.edit_task(
                owner,
                identity,
                TaskEdit(
                    expected_revision=task.revision, body="Produce and verify the requested report."
                ),
            )
        if not task.publication:
            task = store.publish_task(
                owner, identity, TaskPublish(expected_revision=task.revision, completion="report")
            )
        if task.status != "up_next":
            task = store.prioritize_task(
                owner,
                identity,
                TaskPriority(expected_revision=task.revision, status="up_next", author=author),
            )
        settings = execution.settings(owner)
        settings = execution.configure(
            owner,
            SettingsEdit(
                expected_revision=settings.revision,
                selection=AgentChoice(model="fixture"),
                max_parallel=8,
            ),
        )
        execution.queue(owner, QueueEdit(expected_revision=settings.revision, enabled=False))
        with patch.object(
            execution,
            "_settings",
            return_value=execution.settings(owner).model_copy(update={"enabled": True}),
        ):
            run = execution.claim(owner, baseline, {baseline: set()}, task_id=identity)
        if run is None:
            raise ApplicationError(
                "not_ready", "Task cannot start with its current prerequisites or preparation."
            )
        execution.started(owner, run.id)
    if status == "done":
        execution.finish(
            owner,
            run.id,
            "in_review",
            result=WorkerResult(summary="Report complete", checks="Fixture evidence"),
            commit=baseline or None,
        )
        reviewers = Reviewers(store)
        reviewer = reviewers.get(owner, entity=identity)
        reviewers.configure(
            owner, ReviewerEdit(expected_revision=reviewer.revision, enabled=False), entity=identity
        )
        Results(store).process(owner)
    return store.task(owner, identity)
