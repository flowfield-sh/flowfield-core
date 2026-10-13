import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { DetailHeader } from "./Presentation";
import { Markdown, MarkdownField } from "./Markdown";
import { ContentStack, DetailSection } from "./DetailLayout";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { RevisionHistory } from "./RevisionHistory";
import { WorkspaceLink } from "./WorkspaceLink";
import { boardHref } from "./navigation";
import type { Milestone } from "./workspace";

const fields = (record?: Milestone) => ({
  title: record?.title ?? "",
  body: record?.body ?? "",
  acceptance_criteria: record?.acceptance_criteria ?? "",
});
export function MilestoneEditor({
  incoming,
  latest,
  historical = false,
  path,
  onDirty,
  saved,
  close,
}: {
  incoming?: Milestone;
  latest?: Milestone;
  historical?: boolean;
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
      className="editor documents-page"
      aria-label={loaded ? "Milestone" : "New milestone"}
    >
      <DetailHeader
        title={loaded ? `${loaded.key} · ${loaded.title}` : "New milestone"}
        close={close}
        closeLabel={loaded ? "Back" : "Close editor"}
      />
      <div className={loaded ? "document-layout" : "document-new"}>
        <ContentStack space="section" className="document-content">
          {loaded && (
            <div className="actions">
              <WorkspaceLink
                to={boardHref(loaded.workspace_id, { milestone: loaded.id })}
              >
                View tasks
              </WorkspaceLink>
              {historical ? (
                <>
                  <WorkspaceLink
                    to={`/workspaces/${loaded.workspace_id}/milestones/${loaded.key}`}
                  >
                    Current milestone
                  </WorkspaceLink>
                </>
              ) : (
                !state.editing && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => state.setEditing(true)}
                  >
                    Edit milestone
                  </Button>
                )
              )}
            </div>
          )}
          <EditorFeedback state={state} />
          {(!loaded || state.editing) && !historical ? (
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
                <div className="actions justify-end">
                  <Button
                    size="sm"
                    disabled={!values.title.trim() || (!!loaded && !dirty)}
                  >
                    {loaded ? "Save changes" : "Create milestone"}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    type="button"
                    onClick={loaded ? state.cancel : close}
                  >
                    Cancel
                  </Button>
                </div>
              </fieldset>
            </form>
          ) : (
            loaded && (
              <ContentStack space="section">
                <DetailSection title="Description">
                  <Markdown>{loaded.body || "No description yet."}</Markdown>
                </DetailSection>
                <DetailSection title="Acceptance criteria">
                  <Markdown>
                    {loaded.acceptance_criteria ||
                      "No acceptance criteria yet."}
                  </Markdown>
                </DetailSection>
              </ContentStack>
            )
          )}
        </ContentStack>
        {loaded && (
          <RevisionHistory
            key={loaded.id}
            kind="Milestone"
            path={`${path}/milestones/${loaded.id}`}
            href={`/workspaces/${loaded.workspace_id}/milestones/${loaded.key}`}
            revision={latest?.revision ?? loaded.revision}
            selected={loaded.revision}
          />
        )}
      </div>
    </section>
  );
}
