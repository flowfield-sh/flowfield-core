import { Button } from "@/components/ui/button";
import type { components } from "./api-schema";
import { TaskLink } from "./TaskLinks";
import { StatusNotice, StatusNoticeItems } from "./StatusNotice";
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
        task.state?.reason !== "scheduling" &&
        (!task.blocking_questions.length ||
          task.readiness === "needs_reconciliation") && (
          <StatusNotice
            title={
              task.state?.reason === "preview_setup"
                ? "Preview setup needed"
                : "Work needs attention"
            }
            tone="attention"
            actions={
              task.state?.reason === "preview_setup" &&
              task.project_id && (
                <Button variant="outline" size="sm" asChild>
                  <WorkspaceLink
                    to={`/projects/${task.project_id}/edit/execution`}
                  >
                    Configure preview
                  </WorkspaceLink>
                </Button>
              )
            }
          >
            <p>{task.execution_issue}</p>
          </StatusNotice>
        )}
      {task.blocking_questions.map((q) => (
        <StatusNotice
          key={q.id}
          title={
            q.status === "open"
              ? "Your answer is needed"
              : (q.delivery?.state ?? "Answer saved")
          }
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
        </StatusNotice>
      ))}
      {pendingCode.length > 0 && (
        <StatusNotice
          title={
            pendingCode.every(
              (item) => item.reuse_action?.action === "revalidate",
            )
              ? "Prerequisite code needs checking"
              : pendingCode.every(
                    (item) => item.reuse_action?.action !== "revalidate",
                  )
                ? "Prerequisite code is unavailable"
                : "Prerequisite code needs attention"
          }
          tone="attention"
        >
          <StatusNoticeItems>
            {pendingCode.map((prerequisite) => (
              <div className="content-stack" key={prerequisite.key}>
                <p>
                  <TaskLink
                    projectId={projectId}
                    task={prerequisite}
                    badge
                    open={open}
                  />{" "}
                  {prerequisite.title}
                </p>
                {pendingCode.some(
                  (item) => item.reuse_action?.action === "revalidate",
                ) &&
                  pendingCode.some(
                    (item) => item.reuse_action?.action !== "revalidate",
                  ) && (
                    <p>
                      {prerequisite.reuse_action?.action === "revalidate"
                        ? "Needs checking"
                        : "Code unavailable"}
                    </p>
                  )}
                {prerequisite.result_href && (
                  <div className="actions">
                    <Button variant="outline" size="sm" asChild>
                      <WorkspaceLink to={prerequisite.result_href}>
                        View prerequisite result
                      </WorkspaceLink>
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </StatusNoticeItems>
        </StatusNotice>
      )}
    </>
  );
}
