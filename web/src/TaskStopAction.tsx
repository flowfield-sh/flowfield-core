import { Square } from "lucide-react";
import { toast } from "sonner";
import { ConfirmButton } from "./ConfirmButton";
import { reportError } from "./requestFeedback";
import { request, type Task } from "./workspace";

export function TaskStopAction({
  task,
  workspaceId,
  busy,
  setBusy,
}: {
  task: Task;
  workspaceId: string;
  busy: boolean;
  setBusy: (value: boolean) => void;
}) {
  if (!task.can_stop) return null;
  return (
    <ConfirmButton
      tooltip="Stop task"
      size="icon-sm"
      variant="outline"
      aria-label="Stop task"
      disabled={busy}
      binding={`${workspaceId}:${task.id}:${task.revision}`}
      title={`Stop ${task.key}?`}
      description="Stop active Worker and Reviewer execution and move this task to Backlog. Recorded work, files and history are retained."
      actionLabel="Stop task"
      action={async () => {
        setBusy(true);
        try {
          await request(
            `workspaces/${workspaceId}/tasks/${task.id}/stop`,
            "POST",
            { expected_revision: task.revision, author: "human" },
          );
          toast.success("Task moved to Backlog.");
        } catch (error) {
          reportError(error, "Could not stop task");
        } finally {
          setBusy(false);
        }
      }}
    >
      <Square aria-hidden="true" />
    </ConfirmButton>
  );
}
