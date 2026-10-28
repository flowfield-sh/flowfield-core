import { Button } from "@/components/ui/button";
import { ContentStack } from "./DetailLayout";
import { StatusNotice } from "./StatusNotice";
import { TaskNeeds, hasTaskNeeds } from "./TaskNeeds";
import { TaskReview } from "./TaskReview";
import { WorkspaceLink } from "./WorkspaceLink";
import { resultHref } from "./navigation";
import type { Task, WorkspaceBoard } from "./workspace";

/** Current task notices have one position; historical diagnostics stay in the feed. */
export function TaskNotices({
  task,
  workspaceId,
  board,
  openTask,
}: {
  task: Task;
  workspaceId: string;
  board: WorkspaceBoard;
  openTask: (id: string) => void;
}) {
  return (
    <ContentStack className="task-notices" aria-label="Task notices">
      {task.state?.reason === "scheduling" && (
        <StatusNotice title="Work cannot start" tone="attention">
          <p>{task.scheduling_problem}</p>
        </StatusNotice>
      )}
      {task.state?.reason === "prerequisites" && (
        <StatusNotice title="Waiting on prerequisites" />
      )}
      {hasTaskNeeds(task, board.pending_code[task.key], true, false, true) && (
        <StatusNotice title="Waiting on" tone="attention">
          <TaskNeeds
            task={task}
            projectId={workspaceId}
            pendingCode={board.pending_code[task.key]}
            open={openTask}
            detailed
            hideDependencies
          />
        </StatusNotice>
      )}
      <TaskReview
        projectId={workspaceId}
        taskId={task.id}
        taskKey={task.key}
        refresh={board}
        guidanceOnly
        notice
      />
      {task.latest_result_id && (
        <StatusNotice
          title={
            task.status === "done" ? "Result delivered" : "Result available"
          }
          tone={task.status === "done" ? "success" : "neutral"}
          actions={
            <Button variant="outline" size="sm" asChild>
              <WorkspaceLink
                to={resultHref(workspaceId, task.latest_result_id!)}
              >
                View result
              </WorkspaceLink>
            </Button>
          }
        />
      )}
    </ContentStack>
  );
}
