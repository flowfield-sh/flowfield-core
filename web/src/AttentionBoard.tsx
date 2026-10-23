import { requestHref } from "./requestLinks";
import { taskHref } from "./navigation";
import { Badge } from "@/components/ui/badge";
import { LoadMore } from "./LoadMore";
import { ResourceNavigation } from "./WorkspaceTabs";
import type { DirtyCallback } from "./retainedState";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import { useCallback, useContext } from "react";
import { Button } from "@/components/ui/button";
import type { components } from "./api-schema";
import { usePage, useResource } from "./useResource";
import { QuestionDetail } from "./Inbox";
import { label, type Task, type WorkspaceBoard } from "./workspace";
import { WorkState } from "./WorkState";
import { PermissionCard } from "./AgentPermissions";
import { ContentStack } from "./DetailLayout";
import { DetailTabs } from "./Presentation";
import { WorkspaceLink } from "./WorkspaceLink";
import { TaskResponse } from "./TaskResponse";
import { useDirtyItems } from "./useDirtyItems";
import { ResourceRetry } from "./ResourceRetry";
import { PaneFooter } from "./EntityPane";
import { Delivered } from "./Delivered";
import { InboxRow } from "./InboxRow";

type Item = components["schemas"]["AttentionItem"];
type Page = components["schemas"]["AttentionPage"];
type Tab = "needs_me" | "history";
const columns = { needs_me: "action", history: "history" };
const identityOf = (item: Item) =>
  `${item.workspace_id}:${item.kind}:${item.id}`;
function itemHref(item: Item, workspaceId: string) {
  return item.request_href ?? requestHref(workspaceId, item.kind, item.id);
}

function TaskRequest({
  item,
  board,
  refresh,
  onDirty,
}: {
  item: Item;
  board: WorkspaceBoard;
  refresh: unknown;
  onDirty: DirtyCallback;
}) {
  const task = useResource<Task>(
    `workspaces/${item.workspace_id}/view/tasks/${item.task_key}`,
    refresh,
  );
  const mark = useDirtyItems(onDirty);
  // The shared controller retains its exact request and response binding.
  const response = useCallback(
    (value: boolean, retained?: boolean) => {
      mark("response", value, retained);
    },
    [mark],
  );
  return (
    <ContentStack>
      <ResourceRetry resources={[task]} />
      {task.data && (
        <TaskResponse
          projectId={item.workspace_id!}
          projects={board.projects}
          task={task.data}
          refresh={refresh}
          onDirty={response}
          requestTarget={{
            kind: item.kind as
              "question" | "result" | "intervention" | "review" | "integration",
            id: item.id,
          }}
        />
      )}
    </ContentStack>
  );
}

export function RequestContent({
  item,
  workspaceId,
  board,
  refresh,
  markDirty,
}: {
  item: Item;
  workspaceId: string;
  board: WorkspaceBoard;
  refresh: unknown;
  markDirty: (id: string, dirty: boolean, retained?: boolean) => void;
}) {
  const navigate = useNavigate();
  const resourceNavigate = useContext(ResourceNavigation);
  const id = identityOf(item);
  const dirty = useCallback(
    (value: boolean, retained?: boolean) => markDirty(id, value, retained),
    [id, markDirty],
  );
  const href = itemHref(item, workspaceId);
  return (
    <>
      {item.kind === "permission" ? (
        <PermissionCard
          workspaceId={workspaceId}
          id={item.id}
          refresh={refresh}
        />
      ) : item.kind === "question" && !item.task_key ? (
        <QuestionDetail
          projectId={item.workspace_id!}
          identity={item.id}
          tasks={board.tasks}
          refresh={refresh}
          onDirty={dirty}
          open={(path) =>
            resourceNavigate ? resourceNavigate(path) : void navigate(path)
          }
        />
      ) : [
          "question",
          "result",
          "review",
          "intervention",
          "integration",
        ].includes(item.kind) && item.task_key ? (
        <TaskRequest
          item={item}
          board={board}
          refresh={refresh}
          onDirty={dirty}
        />
      ) : (
        <>
          <strong>{item.title}</strong>
          {item.state && <WorkState state={item.state} />}
          <PaneFooter>
            <div className="task-input actions">
              <Button variant="outline" asChild>
                <WorkspaceLink to={href}>
                  {item.next_action ?? "Inspect request"}
                </WorkspaceLink>
              </Button>
            </div>
          </PaneFooter>
        </>
      )}
    </>
  );
}

export function requestTitle(item: Item) {
  return item.kind === "result"
    ? "Result"
    : item.kind === "permission"
      ? "Tool permission"
      : label(item.kind);
}
export function RequestSource({
  item,
  board,
}: {
  item: Item;
  board: WorkspaceBoard;
}) {
  const task = board.tasks.find((task) => task.key === item.task_key);
  const project = board.projects.find((p) => p.id === task?.project_id);
  return (
    <WorkspaceLink
      to={
        item.source_href ??
        (item.task_key
          ? taskHref(board.workspace.id, { key: item.task_key })
          : `/workspaces/${board.workspace.id}`)
      }
      aria-label={`${item.source_key ?? item.task_key ?? project?.name ?? "Workspace"}: ${item.title}`}
    >
      <Badge variant="outline">
        {item.task_key ?? project?.name ?? "Workspace"}
      </Badge>
    </WorkspaceLink>
  );
}

function InboxList({
  workspaceId,
  projectId,
  refresh,
  tab,
}: {
  workspaceId: string;
  projectId?: string;
  refresh: unknown;
  tab: Tab;
}) {
  const page = usePage<Page>(
    `workspaces/${workspaceId}/view/attention?column=${columns[tab]}${tab === "history" ? "&include_answered=true" : ""}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}`,
    refresh,
    tab === "needs_me",
  );
  return (
    <ContentStack
      space="section"
      role="tabpanel"
      id={`inbox-${tab}`}
      aria-label={label(tab)}
      className="inbox-list"
    >
      <ResourceRetry resources={[page]} />
      {page.loading && !page.data && <p>Loading requests…</p>}
      {page.data && !page.data.items.length && (
        <p className="collection-empty">
          {tab === "needs_me"
            ? "Nothing needs your attention."
            : "No past items yet."}
        </p>
      )}
      {!!page.data?.items.length && (
        <ol className="request-list inbox-history work-resource-list">
          {page.data.items.map((item) => (
            <InboxRow
              key={identityOf(item)}
              href={itemHref(item, workspaceId)}
              state={item.state ?? { label: label(item.status), tone: "idle" }}
              itemKey={item.source_key ?? item.task_key}
              title={item.title}
              date={item.updated_at}
            />
          ))}
        </ol>
      )}
      {page.data?.next_offset != null && (
        <LoadMore
          variant="outline"
          disabled={page.loadingMore || page.loading}
          onClick={() => void page.older()}
        >
          Load more
        </LoadMore>
      )}
    </ContentStack>
  );
}

export function AttentionBoard(props: {
  workspaceId: string;
  projectId?: string;
  refresh: unknown;
  board: WorkspaceBoard;
}) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const requested = params.get("inbox");
  const tab =
    requested === "history" || requested === "delivered"
      ? requested
      : "needs_me";
  return (
    <ContentStack className="inbox-view" space="section">
      <DetailTabs
        id="inbox"
        title="Inbox views"
        tabs={["needs_me", "delivered", "history"]}
        active={tab}
        change={(value) => {
          const next = new URLSearchParams(params);
          next.set("inbox", value);
          setParams(next, { state: location.state });
        }}
      />
      {tab === "delivered" ? (
        <div
          role="tabpanel"
          id="inbox-delivered"
          aria-labelledby="inbox-delivered-tab"
        >
          <Delivered
            workspaceId={props.workspaceId}
            projectId={props.projectId}
            refresh={props.refresh}
          />
        </div>
      ) : (
        <InboxList
          key={`${props.workspaceId}:${props.projectId}:${tab}`}
          {...props}
          tab={tab}
        />
      )}
    </ContentStack>
  );
}
