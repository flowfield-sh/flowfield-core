import { useState } from "react";
import { Button } from "@/components/ui/button";
import { LabelPicker } from "./TaskLabels";
import { Popover } from "radix-ui";
import { TaskLabels } from "./TaskLabels";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import type { Task } from "./workspace";

const fields = (task?: Task) => ({ labels: task?.labels ?? [] });
export function TaskLabelEditor({
  task,
  path,
  onDirty,
  saved,
}: {
  task: Task;
  path: string;
  onDirty: (dirty: boolean) => void;
  saved: (task: Task) => void;
}) {
  const [open, setOpen] = useState(false);
  const state = useRecordEditor({
    incoming: task,
    fields,
    recordName: "task labels",
    path: () => `${path}/view/tasks/${task.id}`,
    onDirty,
    saved: (record) => {
      saved(record);
      setOpen(false);
    },
  });
  const {
    element: editor,
    values,
    busy,
    dirty,
    cancel,
    change,
    run,
    loaded,
  } = state;
  return (
    <Popover.Root
      open={open}
      onOpenChange={(value) => {
        if (value || (!dirty && !busy)) setOpen(value);
      }}
    >
      <Popover.Trigger asChild>
        <Button
          size="sm"
          variant="ghost"
          aria-label="Edit labels"
          className="task-label-trigger"
        >
          <TaskLabels ids={task.labels.slice(0, 2)} />
          {task.labels.length > 2
            ? `+${task.labels.length - 2}`
            : !task.labels.length
              ? "Labels"
              : null}
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          collisionPadding={16}
          className="composer-settings"
          aria-label="Edit task labels"
          onEscapeKeyDown={(event) => {
            if (dirty || busy) event.preventDefault();
          }}
        >
          <section ref={editor}>
            <EditorFeedback state={state} />
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void run("PUT", {
                  labels: values.labels,
                  expected_revision: loaded!.revision,
                  author: "human",
                });
              }}
            >
              <fieldset
                disabled={busy}
                className="content-stack"
                data-space="content"
              >
                <LabelPicker
                  value={values.labels}
                  change={(value) => change("labels", value)}
                />
                <div className="actions">
                  <Button size="sm" disabled={!dirty}>
                    Save labels
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!dirty}
                    onClick={cancel}
                  >
                    Cancel
                  </Button>
                </div>
              </fieldset>
            </form>
          </section>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
