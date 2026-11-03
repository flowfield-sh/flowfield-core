import { Button } from "@/components/ui/button";
import type { components } from "./api-schema";
import { TaskLink } from "./TaskLinks";
import { StatusNotice } from "./StatusNotice";
import { WorkspaceLink } from "./WorkspaceLink";
import { requestHref } from "./requestLinks";
import type { TaskCard } from "./workspace";

export function TaskNeeds({
  task,
  projectId,
  pendingCode = [],
  open,
}: {
  task: TaskCard;
  projectId: string;
  pendingCode?: components["schemas"]["PendingCode"][];
  open: (id: string) => void;
}) {
  return (
    <>
      {task.execution_issue &&
        (!task.blocking_questions.length ||
          task.readiness === "needs_reconciliation") && (
          <StatusNotice title="Work needs attention" tone="attention">
            <p>{task.execution_issue}</p>
          </StatusNotice>
        )}
      {task.blocking_questions.map((q) => (
        <StatusNotice
          key={q.id}
          title={q.status === "open" ? "Your answer is needed" : "Answer saved"}
          tone={q.status === "open" ? "attention" : "neutral"}
          actions={
            <Button variant="outline" size="sm" asChild>
              <WorkspaceLink to={requestHref(projectId, "question", q.id)}>
                {q.status === "open" ? "Answer question" : "View answer"}
              </WorkspaceLink>
            </Button>
          }
        >
          <p>{q.question}</p>
          {q.delivery ? (
            <p>{q.delivery.message}</p>
          ) : q.status === "answered" ? (
            <p>The Coordinator needs to apply your saved answer.</p>
          ) : null}
        </StatusNotice>
      ))}
      {pendingCode.map((prerequisite) => (
        <StatusNotice
          key={prerequisite.key}
          title={
            prerequisite.reuse_action?.action === "revalidate"
              ? "Prerequisite code needs checking"
              : "Prerequisite code is unavailable"
          }
          tone="attention"
          actions={
            prerequisite.result_href && (
              <Button variant="outline" size="sm" asChild>
                <WorkspaceLink to={prerequisite.result_href}>
                  View prerequisite result
                </WorkspaceLink>
              </Button>
            )
          }
        >
          <p>
            <TaskLink projectId={projectId} task={prerequisite} open={open} />
          </p>
          {prerequisite.reuse_action?.action === "revalidate" && (
            <p>{prerequisite.reuse_action.reason}</p>
          )}
        </StatusNotice>
      ))}
    </>
  );
}
