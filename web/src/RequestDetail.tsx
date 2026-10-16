import { useLocation } from "react-router";
import type { AdjacentSnapshot } from "./AdjacentView";
import { useCallback, useContext, useEffect } from "react";
import { RequestCard, RequestSource, requestTitle } from "./AttentionBoard";
import { ContentStack } from "./DetailLayout";
import { DetailHeader } from "./Presentation";
import { ResourceTitles } from "./WorkspaceTabs";
import { ResourceRetry } from "./ResourceRetry";
import { EntityPane } from "./EntityPane";
import { WorkState } from "./WorkState";
import { useResource } from "./useResource";
import type { DirtyCallback } from "./retainedState";
import type { WorkspaceBoard } from "./workspace";
import type { components } from "./api-schema";

export function RequestDetail({
  workspaceId,
  kind,
  identity,
  projectId,
  board,
  refresh,
  onDirty,
  close,
  publishView,
}: {
  workspaceId: string;
  kind: string;
  identity: string;
  projectId?: string;
  board: WorkspaceBoard;
  refresh: unknown;
  onDirty: DirtyCallback;
  close: () => void;
  publishView: (value: AdjacentSnapshot) => void;
}) {
  const location = useLocation();
  const resource = useResource<components["schemas"]["AttentionItem"]>(
    `workspaces/${workspaceId}/requests/${kind}/${identity}${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
    refresh,
  );
  const item = resource.data;
  useEffect(() => {
    publishView({
      workspaceId,
      pathname: location.pathname,
      context:
        item && !resource.error
          ? {
              view: {
                surface: "request",
                without_milestone: false,
                project_id: item.project_id,
                request_kind: item.request_kind,
                request_id: item.id,
              },
              task: null,
            }
          : null,
    });
  }, [workspaceId, location.pathname, item, resource.error, publishView]);
  const title = item ? requestTitle(item) : "Request";
  const setTitle = useContext(ResourceTitles);
  useEffect(() => {
    if (item?.request_href)
      setTitle?.(
        item.request_href,
        `${item.task_key ? item.task_key + " " : ""}${title}`,
      );
  }, [item, title, setTitle]);
  const mark = useCallback(
    (_id: string, dirty: boolean, retained?: boolean) =>
      onDirty(dirty, retained),
    [onDirty],
  );
  return (
    <EntityPane
      label="Request"
      identity={`${projectId}:${kind}:${identity}`}
      returnFocusHref={item?.request_href ?? ""}
      close={close}
    >
      <section className="editor request-detail">
        <DetailHeader
          title={
            <span className="feed-event-title">
              <WorkState
                state={item?.state ?? { label: title, tone: "idle" }}
                indicatorOnly
              />
              {item && <RequestSource item={item} board={board} />}
              <span>{title}</span>
            </span>
          }
          close={close}
        />
        <ContentStack space="section">
          <ResourceRetry resources={[resource]} />
          {resource.loading && !item && <p>Loading request…</p>}
          {resource.error && !item && <p>{resource.error}</p>}
          {item && (
            <RequestCard
              item={item}
              workspaceId={workspaceId}
              board={board}
              refresh={refresh}
              markDirty={mark}
              standalone
            />
          )}
        </ContentStack>
      </section>
    </EntityPane>
  );
}
