"""Reusable isolated setup and scripted peers; no collected test cases."""

from flowfield.application import ProjectEdit, TaskReconcile


def prepare(store, task_id="task-0"):
    task = store.task("harbor", task_id)
    from flowfield.guidance_reconciliation import pending

    with store.connection() as db:
        if task.completion == "report" and not pending(store, db, task):
            return task
    return store.reconcile_task(
        "harbor",
        task.id,
        TaskReconcile(
            expected_revision=task.revision,
            completion="report",
            note="Use the selected guidance for this task.",
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
