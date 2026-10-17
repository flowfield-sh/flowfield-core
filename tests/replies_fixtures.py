"""Reusable isolated setup and scripted peers; no collected test cases."""

from flowfield.conversation import Conversation
from flowfield.reply_models import ReplyBinding, ReplyCreate


def message(workspace, task="task-0", action="observation", body="Why this approach?"):
    gate = Conversation(workspace).eligibility("harbor", task)
    return ReplyCreate(
        binding=ReplyBinding.model_validate(
            gate.model_dump(include=set(ReplyBinding.model_fields))
        ),
        action=action,
        body=body,
    )
