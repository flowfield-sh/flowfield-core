import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { DetailHeader } from "./Presentation";
import { MarkdownField } from "./Markdown";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { MilestoneHistory } from "./MilestoneContext";
import type { Milestone } from "./workspace";

const fields = (record?: Milestone) => ({
  title: record?.title ?? "",
  body: record?.body ?? "",
  acceptance_criteria: record?.acceptance_criteria ?? "",
});
export function MilestoneEditor({
  incoming,
  path,
  onDirty,
  saved,
  close,
}: {
  incoming?: Milestone;
  path: string;
  onDirty: (value: boolean) => void;
  saved: (record: Milestone) => void;
  close: () => void;
}) {
  const state = useRecordEditor({
    recordName: "milestone",
    incoming,
    fields,
    path: (record) =>
      `${path}/milestones${record ? `/${encodeURIComponent(record.id)}` : ""}`,
    onDirty,
    saved,
  });
  const { values, loaded, busy, dirty, change, element: editor } = state;
  return (
    <section
      ref={editor}
      className="editor"
      aria-label={loaded ? "Edit milestone" : "New milestone"}
    >
      <DetailHeader
        title={loaded ? `Edit ${loaded.key}` : "New milestone"}
        close={close}
      />
      <EditorFeedback state={state} />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void state.run(loaded ? "PUT" : "POST", {
            ...values,
            ...(loaded ? { expected_revision: loaded.revision } : {}),
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
            Title
            <Input
              value={values.title}
              maxLength={200}
              required
              onChange={(event) => change("title", event.target.value)}
            />
          </Label>
          <MarkdownField
            label="Description"
            value={values.body}
            onChange={(value) => change("body", value)}
          />
          <MarkdownField
            label="Acceptance criteria"
            value={values.acceptance_criteria}
            onChange={(value) => change("acceptance_criteria", value)}
          />
          {loaded && <MilestoneHistory milestone={loaded} />}
          <div className="actions justify-end">
            <Button
              size="sm"
              disabled={!values.title.trim() || (!!loaded && !dirty)}
            >
              {loaded ? "Save changes" : "Create milestone"}
            </Button>
            <Button size="sm" variant="outline" type="button" onClick={close}>
              Cancel
            </Button>
          </div>
        </fieldset>
      </form>
    </section>
  );
}
