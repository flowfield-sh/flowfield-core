import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { ActionTooltip } from "./ActionTooltip";
import { ConfirmButton } from "./ConfirmButton";
import { reportError } from "./requestFeedback";
import { request, type Task } from "./workspace";
import type { components } from "./api-schema";

export function TaskDeleteAction({
  task,
  workspaceId,
  busy,
  setBusy,
  dirty,
  close,
}: {
  task: Task;
  workspaceId: string;
  busy: boolean;
  setBusy: (value: boolean) => void;
  dirty: boolean;
  close: () => void;
}) {
  const blocker = dirty
    ? "Save or discard settings before deleting this task."
    : task.delete_blocker;
  return (
    <ActionTooltip
      label={blocker || "Delete task"}
      disabled={!!blocker || busy}
    >
      <ConfirmButton
        size="icon-sm"
        variant="outline"
        aria-label="Delete task"
        disabled={!!blocker || busy}
        binding={`${workspaceId}:${task.id}:${task.revision}`}
        title={`Delete ${task.key}?`}
        description="Permanently delete this task, its history, results, files and inspection copies, including local edits and preview data. Stop any running previews first. Delivered repository code stays. This cannot be undone."
        actionLabel="Delete task"
        action={async () => {
          setBusy(true);
          try {
            const deleted = await request<
              components["schemas"]["TaskDeletion"]
            >(`workspaces/${workspaceId}/tasks/${task.id}`, "DELETE", {
              expected_revision: task.revision,
              author: "human",
            });
            if (deleted.cleanup_pending)
              toast.info(
                "Task deleted. File cleanup is pending; Flowfield will retry.",
              );
            else toast.success("Task deleted.");
            close();
          } catch (error) {
            reportError(error, "Could not delete task");
          } finally {
            setBusy(false);
          }
        }}
      >
        <Trash2 aria-hidden="true" />
      </ConfirmButton>
    </ActionTooltip>
  );
}
