"""HTTP transport for task questions; application rules live in Questions."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from flowfield.application import Workspace
from flowfield.questions import (
    AnswerRetract,
    Question,
    QuestionAnswer,
    QuestionApply,
    Questions,
    QuestionWithdraw,
)


def question_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/questions")

    @router.get("")
    def questions(
        workspace_id: str,
        status: str = "active",
        task_id: str | None = None,
        after: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return Questions(workspace()).list(
            workspace_id, status=status, task_id=task_id, after=after, limit=limit
        )

    @router.get("/{identity}")
    def question(workspace_id: str, identity: str, revision: int | None = None) -> Question:
        return Questions(workspace()).get(workspace_id, identity, revision)

    @router.get("/{identity}/history")
    def history(
        workspace_id: str, identity: str, before: int | None = None, limit: int = 10
    ) -> dict[str, Any]:
        return Questions(workspace()).history(workspace_id, identity, before=before, limit=limit)

    @router.post("/{identity}/answer")
    def answer(workspace_id: str, identity: str, request: QuestionAnswer) -> Question:
        return Questions(workspace()).answer(workspace_id, identity, request)

    @router.post("/{identity}/apply")
    def apply(workspace_id: str, identity: str, request: QuestionApply) -> Question:
        return Questions(workspace()).apply(workspace_id, identity, request)

    @router.post("/{identity}/correction")
    def correct(workspace_id: str, identity: str, request: QuestionAnswer) -> Question:
        return Questions(workspace()).correct(workspace_id, identity, request)

    @router.post("/{identity}/retract-answer")
    def retract_answer(workspace_id: str, identity: str, request: AnswerRetract) -> Question:
        return Questions(workspace()).retract_answer(workspace_id, identity, request)

    @router.post("/{identity}/withdraw")
    def withdraw(workspace_id: str, identity: str, request: QuestionWithdraw) -> Question:
        return Questions(workspace()).withdraw(workspace_id, identity, request)

    return router
