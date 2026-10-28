"""Reusable isolated setup and scripted peers; no collected test cases."""

from flowfield.application import now
from flowfield.attachments import Attachments
from flowfield.conversation import Conversation
from flowfield.reply_models import Reply, ReplyBinding, ReplyCreate


def message(workspace, task="task-0", action="changes", body="Why this approach?"):
    gate = Conversation(workspace).eligibility("harbor", task)
    return ReplyCreate(
        binding=ReplyBinding.model_validate(
            gate.model_dump(include=set(ReplyBinding.model_fields))
        ),
        action=action,
        body=body,
    )


def historical_note(workspace, task="task-0", body="Saved testing note", *, owner="harbor"):
    """Persist a pre-removal note to exercise historical state compatibility."""
    gate = Conversation(workspace).eligibility(owner, task)
    note = Reply(
        workspace_id=owner,
        task_id=task,
        action="observation",
        body=body,
        binding=ReplyBinding.model_validate(
            gate.model_dump(include=set(ReplyBinding.model_fields))
        ),
        created_at=now(),
        status="recorded",
    )
    with workspace.connection(write=True) as db:
        Attachments(workspace).references(db, owner, task, body, bind=True)
        db.execute(
            "INSERT INTO task_replies VALUES (?,?,?,?,?)",
            (owner, task, note.id, note.created_at, note.model_dump_json()),
        )
    return note
