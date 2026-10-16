"""Atomic answers, exact-result feedback and testing observations."""

from flowfield.application import Workspace, now
from flowfield.attachments import Attachments
from flowfield.conversation import Conversation
from flowfield.errors import ApplicationError
from flowfield.questions import QuestionAnswer, Questions
from flowfield.reply_models import Reply, ReplyBinding, ReplyCreate
from flowfield.result_models import ResultReview
from flowfield.results import Results


class Replies:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def submit(self, project_id: str, task_id: str, request: ReplyCreate) -> Reply:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            task_id = self.workspace._task(db, project_id, task_id).id
            old = db.execute("SELECT data FROM task_replies WHERE id=?", (request.id,)).fetchone()
            if old:
                reply = Reply.model_validate_json(old[0])
                if (
                    reply.project_id != project_id
                    or reply.task_id != task_id
                    or reply.model_dump(include=set(ReplyCreate.model_fields))
                    != request.model_dump()
                ):
                    raise ApplicationError(
                        "reply_conflict", "This message ID is already used.", 409
                    )
                return reply
            gate = Conversation(self.workspace)._eligibility(db, project_id, task_id)
            if not gate.enabled:
                raise ApplicationError(
                    "input_closed", "Work is processing. Your draft is kept; wait until idle.", 409
                )
            binding = ReplyBinding.model_validate(
                gate.model_dump(include=set(ReplyBinding.model_fields))
            )
            if request.binding != binding:
                raise ApplicationError(
                    "reply_stale",
                    "The task, question or result changed. Review it before "
                    "sending your preserved draft.",
                    409,
                )
            if not request.body.strip():
                raise ApplicationError("empty_reply", "Write a reply before sending.")
            if request.milestone:
                from flowfield.milestone_promotion import read as promotion_read
                from flowfield.milestone_verification import read
                from flowfield.worker_settings import workspace_id

                owner = workspace_id(db, project_id)
                if not owner:
                    raise ApplicationError("not_found", "Workspace not found.", 404)
                evidence = read(db, owner, request.milestone.verification_id)
                promotion = (
                    promotion_read(db, evidence.promotion_id) if evidence.promotion_id else None
                )
                self.workspace._current(evidence.revision, request.milestone.verification_revision)
                if (
                    request.action != "observation"
                    or not evidence.current
                    or not promotion
                    or promotion.candidate_commit != request.milestone.candidate_commit
                    or not any(
                        task.project_id == project_id
                        and task.task_id == task_id
                        and task.result_id == binding.result_id
                        and task.result_revision == binding.result_revision
                        for task in evidence.tasks
                    )
                ):
                    raise ApplicationError(
                        "observation_stale",
                        "Read the current milestone candidate before recording testing.",
                        409,
                    )
            Attachments(self.workspace).references(db, project_id, task_id, request.body, bind=True)
            reply = Reply(
                **request.model_dump(), project_id=project_id, task_id=task_id, created_at=now()
            )
            if request.action == "answer":
                if not binding.question_id or not binding.question_revision:
                    raise ApplicationError(
                        "question_required", "There is no current question to answer.", 409
                    )
                Questions(self.workspace)._answer(
                    db,
                    project_id,
                    binding.question_id,
                    QuestionAnswer(
                        expected_revision=binding.question_revision,
                        answer=request.body,
                        author=request.author,
                    ),
                )
                reply.status = "recorded"
            elif binding.question_id:
                raise ApplicationError(
                    "answer_required",
                    "Answer the current question before starting other work.",
                    409,
                )
            elif request.action == "observation":
                if not binding.result_id or not binding.result_revision:
                    raise ApplicationError(
                        "result_required", "Select a result before recording testing.", 409
                    )
                # Exact binding was checked in the same transaction. Recording evidence
                # neither schedules a model nor changes the result, task or approval.
                reply.status = "recorded"
            elif request.action == "changes":
                if not binding.result_id or not binding.result_revision:
                    raise ApplicationError(
                        "result_required",
                        "Select the current result before requesting changes.",
                        409,
                    )
                results = Results(self.workspace)
                version = results._get(db, project_id, binding.result_id)
                results._review(
                    db,
                    project_id,
                    version.id,
                    ResultReview(
                        expected_revision=binding.result_revision,
                        candidate_commit=version.candidate_commit or version.source_commit,
                        action="request_changes",
                        note=request.body,
                        author=request.author,
                    ),
                )
                reply.status = "recorded"
            db.execute(
                "INSERT INTO task_replies VALUES (?,?,?,?,?)",
                (project_id, task_id, reply.id, reply.created_at, reply.model_dump_json()),
            )
            return reply

    def cancel(self, project_id: str, task_id: str, identity: str) -> Reply:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            task_id = self.workspace._task(db, project_id, task_id).id
            row = db.execute(
                "SELECT data FROM task_replies WHERE project_id=? AND task_id=? AND id=?",
                (project_id, task_id, identity),
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "Message not found.", 404)
            reply = Reply.model_validate_json(row[0])
            if reply.status not in ("pending", "cancelled"):
                raise ApplicationError(
                    "reply_assigned",
                    "This reply already belongs to a worker; stop that attempt instead.",
                    409,
                )
            reply.status = "cancelled"
            db.execute(
                "UPDATE task_replies SET data=? WHERE id=?", (reply.model_dump_json(), identity)
            )
            return reply
