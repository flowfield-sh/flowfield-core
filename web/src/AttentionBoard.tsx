import { requestHref } from "./requestLinks";
import { taskHref } from "./navigation";
import { Badge } from "@/components/ui/badge";
import { LoadMore } from "./LoadMore";
import { useRetainedState } from "./retainedState";
import { ResourceNavigation } from "./WorkspaceTabs";
import type { DirtyCallback } from "./retainedState";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import {
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Button } from "@/components/ui/button";
import type { components } from "./api-schema";
import { usePage, useResource } from "./useResource";
import { QuestionDetail } from "./Inbox";
import { label, type Task, type WorkspaceBoard } from "./workspace";
import { WorkState } from "./WorkState";
import { WorkIdentity } from "./WorkIdentity";
import { PermissionCard } from "./AgentPermissions";
import { ContentStack, Disclosure } from "./DetailLayout";
import { DetailTabs } from "./Presentation";
import { WorkspaceLink } from "./WorkspaceLink";
import { TaskResponse } from "./TaskResponse";
import { useDirtyItems } from "./useDirtyItems";
import { ResourceRetry } from "./ResourceRetry";
import { RequestFrame } from "./RequestFrame";
import { Timestamp } from "./Timestamp";
import { PaneFooter } from "./EntityPane";
import { Delivered } from "./Delivered";

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
  refresh,
  onDirty,
}: {
  item: Item;
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

export function RequestCard({
  item,
  workspaceId,
  board,
  refresh,
  markDirty,
  standalone = false,
}: {
  item: Item;
  workspaceId: string;
  board: WorkspaceBoard;
  refresh: unknown;
  markDirty: (id: string, dirty: boolean, retained?: boolean) => void;
  standalone?: boolean;
}) {
  const navigate = useNavigate();
  const resourceNavigate = useContext(ResourceNavigation);
  const id = identityOf(item);
  const dirty = useCallback(
    (value: boolean, retained?: boolean) => markDirty(id, value, retained),
    [id, markDirty],
  );
  const kind = requestTitle(item);
  const href = itemHref(item, workspaceId);
  const content = (
    <>
      {item.kind === "result" && <h3 className="detail-title">{item.title}</h3>}
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
        <TaskRequest item={item} refresh={refresh} onDirty={dirty} />
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
  return standalone ? (
    content
  ) : (
    <RequestFrame
      kind={kind}
      state={item.state}
      label={`${kind}: ${item.title}`}
      href={href}
      source={<RequestSource item={item} board={board} />}
    >
      {content}
    </RequestFrame>
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
  board,
  refresh,
  tab,
  onDirty,
}: {
  workspaceId: string;
  projectId?: string;
  board: WorkspaceBoard;
  refresh: unknown;
  tab: Tab;
  onDirty: DirtyCallback;
}) {
  const page = usePage<Page>(
    `workspaces/${workspaceId}/view/attention?column=${columns[tab]}${tab === "history" ? "&include_answered=true" : ""}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}`,
    refresh,
    tab === "needs_me",
  );
  const mark = useDirtyItems(onDirty);
  const [drafts, setDrafts] = useRetainedState<Record<string, boolean>>(
    `inbox:${workspaceId}:${projectId}:drafts`,
    {},
  );
  const markDirty = useCallback(
    (id: string, dirty: boolean, retained?: boolean) => {
      mark(id, dirty, retained);
      // Cleanup clears mounted navigation guards, not the retained response.
      if (retained === undefined) return;
      setDrafts((previous) =>
        previous[id] === dirty ? previous : { ...previous, [id]: dirty },
      );
    },
    [mark, setDrafts],
  );
  const [seen, setSeen] = useState<Page | null>(null);
  const [retained, setRetained] = useRetainedState<Item[]>(
    `inbox:${workspaceId}:${projectId}:items`,
    [],
  );
  const [currentIds, setCurrentIds] = useRetainedState<string[]>(
    `inbox:${workspaceId}:${projectId}:current`,
    [],
  );
  // Keep independent drafts while canonical resolution
  // removes answered requests. Resolved drafts remain reachable in History.
  if (tab === "needs_me" && page.data && page.data !== seen) {
    setSeen(page.data);
    setCurrentIds(page.data.items.map(identityOf));
    setRetained((previous) => {
      const incoming = new Map(
        page.data!.items.map((item) => [identityOf(item), item]),
      );
      return [
        ...previous
          .filter(
            (item) =>
              incoming.has(identityOf(item)) || drafts[identityOf(item)],
          )
          .map((item) => incoming.get(identityOf(item)) ?? item),
        ...page.data!.items.filter(
          (item) =>
            !previous.some((old) => identityOf(old) === identityOf(item)),
        ),
      ];
    });
  }
  const items = useMemo(() => {
    const current = new Set(page.data?.items.map(identityOf));
    return (
      tab === "needs_me"
        ? retained.filter((item) => current.has(identityOf(item)))
        : [
            ...retained.filter(
              (item) =>
                drafts[identityOf(item)] &&
                !currentIds.includes(identityOf(item)) &&
                !current.has(identityOf(item)),
            ),
            ...(page.data?.items ?? []),
          ]
    ).sort(
      (a, b) =>
        b.updated_at.localeCompare(a.updated_at) ||
        identityOf(a).localeCompare(identityOf(b)),
    );
  }, [page.data, tab, retained, drafts, currentIds]);
  const list = useRef<HTMLDivElement>(null);
  const focused = useRef<{ id: string; index: number } | null>(null);
  useLayoutEffect(() => {
    const previous = focused.current;
    if (!previous || items.some((item) => identityOf(item) === previous.id))
      return;
    focused.current = null;
    // Removing the submitted card must not strand keyboard focus on the page
    // body or steal it from another request the person has already moved to.
    if (document.activeElement !== document.body) return;
    const remaining =
      list.current?.querySelectorAll<HTMLElement>(".request-list > li");
    const next = remaining?.[Math.min(previous.index, remaining.length - 1)];
    (
      next?.querySelector<HTMLElement>("header a, button, a") ?? list.current
    )?.focus({ preventScroll: true });
  }, [items]);
  return (
    <ContentStack
      ref={list}
      tabIndex={-1}
      space="section"
      role="tabpanel"
      id={`inbox-${tab}`}
      aria-label={label(tab)}
      className="inbox-list"
    >
      <ResourceRetry resources={[page]} />
      {page.loading && !page.data && <p>Loading requests…</p>}
      {page.data && !items.length && (
        <p className="collection-empty">
          {tab === "needs_me"
            ? "Nothing needs your attention."
            : "No past items yet."}
        </p>
      )}
      {tab === "needs_me" &&
        retained.some(
          (item) =>
            drafts[identityOf(item)] && !currentIds.includes(identityOf(item)),
        ) && (
          <p>
            Unsent responses to resolved requests are saved in{" "}
            <WorkspaceLink to="?inbox=history">History</WorkspaceLink>.
          </p>
        )}
      {items.length > 0 && (
        <ol
          className={
            tab === "needs_me"
              ? "request-list"
              : "request-list inbox-history work-resource-list"
          }
        >
          {items.map((item, index) => (
            <li
              key={identityOf(item)}
              onFocusCapture={() => {
                focused.current = { id: identityOf(item), index };
              }}
            >
              {tab === "needs_me" ? (
                <RequestCard
                  item={item}
                  workspaceId={workspaceId}
                  board={board}
                  refresh={refresh}
                  markDirty={markDirty}
                />
              ) : (
                <ContentStack>
                  <WorkspaceLink
                    className="work-navigation-row inbox-history-row"
                    to={item.request_href ?? itemHref(item, workspaceId)}
                  >
                    <WorkIdentity
                      state={
                        item.state ?? {
                          label: label(item.status),
                          tone: "idle",
                        }
                      }
                      itemKey={item.source_key ?? item.task_key}
                      title={item.title}
                    />
                    <span className="inbox-history-metadata">
                      <span className="inbox-history-outcome">
                        {item.next_action ??
                          item.state?.label ??
                          label(item.status)}
                      </span>
                      <span className="detail-metadata">
                        <Timestamp date={item.updated_at} />
                      </span>
                    </span>
                  </WorkspaceLink>
                  {drafts[identityOf(item)] && (
                    <Disclosure summary="Unsent response" open>
                      <RequestCard
                        item={item}
                        workspaceId={workspaceId}
                        board={board}
                        refresh={refresh}
                        markDirty={markDirty}
                      />
                    </Disclosure>
                  )}
                </ContentStack>
              )}
            </li>
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
  onDirty: DirtyCallback;
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
