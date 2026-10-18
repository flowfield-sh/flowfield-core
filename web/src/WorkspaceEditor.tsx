import { useId, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { ContentStack } from "./DetailLayout";
import { DetailHeader, DetailTabs } from "./Presentation";
import { MarkdownField } from "./Markdown";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { AgentSettingsForm } from "./AgentSettings";
import { ReviewerSettings } from "./ReviewerSettings";
import { DetailSection } from "./DetailLayout";
import { QueueSettings, WorkerSettings } from "./Workers";
import { workspaceHref } from "./navigation";
import type { WorkspaceRecord } from "./workspace";

const fields = (record?: WorkspaceRecord) => ({
  name: record?.name ?? "",
  description: record?.description ?? "",
});
export function WorkspaceEditor({
  incoming,
  onDirty,
  saved,
  close,
}: {
  incoming: WorkspaceRecord;
  onDirty: (dirty: boolean) => void;
  saved: (workspace: WorkspaceRecord) => void;
  close: () => void;
}) {
  const id = useId();
  const { workspaceTab, projectTab } = useParams();
  const selectedTab = workspaceTab ?? projectTab;
  const tab =
    selectedTab === "workers" ||
    selectedTab === "coordinator" ||
    selectedTab === "reviewers"
      ? selectedTab
      : "general";
  const navigate = useNavigate();
  const location = useLocation();
  const [workerDirty, setWorkerDirty] = useState(false);
  const [coordinatorDirty, setCoordinatorDirty] = useState(false);
  const [taskReviewerDirty, setTaskReviewerDirty] = useState(false);
  const path = `workspaces/${incoming.id}`;
  const state = useRecordEditor({
    recordName: "workspace settings",
    incoming,
    fields,
    path: () => path,
    onDirty,
    saved,
    otherDirty: workerDirty || coordinatorDirty || taskReviewerDirty,
  });
  const {
    element: editor,
    values,
    dirty,
    busy,
    cancel,
    change,
    run,
    loaded,
  } = state;
  return (
    <section ref={editor} className="editor" aria-label="Edit workspace">
      <DetailHeader title={incoming.name} close={close} />
      <DetailTabs
        id={id}
        title="Workspace settings"
        tabs={["general", "coordinator", "workers", "reviewers"]}
        active={tab}
        change={(value) =>
          void navigate(
            `${workspaceHref(incoming.id)}/edit${value === "general" ? "" : `/${value}`}`,
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
      >
        <ContentStack space="section">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void run("PUT", {
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
                  required
                  maxLength={240}
                  value={values.name}
                  onChange={(event) => change("name", event.target.value)}
                />
              </Label>
              <MarkdownField
                label="Description"
                value={values.description}
                onChange={(value) => change("description", value)}
                previewEnabled={false}
              />
              <div className="actions">
                <Button size="sm" disabled={!dirty}>
                  Save changes
                </Button>
                <Button
                  size="sm"
                  type="button"
                  variant="outline"
                  disabled={!dirty}
                  onClick={cancel}
                >
                  Cancel
                </Button>
              </div>
            </fieldset>
          </form>
          <Separator />
          <QueueSettings workspaceId={incoming.id} refresh={incoming} />
        </ContentStack>
      </div>
      <div
        role="tabpanel"
        id={`${id}-coordinator`}
        aria-labelledby={`${id}-coordinator-tab`}
        hidden={tab !== "coordinator"}
      >
        <AgentSettingsForm
          workspaceId={incoming.id}
          path={`${path}/coordinator-settings`}
          refresh={incoming}
          onDirty={setCoordinatorDirty}
          coordinator
        />
      </div>
      <div
        role="tabpanel"
        id={`${id}-workers`}
        aria-labelledby={`${id}-workers-tab`}
        hidden={tab !== "workers"}
      >
        <WorkerSettings
          workspaceId={incoming.id}
          refresh={incoming}
          onDirty={setWorkerDirty}
        />
      </div>
      <div
        role="tabpanel"
        id={`${id}-reviewers`}
        aria-labelledby={`${id}-reviewers-tab`}
        hidden={tab !== "reviewers"}
      >
        <ContentStack space="section">
          <DetailSection title="Task reviewer">
            <ReviewerSettings
              workspaceId={incoming.id}
              refresh={incoming}
              onDirty={setTaskReviewerDirty}
              active={tab === "reviewers"}
            />
          </DetailSection>
        </ContentStack>
      </div>
    </section>
  );
}
