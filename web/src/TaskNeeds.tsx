import type { components } from "./api-schema";
import { TaskLink, BlockedBy } from "./TaskLinks";
import { WorkspaceLink } from "./WorkspaceLink";
import { taskHref } from "./navigation";
import { QuestionLink } from "./QuestionLink";
import type { TaskCard } from "./workspace";
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
      {task.state && (
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
      {!!task.document_updates?.length && (
        <p className="detail-metadata">{"Updated guidance available"}</p>
      )}
    </>
  );
}

export function hasTaskNeeds(
  task: TaskCard,
  pendingCode: components["schemas"]["PendingCode"][] = [],
  showQuestions = true,
  includeDependencies = false,
  hideDependencies = false,
) {
  return !!(
    (task.preparation_issue &&
      (!task.blocking_questions.length ||
        task.readiness === "needs_reconciliation")) ||
    (!hideDependencies &&
      (includeDependencies || !combinedDependencies(task)) &&
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
  hideDependencies = false,
}: {
  task: TaskCard;
  projectId: string;
  pendingCode?: components["schemas"]["PendingCode"][];
  open: (id: string) => void;
  detailed?: boolean;
  showQuestions?: boolean;
  includeDependencies?: boolean;
  hideDependencies?: boolean;
}) {
  return (
    <ul className="task-needs">
      {task.preparation_issue &&
        (!task.blocking_questions.length ||
          task.readiness === "needs_reconciliation") && (
          <li>{task.preparation_issue}</li>
        )}
      {!hideDependencies &&
        (includeDependencies || !combinedDependencies(task)) &&
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
                to={`/workspaces/${projectId}/requests/question/${q.id}`}
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
              <QuestionLink workspaceId={projectId} id={q.id}>
                {q.question}
              </QuestionLink>
            )}
          </li>
        ))}
      {pendingCode.map((prerequisite) => (
        <li key={prerequisite.key}>
          <TaskLink projectId={projectId} task={prerequisite} open={open} />
          {prerequisite.reuse_action?.action === "revalidate" ? (
            <>
              {": "}
              {prerequisite.reuse_action.reason}{" "}
              {prerequisite.result_href && (
                <WorkspaceLink to={prerequisite.result_href}>
                  {prerequisite.reuse_action.label} for {prerequisite.key}
                </WorkspaceLink>
              )}
            </>
          ) : (
            " — prerequisite code is unavailable. Open the task for details."
          )}
        </li>
      ))}
    </ul>
  );
}
