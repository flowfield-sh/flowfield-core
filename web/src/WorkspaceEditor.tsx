import { useId, useState, type FormEvent } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ContentStack } from "./DetailLayout";
import { DetailHeader, DetailTabs } from "./Presentation";
import { MarkdownField } from "./Markdown";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { AgentSettingsForm } from "./AgentSettings";
import { ReviewerSettings } from "./ReviewerSettings";
import { WorkerSettings } from "./Workers";
import { workspaceHref } from "./navigation";
import type { WorkspaceRecord } from "./workspace";
import { WorkspaceLabels } from "./WorkspaceLabels";

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
  const [labelsDirty, setLabelsDirty] = useState(false);
  const path = `workspaces/${incoming.id}`;
  const state = useRecordEditor({
    recordName: "workspace settings",
    incoming,
    fields,
    path: () => path,
    onDirty,
    saved,
    otherDirty:
      workerDirty || coordinatorDirty || taskReviewerDirty || labelsDirty,
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
  function save(event: FormEvent) {
    event.preventDefault();
    // Both tabs share the workspace revision/draft owner. Omit unchanged fields
    // so a General save cannot write a stale, hidden startup preference.
    const original = fields(loaded);
    void run("PUT", {
      ...Object.fromEntries(
        Object.entries(values).filter(
          ([key, value]) => value !== original[key as keyof typeof original],
        ),
      ),
      expected_revision: loaded!.revision,
      author: "human",
    });
  }
  const actions = (
    <div className="actions">
      <Button size="sm" disabled={!dirty}>
        Save changes
      </Button>
      <Button
        size="sm"
        type="button"
        variant="outline"
        disabled={!dirty}
        onClick={() => cancel()}
      >
        Cancel
      </Button>
    </div>
  );
  return (
    <section
      ref={editor}
      className="editor content-stack"
      data-space="section"
      aria-label="Edit workspace"
    >
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
        <ContentStack space="settings">
          <form onSubmit={save}>
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
              {actions}
            </fieldset>
          </form>
          <WorkspaceLabels
            workspaceId={incoming.id}
            refresh={incoming}
            onDirty={setLabelsDirty}
          />
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
          workspaceId={incoming.id}
          refresh={incoming}
          onDirty={setWorkerDirty}
          active={tab === "workers"}
        />
      </div>
      <div
        role="tabpanel"
        id={`${id}-reviewers`}
        aria-labelledby={`${id}-reviewers-tab`}
        hidden={tab !== "reviewers"}
      >
        <ContentStack space="settings">
          <ReviewerSettings
            workspaceId={incoming.id}
            refresh={incoming}
            onDirty={setTaskReviewerDirty}
            active={tab === "reviewers"}
          />
        </ContentStack>
      </div>
    </section>
  );
}
