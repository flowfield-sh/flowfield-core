import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { ContentStack, DetailHeading } from "./DetailLayout";
import { ProjectBadge } from "./ProjectBadge";
import { WorkspaceLink } from "./WorkspaceLink";
import { workspaceHref } from "./navigation";
import { request, type WorkspaceRecord } from "./workspace";
import { reportError } from "./requestFeedback";
import { AgentChoiceFields } from "./AgentChoiceFields";
import { SettingsCheckbox } from "./SettingsCheckbox";
import type { components } from "./api-schema";

export function WorkspaceList({
  workspaces,
  attentionCounts,
}: {
  workspaces: WorkspaceRecord[];
  attentionCounts: Record<string, number> | null;
}) {
  return (
    <section className="setup-page" aria-label="Workspaces">
      <ContentStack space="section" className="pane-form">
        <div className="detail-heading-row">
          <DetailHeading title="Workspaces" titleAs="h2" />
          <Button size="sm" asChild>
            <WorkspaceLink to="/new-workspace">
              <Plus />
              New workspace
            </WorkspaceLink>
          </Button>
        </div>
        {!workspaces.length && (
          <p>
            Create a workspace to coordinate your projects, plan work together
            and follow progress.
          </p>
        )}
        {!!workspaces.length && (
          <div className="project-list">
            {workspaces.map((workspace) => (
              <WorkspaceLink
                key={workspace.id}
                to={workspaceHref(workspace.id)}
                className="project-list-row workspace-list-row"
              >
                <ProjectBadge id={workspace.id} name={workspace.name} />
                <strong>{workspace.name}</strong>
                {!!attentionCounts?.[workspace.id] && (
                  <span className="workspace-inbox-count">
                    <span className="nav-count needs-attention">
                      {attentionCounts[workspace.id]}
                    </span>
                    <span>in Inbox</span>
                  </span>
                )}
              </WorkspaceLink>
            ))}
          </div>
        )}
      </ContentStack>
    </section>
  );
}

export function NewWorkspace({
  created,
  onDirty,
}: {
  created: (workspace: WorkspaceRecord) => void;
  onDirty: (dirty: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [shared, setShared] = useState<
    components["schemas"]["AgentChoice-Output"] | null
  >(null);
  const [customize, setCustomize] = useState(false);
  const [sharedReady, setSharedReady] = useState(false);
  const [coordinator, setCoordinator] = useState<
    components["schemas"]["AgentChoice-Output"] | null
  >(null);
  const [worker, setWorker] = useState<
    components["schemas"]["AgentChoice-Output"] | null
  >(null);
  const [reviewer, setReviewer] = useState<
    components["schemas"]["AgentChoice-Output"] | null
  >(null);
  const [coordinatorReady, setCoordinatorReady] = useState(false);
  const [workerReady, setWorkerReady] = useState(false);
  const [reviewerReady, setReviewerReady] = useState(false);
  const ready = customize
    ? coordinatorReady && workerReady && reviewerReady
    : sharedReady;
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    onDirty(!!name || !!shared || !!coordinator || !!worker || !!reviewer);
  }, [name, shared, coordinator, worker, reviewer, onDirty]);
  return (
    <section className="editor" aria-label="New workspace form">
      <ContentStack space="section" className="pane-form">
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (busy || !name.trim() || !ready) return;
            setBusy(true);
            try {
              const workspace = await request<WorkspaceRecord>(
                "workspaces",
                "POST",
                {
                  name: name.trim(),
                  coordinator: customize ? coordinator : shared,
                  worker: customize ? worker : shared,
                  reviewer: customize ? reviewer : shared,
                },
              );
              onDirty(false);
              created(workspace);
            } catch (error) {
              reportError(error, "Could not create workspace");
            } finally {
              setBusy(false);
            }
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
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </Label>
            <Separator />
            <SettingsCheckbox
              checked={customize}
              onChange={(event) => {
                if (event.target.checked) {
                  setCoordinator((value) => value ?? shared);
                  setWorker((value) => value ?? shared);
                  setReviewer((value) => value ?? shared);
                }
                setCustomize(event.target.checked);
              }}
            >
              Customize roles
            </SettingsCheckbox>
            {!customize ? (
              <AgentChoiceFields
                label="Agents"
                description="Use this choice for the Coordinator that plans work, Workers that carry it out and Reviewer that checks results."
                showFast
                value={shared}
                change={setShared}
                onReady={setSharedReady}
              />
            ) : (
              <>
                <AgentChoiceFields
                  label="Coordinator"
                  showFast
                  description="Plan work, answer questions and coordinate delivery across your projects."
                  value={coordinator}
                  change={setCoordinator}
                  onReady={setCoordinatorReady}
                />
                <Separator />
                <AgentChoiceFields
                  label="Worker"
                  description="Workers carry out tasks and submit results. Tasks inherit this choice unless overridden."
                  showFast
                  value={worker}
                  change={setWorker}
                  onReady={setWorkerReady}
                />
                <Separator />
                <AgentChoiceFields
                  label="Reviewer"
                  description="Reviewers independently check task results before delivery. Tasks inherit this choice unless overridden."
                  showFast
                  value={reviewer}
                  change={setReviewer}
                  onReady={setReviewerReady}
                />
              </>
            )}
            <div className="actions">
              <Button type="submit" disabled={!name.trim() || !ready}>
                {busy ? "Creating workspace…" : "Create workspace"}
              </Button>
            </div>
          </fieldset>
        </form>
      </ContentStack>
    </section>
  );
}
