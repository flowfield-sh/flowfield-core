import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ContentStack, DetailHeading } from "./DetailLayout";
import { ProjectBadge } from "./ProjectBadge";
import { WorkspaceLink } from "./WorkspaceLink";
import { workspaceHref } from "./navigation";
import { request, type WorkspaceRecord } from "./workspace";
import { reportError } from "./requestFeedback";

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
        <div className="project-list">
          {workspaces.map((workspace) => (
            <WorkspaceLink
              key={workspace.id}
              to={workspaceHref(workspace.id)}
              className="project-list-row"
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
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    onDirty(!!name);
  }, [name, onDirty]);
  return (
    <section className="editor" aria-label="New workspace form">
      <ContentStack space="section" className="pane-form">
        <p>
          A workspace brings projects together around one Coordinator, shared
          milestones and a board. You can add projects after creating it.
        </p>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (busy || !name.trim()) return;
            setBusy(true);
            try {
              const workspace = await request<WorkspaceRecord>(
                "workspaces",
                "POST",
                { name: name.trim() },
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
            <div className="actions">
              <Button type="submit">
                {busy ? "Creating workspace…" : "Create workspace"}
              </Button>
            </div>
          </fieldset>
        </form>
      </ContentStack>
    </section>
  );
}
