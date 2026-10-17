"""Reusable isolated setup and scripted peers; no collected test cases."""

from project_fixtures import fixture_stage_change

from flowfield.application import ProjectEdit, TaskPublish


def prepare(store, task_id="task-0"):
    task = store.task("harbor", task_id)
    return store.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            expected_revision=task.revision,
            completion="report",
            stages=fixture_stage_change(store, "harbor", task.id),
        ),
    )


def defaults(store, references):
    project = store.project("harbor")
    return store.edit_project(
        "harbor",
        ProjectEdit(
            expected_revision=project.revision,
            worker_documents=references,
        ),
    )
