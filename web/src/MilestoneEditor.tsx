import type { ReactNode } from "react";
import { DetailSection } from "./DetailLayout";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { DetailHeader } from "./Presentation";
import { Markdown, MarkdownField } from "./Markdown";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import type { Milestone } from "./workspace";

const fields = (record?: Milestone) => ({
  title: record?.title ?? "",
  body: record?.body ?? "",
});
export function MilestoneEditor({
  incoming,
  path,
  onDirty,
  saved,
  close,
  viewTasks,
}: {
  incoming?: Milestone;
  path: string;
  onDirty: (value: boolean) => void;
  saved: (record: Milestone) => void;
  close: () => void;
  viewTasks?: ReactNode;
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
  const {
    values,
    loaded,
    editing,
    busy,
    dirty,
    change,
    element: editor,
    cancel,
  } = state;
  return (
    <section
      ref={editor}
      className={incoming ? "embedded-editor" : "editor"}
      aria-label={loaded ? "Edit milestone" : "New milestone"}
    >
      {!incoming && <DetailHeader title="New milestone" close={close} />}
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
          {editing ? (
            <>
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
                previewEnabled={false}
              />
            </>
          ) : (
            <DetailSection className="record-info" title={<>Description</>}>
              <div className="agreement-content">
                <Markdown>{values.body || "No description yet."}</Markdown>
              </div>
            </DetailSection>
          )}
          <div className="actions editor-actions">
            {editing ? (
              <>
                <Button
                  size="sm"
                  disabled={!values.title.trim() || (!!loaded && !dirty)}
                >
                  {loaded ? "Save changes" : "Create milestone"}
                </Button>
                {loaded && (
                  <Button
                    size="sm"
                    variant="outline"
                    type="button"
                    className="quiet"
                    onClick={cancel}
                  >
                    Cancel
                  </Button>
                )}
              </>
            ) : (
              <>
                <Button
                  size="sm"
                  type="button"
                  variant="outline"
                  onClick={() => state.setEditing(true)}
                >
                  Edit
                </Button>
                {viewTasks}
              </>
            )}
          </div>
        </fieldset>
      </form>
    </section>
  );
}
