import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ActionTooltip } from "./ActionTooltip";
import { ContentStack } from "./DetailLayout";
import { MarkdownField } from "./Markdown";
import { DetailHeader } from "./Presentation";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { TaskLabels } from "./TaskLabels";
import { useLabels } from "./labelContext";
import type { DirtyCallback } from "./retainedState";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { type Task } from "./workspace";

const fields = (task?: Task) => ({
  title: task?.title ?? "",
  body: task?.body ?? "",
  acceptance_criteria: task?.acceptance_criteria ?? "",
  labels: task?.labels ?? [],
});

export function TaskEditor({
  task,
  workspaceId,
  onDirty,
  saved,
  close,
}: {
  task: Task;
  workspaceId: string;
  onDirty: DirtyCallback;
  saved: () => void;
  close: () => void;
}) {
  const labels = useLabels();
  const path = `workspaces/${workspaceId}/view/tasks/${task.id}`;
  const state = useRecordEditor({
    incoming: task,
    fields,
    onDirty,
    recordName: "task",
    retainedKey: `${path}:edit`,
    path: () => path,
    saved: (_record, method) => {
      if (method === "PUT") saved();
    },
  });
  const { loaded, values, change, dirty, busy, element } = state;
  return (
    <section ref={element} className="editor" aria-label="Edit task">
      <DetailHeader title="Edit task" close={close} />
      <ContentStack space="section">
        {!task.edit_blocker && <EditorFeedback state={state} />}
        {loaded && (
          <form
            className="content-stack"
            data-space="section"
            onSubmit={(event) => {
              event.preventDefault();
              if (task.edit_blocker) return;
              void state.run("PUT", {
                ...values,
                expected_revision: loaded.revision,
                author: "human",
              });
            }}
          >
            <fieldset
              disabled={busy}
              className="content-stack"
              data-space="section"
            >
              <Label className="field block">
                Task title
                <Input
                  required
                  maxLength={200}
                  value={values.title}
                  onChange={(event) => change("title", event.target.value)}
                />
              </Label>
              <MarkdownField
                label="Description"
                rows={12}
                value={values.body}
                onChange={(value) => change("body", value)}
              />
              <MarkdownField
                label="Acceptance criteria"
                rows={6}
                value={values.acceptance_criteria}
                onChange={(value) => change("acceptance_criteria", value)}
              />
              <fieldset className="content-stack">
                <legend className="mb-2 text-sm font-medium">Labels</legend>
                {labels
                  .filter(
                    (label) =>
                      !label.deleted_at || values.labels.includes(label.id),
                  )
                  .map((label) => (
                    <SettingsCheckbox
                      key={label.id}
                      checked={values.labels.includes(label.id)}
                      onChange={(event) =>
                        change(
                          "labels",
                          event.target.checked
                            ? [...values.labels, label.id].sort()
                            : values.labels.filter((id) => id !== label.id),
                        )
                      }
                    >
                      <TaskLabels ids={[label.id]} />
                    </SettingsCheckbox>
                  ))}
                {!labels.some(
                  (label) =>
                    !label.deleted_at || values.labels.includes(label.id),
                ) && (
                  <p className="detail-metadata">
                    No labels have been added to this workspace.
                  </p>
                )}
              </fieldset>
            </fieldset>
            <div className="actions">
              <ActionTooltip label={task.edit_blocker} disabled>
                <Button
                  size="sm"
                  disabled={
                    busy ||
                    !!task.edit_blocker ||
                    !values.title.trim() ||
                    !dirty
                  }
                >
                  Save task
                </Button>
              </ActionTooltip>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void state.cancel(saved)}
              >
                Cancel
              </Button>
            </div>
          </form>
        )}
      </ContentStack>
    </section>
  );
}
