import { DetailHeader } from "./Presentation";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { MarkdownField } from "./Markdown";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { label, taskTypes, type Board, type Task } from "./workspace";
const fields = () => ({
  title: "",
  body: "",
  task_type: "feature" as Task["task_type"],
  milestone_id: "",
  dependencies: [] as string[],
});
type Values = ReturnType<typeof fields>;
export function NewTask({
  board,
  path,
  setUnsaved,
  saved,
  close,
}: {
  board: Board;
  path: string;
  setUnsaved: (dirty: boolean) => void;
  saved: (task: Task) => void;
  close: () => void;
}) {
  const state = useRecordEditor<Task, Values>({
    recordName: "task",
    fields,
    path: () => path + "/view/tasks",
    onDirty: setUnsaved,
    saved,
  });
  const { values, change, run, busy } = state;
  function field(
    key: Exclude<keyof Values, "dependencies">,
    title: string,
    multiline = false,
    required = false,
  ) {
    if (multiline)
      return (
        <MarkdownField
          label={title}
          value={values[key]}
          onChange={(value) => change(key, value)}
          rows={key === "body" ? 5 : 3}
          previewEnabled={false}
          maxLength={400020}
        />
      );
    return (
      <Label className="field block">
        {title}
        <Input
          value={values[key]}
          maxLength={200}
          required={required}
          onChange={(e) => change(key, e.target.value)}
        />
      </Label>
    );
  }

  return (
    <section className="editor" aria-label="New task">
      <DetailHeader title="New task" close={close} />
      <EditorFeedback state={state} />{" "}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const content = {
            title: values.title,
            stages: [
              {
                id: "work",
                title: values.title.slice(0, 80),
                outcome: values.title,
              },
            ],
            body: values.body,
            task_type: values.task_type,
            milestone_id: values.milestone_id || null,
            dependencies: values.dependencies,
          };
          void run("POST", {
            ...content,
            author: "human",
          });
        }}
      >
        <fieldset
          disabled={busy}
          className="content-stack"
          data-space="section"
        >
          <div className="content-stack" data-space="section">
            <>
              {field("title", "Title", false, true)}
              <div className="two-fields">
                <Label className="field block">
                  Type
                  <NativeSelect
                    value={values.task_type}
                    onChange={(e) =>
                      change("task_type", e.target.value as Task["task_type"])
                    }
                  >
                    {taskTypes.map((type) => (
                      <option value={type} key={type}>
                        {label(type)}
                      </option>
                    ))}
                  </NativeSelect>
                </Label>
                <Label className="field block">
                  Milestone
                  <NativeSelect
                    value={values.milestone_id}
                    onChange={(e) => change("milestone_id", e.target.value)}
                  >
                    <option value="">No milestone</option>
                    {board.milestones.map((milestone) => (
                      <option key={milestone.id} value={milestone.id}>
                        {milestone.key} · {milestone.title}
                      </option>
                    ))}
                  </NativeSelect>
                </Label>
              </div>
              {field("body", "Description", true)}
            </>
          </div>
          <div className="actions editor-actions">
            <Button size="sm" disabled={!values.title.trim()}>
              Create task
            </Button>
          </div>
        </fieldset>
      </form>
    </section>
  );
}
