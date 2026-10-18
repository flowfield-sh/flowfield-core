import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useParams, useLocation, useNavigate } from "react-router";
import { projectHref } from "./navigation";
import { DetailHeader, DetailTabs } from "./Presentation";
import { MarkdownField } from "./Markdown";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import type { Project } from "./workspace";
import { useId, useState } from "react";
import { IntegrationSettings } from "./Integration";
import { WorkerDocumentPicker } from "./WorkerDocuments";

const fields = (record?: Project) => ({
  name: record?.name ?? "",
  description: record?.description ?? "",
  worker_documents: record?.worker_documents ?? [],
});
export function ProjectEditor({
  incoming,
  workspaceId,
  path,
  onDirty,
  saved,
  close,
}: {
  incoming: Project;
  workspaceId: string;
  path: string;
  onDirty: (value: boolean) => void;
  saved: (record: Project) => void;
  close: () => void;
}) {
  const id = useId();
  const { projectTab } = useParams();
  const tab = projectTab === "integration" ? "integration" : "general";
  const navigate = useNavigate();
  const location = useLocation();
  const [integrationDirty, setIntegrationDirty] = useState(false);
  const state = useRecordEditor({
    recordName: "project settings",
    incoming,
    fields,
    path: () => path,
    onDirty,
    saved,
    otherDirty: integrationDirty,
  });
  const {
    values,
    loaded,
    busy,
    dirty,
    change,
    element: editor,
    cancel,
  } = state;
  return (
    <section ref={editor} className="editor" aria-label="Edit project">
      <DetailHeader title={values.name} close={close} />
      <DetailTabs
        id={id}
        title="Project settings"
        tabs={["general", "integration"]}
        active={tab}
        change={(value) =>
          void navigate(
            projectHref(incoming.id) +
              "/edit" +
              (value === "general" ? "" : "/" + value),
            { state: location.state },
          )
        }
      />
      <EditorFeedback state={state} />
      <div
        role="tabpanel"
        id={`${id}-general`}
        aria-labelledby={`${id}-general-tab`}
        hidden={tab !== "general"}
        className="content-stack"
        data-space="section"
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void state.run("PUT", {
              ...values,
              expected_revision: loaded!.revision,
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
              Name
              <Input
                value={values.name}
                maxLength={200}
                required
                onChange={(event) => change("name", event.target.value)}
              />
            </Label>
            <MarkdownField
              label="Description"
              value={values.description}
              onChange={(value) => change("description", value)}
              previewEnabled={false}
            />
            <WorkerDocumentPicker
              workspaceId={workspaceId}
              value={values.worker_documents}
              change={(value) => change("worker_documents", value)}
            />
            <div className="actions editor-actions">
              <Button size="sm" disabled={!dirty}>
                Save changes
              </Button>
              <Button
                size="sm"
                variant="outline"
                type="button"
                disabled={!dirty}
                onClick={cancel}
              >
                Cancel
              </Button>
            </div>
          </fieldset>
        </form>
      </div>
      <div
        role="tabpanel"
        id={`${id}-integration`}
        aria-labelledby={`${id}-integration-tab`}
        hidden={tab !== "integration"}
      >
        <IntegrationSettings
          projectId={incoming.id}
          onDirty={setIntegrationDirty}
          refresh={incoming}
        />
      </div>
    </section>
  );
}
