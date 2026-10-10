import { Separator } from "@/components/ui/separator";
import { ContentStack } from "./DetailLayout";
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
import { QueueSettings, WorkerSettings } from "./Workers";
import { ProjectGuidance } from "./ProjectGuidance";
import { IntegrationSettings } from "./Integration";

const fields = (record?: Project) => ({
  name: record?.name ?? "",
  description: record?.description ?? "",
  task_prefix: record?.task_prefix ?? "",
});
export function ProjectEditor({
  incoming,
  path,
  hasTasks,
  onDirty,
  saved,
  close,
}: {
  incoming: Project;
  path: string;
  hasTasks: boolean;
  onDirty: (value: boolean) => void;
  saved: (record: Project) => void;
  close: () => void;
}) {
  const id = useId();
  const { projectTab } = useParams();
  const tab =
    projectTab === "workers" ||
    projectTab === "integration" ||
    projectTab === "coordinator"
      ? projectTab
      : "general";
  const navigate = useNavigate();
  const location = useLocation();
  const [workerDirty, setWorkerDirty] = useState(false);
  const [integrationDirty, setIntegrationDirty] = useState(false);
  const state = useRecordEditor({
    recordName: "project settings",
    incoming,
    fields,
    path: () => path,
    onDirty,
    saved,
    otherDirty: workerDirty || integrationDirty,
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
        tabs={["general", "coordinator", "workers", "integration"]}
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
            <ContentStack space="tight">
              <Label className="field block">
                Prefix
                <Input
                  aria-describedby={`${id}-prefix-help`}
                  value={values.task_prefix}
                  required
                  pattern="[A-Za-z]{3}"
                  minLength={3}
                  maxLength={3}
                  disabled={hasTasks}
                  onChange={(event) =>
                    change("task_prefix", event.target.value.toUpperCase())
                  }
                />
              </Label>
              <p id={`${id}-prefix-help`} className="detail-metadata">
                {hasTasks
                  ? "Fixed after the first task to preserve keys and links."
                  : "Choose a unique three-letter prefix before creating tasks."}
              </p>
            </ContentStack>
            <MarkdownField
              label="Description"
              value={values.description}
              onChange={(value) => change("description", value)}
              previewEnabled={false}
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
        <Separator />
        <QueueSettings projectId={incoming.id} refresh={incoming} />
      </div>
      <div
        role="tabpanel"
        id={`${id}-coordinator`}
        aria-labelledby={`${id}-coordinator-tab`}
        hidden={tab !== "coordinator"}
        className="content-stack"
        data-space="section"
      >
        <ProjectGuidance
          projectId={incoming.id}
          active={tab === "coordinator"}
        />
      </div>
      <div
        role="tabpanel"
        id={`${id}-workers`}
        aria-labelledby={`${id}-workers-tab`}
        hidden={tab !== "workers"}
      >
        <WorkerSettings
          projectId={incoming.id}
          onDirty={setWorkerDirty}
          refresh={incoming}
        />
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
