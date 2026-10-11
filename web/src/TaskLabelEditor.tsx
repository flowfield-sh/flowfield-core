import { Button } from "@/components/ui/button";
import { LabelPicker } from "./TaskLabels";
import { Disclosure } from "./DetailLayout";
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
  const state = useRecordEditor({
    incoming: task,
    fields,
    recordName: "task labels",
    path: () => `${path}/view/tasks/${task.id}`,
    onDirty,
    saved,
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
    <Disclosure summary="Labels">
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
    </Disclosure>
  );
}
