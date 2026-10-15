import { BlockedBy } from "./TaskLinks";
import { WorkspaceLink } from "./WorkspaceLink";
import { taskHref } from "./navigation";
import { QuestionLink } from "./QuestionLink";
import type { TaskCard, TaskReference } from "./workspace";
import { WorkState } from "./WorkState";

function combinedDependencies(task: TaskCard) {
  return task.state?.reason === "prerequisites" && task.blocked_by.length > 0;
}

export function TaskState({
  task,
  projectId,
  open,
  linked = false,
}: {
  task: TaskCard;
  projectId: string;
  open: (id: string) => void;
  linked?: boolean;
}) {
  return (
    <>
      {task.state?.tone !== "idle" && (
        <WorkState
          state={
            task.state && { ...task.state, href: taskHref(projectId, task) }
          }
          linked={linked}
        >
          {combinedDependencies(task) ? (
            <BlockedBy
              prefix="Waiting on"
              projectId={projectId}
              tasks={task.blocked_by}
              open={open}
            />
          ) : undefined}
        </WorkState>
      )}
      {(!!task.document_updates?.length || !!task.milestone_update) && (
        <p className="detail-metadata">
          {task.milestone_update
            ? "Milestone details changed"
            : "Newer source revision available"}
        </p>
      )}
    </>
  );
}

export function hasTaskNeeds(
  task: TaskCard,
  pendingCode: TaskReference[] = [],
  showQuestions = true,
  includeDependencies = false,
) {
  return !!(
    (task.preparation_issue &&
      (!task.blocking_questions.length ||
        task.readiness === "needs_reconciliation")) ||
    ((includeDependencies || !combinedDependencies(task)) &&
      task.blocked_by.length) ||
    task.blocking_questions.some((q) => showQuestions || !q.task_id) ||
    pendingCode.length
  );
}

export function TaskNeeds({
  task,
  projectId,
  pendingCode = [],
  open,
  detailed = false,
  showQuestions = true,
  includeDependencies = false,
}: {
  task: TaskCard;
  projectId: string;
  pendingCode?: TaskReference[];
  open: (id: string) => void;
  detailed?: boolean;
  showQuestions?: boolean;
  includeDependencies?: boolean;
}) {
  return (
    <ul className="task-needs">
      {task.preparation_issue &&
        (!task.blocking_questions.length ||
          task.readiness === "needs_reconciliation") && (
          <li>{task.preparation_issue}</li>
        )}
      {(includeDependencies || !combinedDependencies(task)) &&
        !!task.blocked_by.length && (
          <li>
            <BlockedBy
              projectId={projectId}
              tasks={task.blocked_by}
              open={open}
            />
          </li>
        )}
      {task.blocking_questions
        .filter((q) => showQuestions || !q.task_id)
        .map((q) => (
          <li key={q.id}>
            {detailed && (
              <p>
                {q.delivery
                  ? q.delivery.message
                  : q.status === "answered"
                    ? "Ask the coordinator to apply your answer:"
                    : "Needs your answer before work can continue:"}
              </p>
            )}
            {q.task_id ? (
              <WorkspaceLink
                to={`${taskHref(projectId, task)}/conversation/${encodeURIComponent(`question:${q.id}:${q.revision}`)}`}
                className={`input-request${q.status === "open" ? " needs-answer" : ""}`}
              >
                {detailed
                  ? q.question
                  : q.delivery
                    ? q.delivery.state
                    : q.status === "answered"
                      ? "Ask coordinator to apply answer"
                      : "Needs your answer"}
              </WorkspaceLink>
            ) : (
              <QuestionLink id={q.id}>{q.question}</QuestionLink>
            )}
          </li>
        ))}
      {!!pendingCode.length && (
        <li>
          <BlockedBy
            prefix="Needs code from"
            projectId={projectId}
            tasks={pendingCode}
            open={open}
          />
        </li>
      )}
    </ul>
  );
}
