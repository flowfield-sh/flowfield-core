import type { components } from "./api-schema";
import { ContentStack } from "./DetailLayout";
import { OutputFiles } from "./Files";
import { LoadMore } from "./LoadMore";
import { Markdown } from "./Markdown";
import { ResourceRetry } from "./ResourceRetry";
import { Timestamp } from "./Timestamp";
import { WorkIdentity } from "./WorkIdentity";
import { WorkspaceLink } from "./WorkspaceLink";
import { followLink, taskHref, workspaceHref } from "./navigation";
import { usePage, useResource } from "./useResource";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

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
      {page.data?.items.map((item) => (
        <article key={item.id} className="overflow-hidden rounded-md border">
          <div className="flex flex-wrap items-center gap-2 bg-muted px-3 py-2">
            <WorkspaceLink
              className="min-w-0 flex-1"
              to={taskHref(workspaceId, { key: item.task_key })}
            >
              <WorkIdentity
                state={state}
                itemKey={item.task_key}
                title={item.title}
              />
            </WorkspaceLink>
            <span className="detail-metadata">
              <Timestamp date={item.delivered_at} />
            </span>
          </div>
          <ContentStack className="p-3">
            <Markdown>{item.summary}</Markdown>
            {item.completion === "code" && (
              <p className="detail-metadata">
                Integrated into {item.target_branch}
              </p>
            )}
            {!!item.files.length && <OutputFiles files={item.files} />}
            {item.file_count > item.files.length && (
              <WorkspaceLink to={taskHref(workspaceId, { key: item.task_key })}>
                View all {item.file_count} files
              </WorkspaceLink>
            )}
          </ContentStack>
        </article>
      ))}
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

export function DeliveredNavigation({
  workspaceId,
  refresh,
  current,
  open,
}: {
  workspaceId: string;
  refresh: unknown;
  current: string;
  open: (url: string) => void;
}) {
  const page = useResource<Page>(
    `workspaces/${workspaceId}/view/delivered?limit=5`,
    refresh,
  );
  const href = workspaceHref(workspaceId) + "?inbox=delivered";
  return (
    <section aria-label="Delivered">
      <div className="work-navigation-heading">
        <h3 className="work-group-label">Delivered</h3>
        <a href={href} onClick={(event) => followLink(event, () => open(href))}>
          View all
        </a>
      </div>
      <ResourceRetry resources={[page]} />
      {page.data?.items.map((item) => {
        const url = taskHref(workspaceId, { key: item.task_key });
        return (
          <Tooltip key={item.id}>
            <TooltipTrigger asChild>
              <WorkspaceLink
                to={url}
                className="work-navigation-row"
                aria-current={
                  current === url || current.startsWith(url + "/")
                    ? "page"
                    : undefined
                }
                aria-label={`${item.task_key} ${item.title}`}
                onClick={(event) => followLink(event, () => open(url))}
              >
                <WorkIdentity
                  state={state}
                  itemKey={item.task_key}
                  title={item.title}
                  decorative
                />
              </WorkspaceLink>
            </TooltipTrigger>
            <TooltipContent side="right">
              {item.task_key} {item.title} · Delivered{" "}
              <Timestamp date={item.delivered_at} tooltip={false} />
            </TooltipContent>
          </Tooltip>
        );
      })}
      {page.data && !page.data.items.length && (
        <p className="collection-empty">No delivered work yet</p>
      )}
    </section>
  );
}
