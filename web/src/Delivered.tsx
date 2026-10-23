import type { components } from "./api-schema";
import { ContentStack } from "./DetailLayout";
import { LoadMore } from "./LoadMore";
import { ResourceRetry } from "./ResourceRetry";
import { resultHref } from "./navigation";
import { usePage } from "./useResource";
import { InboxRow } from "./InboxRow";

type Page = components["schemas"]["DeliveredPage"];
const state = { label: "Delivered", tone: "complete" as const };

export function Delivered({
  workspaceId,
  projectId,
  refresh,
}: {
  workspaceId: string;
  projectId?: string;
  refresh: unknown;
}) {
  const page = usePage<Page>(
    `workspaces/${workspaceId}/view/delivered${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
    refresh,
  );
  return (
    <ContentStack space="section" role="region" aria-label="Delivered tasks">
      <ResourceRetry resources={[page]} />
      {page.loading && !page.data && <p>Loading delivered work…</p>}
      {page.data && !page.data.items.length && (
        <p className="collection-empty">No delivered work yet</p>
      )}
      {!!page.data?.items.length && (
        <ol className="request-list inbox-history work-resource-list">
          {page.data.items.map((item) => (
            <InboxRow
              key={item.id}
              href={resultHref(workspaceId, item.id)}
              state={state}
              itemKey={item.task_key}
              title={item.title}
              outcome="Delivered"
              date={item.delivered_at}
            />
          ))}
        </ol>
      )}
      {page.data?.next_cursor && (
        <LoadMore
          variant="outline"
          size="sm"
          disabled={page.loading || page.loadingMore}
          onClick={() => void page.older()}
        >
          Earlier deliveries
        </LoadMore>
      )}
    </ContentStack>
  );
}
