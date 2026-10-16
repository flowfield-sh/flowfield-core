import { ResourceRetry } from "./ResourceRetry";
import type { components } from "./api-schema";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ContentStack } from "./DetailLayout";
import { WorkspaceLink } from "./WorkspaceLink";
import { Timestamp } from "./Timestamp";
import { useResource } from "./useResource";
import { authorLabel, type Milestone } from "./workspace";
type HistoryItem =
  | components["schemas"]["DocumentHeader"]
  | Pick<Milestone, "revision" | "updated_by" | "updated_at">;
type HistoryPage = {
  items: HistoryItem[];
  next_cursor: string | number | null;
};

export function RevisionHistory({
  path,
  href,
  revision,
  selected,
  kind = "Document",
}: {
  path: string;
  href: string;
  revision: number;
  selected: number;
  kind?: "Document" | "Milestone";
}) {
  const [before, setBefore] = useState<number | null>(null);
  const resource = useResource<HistoryPage>(
    `${path}/history?limit=20${before ? `&before=${before}` : ""}`,
    revision,
  );
  return (
    <aside className="document-history" aria-label="Revision history">
      <ContentStack>
        <h3 className="detail-title">Revisions</h3>
        <nav
          className="project-list document-revisions"
          aria-label={`${kind} revisions`}
        >
          <ResourceRetry resources={[resource]}>Retry history</ResourceRetry>
          {!resource.data && !resource.error && <p>Loading history…</p>}
          {resource.data?.items.map((item) => (
            <WorkspaceLink
              className="project-list-row document-revision"
              key={item.revision}
              aria-label={`Revision ${item.revision}`}
              aria-current={item.revision === selected ? "page" : undefined}
              to={`${href}/revisions/${item.revision}`}
            >
              <ContentStack space="tight" className="col-span-2 gap-0.5">
                <span>
                  Revision {item.revision}
                  {item.revision === revision ? " · Current" : ""}
                </span>
                <span className="detail-metadata">
                  {"actor" in item
                    ? item.actor.label
                    : authorLabel(item.updated_by)}{" "}
                  ·{" "}
                  <Timestamp
                    date={
                      "created_at" in item ? item.created_at : item.updated_at
                    }
                  />
                </span>
              </ContentStack>
            </WorkspaceLink>
          ))}
        </nav>
        <div className="pagination-controls actions">
          {before && (
            <Button
              size="sm"
              variant="outline"
              disabled={resource.loading}
              onClick={() => setBefore(null)}
            >
              Latest revisions
            </Button>
          )}
          {resource.data?.next_cursor && (
            <Button
              size="sm"
              variant="outline"
              disabled={resource.loading}
              onClick={() => setBefore(Number(resource.data!.next_cursor))}
            >
              Older revisions
            </Button>
          )}
        </div>
      </ContentStack>
    </aside>
  );
}
