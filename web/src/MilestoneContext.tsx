import { useState } from "react";
import type { components } from "./api-schema";
import { Button } from "@/components/ui/button";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { Markdown } from "./Markdown";
import { ResourceRetry } from "./ResourceRetry";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { useResource } from "./useResource";
import { authorLabel, type Milestone } from "./workspace";

type Header = Pick<
  Milestone,
  "id" | "revision" | "title" | "updated_at" | "updated_by"
>;
type HistoryPage = { items: Header[]; next_cursor: number | null };
type Binding = components["schemas"]["MilestoneBinding"];

export function MilestoneRevision({ binding }: { binding: Binding }) {
  const source = useResource<Milestone>(
    `workspaces/${binding.workspace_id}/milestones/${binding.id}?revision=${binding.revision}`,
    binding.revision,
  );
  return (
    <ContentStack space="section">
      <ResourceRetry resources={[source]} />
      {source.loading && <p>Loading milestone…</p>}
      {source.data && (
        <>
          <p className="detail-metadata">
            Revision {source.data.revision} ·{" "}
            {authorLabel(source.data.updated_by)} ·{" "}
            <Timestamp date={source.data.updated_at} />
          </p>
          <Markdown>{source.data.body || "No description."}</Markdown>
          {!!source.data.acceptance_criteria && (
            <DetailSection title="Acceptance criteria">
              <Markdown>{source.data.acceptance_criteria}</Markdown>
            </DetailSection>
          )}
        </>
      )}
    </ContentStack>
  );
}

export function MilestoneHistory({ milestone }: { milestone: Milestone }) {
  const [open, setOpen] = useState(false);
  const [cursors, setCursors] = useState<number[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const before = cursors.at(-1);
  const history = useResource<HistoryPage>(
    open
      ? `workspaces/${milestone.workspace_id}/milestones/${milestone.id}/history?limit=10${before ? `&before=${before}` : ""}`
      : null,
    milestone.revision,
  );
  return (
    <Disclosure
      summary="Milestone history"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <ContentStack space="section">
        <ResourceRetry resources={[history]} />
        {history.loading && <p>Loading revisions…</p>}
        {history.data?.items.map((item) => (
          <ContentStack key={item.revision} space="tight">
            <div className="actions">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setSelected(item.revision)}
              >
                View revision {item.revision}
              </Button>
              <span className="detail-metadata">
                {authorLabel(item.updated_by)} ·{" "}
                <Timestamp date={item.updated_at} />
              </span>
            </div>
          </ContentStack>
        ))}
        <div className="actions">
          {!!cursors.length && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setCursors(cursors.slice(0, -1))}
            >
              Newer revisions
            </Button>
          )}
          {history.data?.next_cursor && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                setCursors([...cursors, history.data!.next_cursor!])
              }
            >
              Older revisions
            </Button>
          )}
        </div>
        {selected !== null && (
          <DetailSection title={`Revision ${selected}`}>
            <MilestoneRevision
              binding={{
                workspace_id: milestone.workspace_id,
                id: milestone.id,
                key: milestone.key,
                title: milestone.title,
                revision: selected,
                origin: "preparation",
              }}
            />
          </DetailSection>
        )}
      </ContentStack>
    </Disclosure>
  );
}

export function PreparedMilestone({
  binding,
  update,
}: {
  binding?: Binding | null;
  update?: components["schemas"]["MilestoneUpdate"] | null;
}) {
  const [open, setOpen] = useState(false);
  if (!binding) return null;
  return (
    <Disclosure
      summary={`Milestone at preparation · ${binding.key}`}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <ContentStack space="content">
        {binding.origin === "upgrade" && (
          <p>
            These milestone details were retained when Flowfield was upgraded.
          </p>
        )}
        {binding.origin !== "upgrade" && (
          <p>
            The milestone description and acceptance criteria below were saved
            when this task was prepared. The task description defines this
            worker’s contribution.
          </p>
        )}
        {update && (
          <p>
            The milestone has changed to revision {update.latest_revision}. This
            task still uses revision {binding.revision}; ask the coordinator to
            review whether it needs updating.
          </p>
        )}
        <WorkspaceLink
          to={`/workspaces/${binding.workspace_id}/milestones/${binding.key}`}
        >
          View current milestone
        </WorkspaceLink>
        {open && <MilestoneRevision binding={binding} />}
      </ContentStack>
    </Disclosure>
  );
}
