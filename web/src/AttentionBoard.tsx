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
import { taskHref } from "./navigation";
import { QuestionDetail, questionHref } from "./Inbox";
import { label, type Task, type WorkspaceBoard } from "./workspace";
import { WorkState } from "./WorkState";
import { WorkIdentity } from "./WorkIdentity";
import { PermissionCard } from "./AgentPermissions";
import { ContentStack, Disclosure } from "./DetailLayout";
import { DetailHeader, DetailTabs } from "./Presentation";
import { WorkspaceLink } from "./WorkspaceLink";
import { TaskConversation } from "./TaskConversation";
import { useDirtyItems } from "./useDirtyItems";
import { ResourceRetry } from "./ResourceRetry";
import { RequestFrame } from "./RequestFrame";
import { Timestamp } from "./Timestamp";
import { EntityPane, PaneFooter } from "./EntityPane";

type Item = components["schemas"]["AttentionItem"];
type Page = components["schemas"]["AttentionPage"];
type Tab = "needs_me" | "history";
const columns = { needs_me: "action", history: "history" };
const identityOf = (item: Item) =>
  `${item.workspace_id}:${item.kind}:${item.id}`;
function itemHref(item: Item, workspaceId: string) {
  if (item.kind === "permission")
    return (
      item.state?.href ??
      (item.workspace_id
        ? `/workspaces/${item.workspace_id}/permissions/${item.id}`
        : `/workspaces/${workspaceId}/permissions/${item.id}`)
    );
  return (
    item.state?.href ??
    (item.kind === "question"
      ? questionHref(item.workspace_id!, item.id)
      : item.kind === "result"
        ? `${taskHref(item.workspace_id!, { key: item.task_key! }, "changes")}/${item.id}`
        : `${taskHref(item.workspace_id!, { key: item.task_key! }, "history")}/${item.kind === "integration" ? `validation:${item.id}` : `worker:${item.run_id ?? item.id}`}`)
  );
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
  const settings = useCallback(
    (value: boolean) => {
      mark("settings", value);
    },
    [mark],
  );
  return (
    <ContentStack>
      <ResourceRetry resources={[task]} />
      {task.data && (
        <TaskConversation
          projectId={item.workspace_id!}
          task={task.data}
          board={board}
          refresh={refresh}
          onDirty={response}
          onSettingsDirty={settings}
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
          taskKey={item.task_key}
          refresh={refresh}
          inline
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
          inline
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
  return standalone ? (
    content
  ) : (
    <RequestFrame
      kind={kind}
      state={item.state}
      label={`${kind}: ${item.title}`}
      href={item.request_href ?? undefined}
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
      to={item.source_href ?? itemHref(item, board.workspace.id)}
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
  identity?: string;
  refresh: unknown;
  board: WorkspaceBoard;
  onDirty: DirtyCallback;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const requested = params.get("inbox");
  const tab: Tab = requested === "history" ? requested : "needs_me";
  const permission = params.get("permission");
  const closePermission = () => {
    const next = new URLSearchParams(params);
    next.delete("permission");
    next.delete("permission_project");
    void navigate(`${location.pathname}?${next}`, {
      replace: true,
      state: location.state,
    });
  };
  if (permission)
    return (
      <EntityPane
        label="Tool permission"
        identity={permission}
        returnFocusHref={location.pathname}
        close={closePermission}
      >
        <DetailHeader title="Tool permission" close={closePermission} />
        <PermissionCard
          workspaceId={props.workspaceId}
          id={permission}
          taskKey={null}
          refresh={props.refresh}
        />
      </EntityPane>
    );
  return (
    <ContentStack className="inbox-view" space="section">
      <DetailTabs
        id="inbox"
        title="Inbox views"
        tabs={["needs_me", "history"]}
        active={tab}
        change={(value) => {
          const next = new URLSearchParams(params);
          next.set("inbox", value);
          setParams(next, { state: location.state });
        }}
      />
      <InboxList
        key={`${props.workspaceId}:${props.projectId}:${tab}`}
        {...props}
        tab={tab}
      />
    </ContentStack>
  );
}
