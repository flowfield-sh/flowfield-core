import { useLocation, useNavigate, useSearchParams } from "react-router";
import { useCallback, useState } from "react";
import { Button } from "@/components/ui/button";
import type { components } from "./api-schema";
import { usePage, useResource } from "./useResource";
import { AttentionCard } from "./AttentionCard";
import { taskHref } from "./navigation";
import { QuestionDetail, questionHref } from "./Inbox";
import { label, type Task, type WorkspaceBoard } from "./workspace";
import { WorkState } from "./WorkState";
import { PermissionCard } from "./AgentPermissions";
import { ContentStack } from "./DetailLayout";
import { DetailTabs } from "./Presentation";
import { WorkspaceLink } from "./WorkspaceLink";
import { TaskConversation } from "./TaskConversation";
import { MilestoneReviewRequest } from "./MilestoneVerification";
import { useDirtyItems } from "./useDirtyItems";
import { ResourceRetry } from "./ResourceRetry";
import { RequestFrame } from "./RequestFrame";
import { EntityOverlay } from "./EntityOverlay";

type Item = components["schemas"]["AttentionItem"];
type Page = components["schemas"]["AttentionPage"];
type Tab = "needs_me" | "history";
const columns = { needs_me: "action", history: "history" };
const identityOf = (item: Item) => `${item.project_id}:${item.kind}:${item.id}`;
function itemHref(item: Item) {
  return (
    item.state?.href ??
    (item.kind === "question"
      ? questionHref(item.project_id!, item.id)
      : item.kind === "result"
        ? `${taskHref(item.project_id!, { key: item.task_key! }, "result")}/${item.id}`
        : `${taskHref(item.project_id!, { key: item.task_key! }, "runs")}/${item.run_id ?? item.id}${item.kind === "integration" ? `/integrations/${item.id}` : ""}`)
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
  onDirty: (dirty: boolean) => void;
}) {
  const task = useResource<Task>(
    `projects/${item.project_id}/view/tasks/${item.task_key}`,
    refresh,
  );
  const mark = useDirtyItems(onDirty);
  // The shared controller retains its exact request and response binding.
  const response = useCallback(
    (value: boolean) => {
      mark("response", value);
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
          projectId={item.project_id!}
          task={task.data}
          board={board}
          refresh={refresh}
          onDirty={response}
          onSettingsDirty={settings}
          requestTarget={{
            kind: item.kind as
              "question" | "result" | "intervention" | "review",
            id: item.id,
          }}
        />
      )}
    </ContentStack>
  );
}

function RequestCard({
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
  markDirty: (id: string, dirty: boolean) => void;
}) {
  const navigate = useNavigate();
  const id = identityOf(item);
  const dirty = useCallback(
    (value: boolean) => markDirty(id, value),
    [id, markDirty],
  );
  const kind =
    item.kind === "milestone"
      ? "Milestone acceptance"
      : item.kind === "result"
        ? "Code review"
        : label(item.kind);
  const href = itemHref(item);
  const project = board.projects.find((p) => p.id === item.project_id);
  return (
    <RequestFrame
      kind={kind}
      label={`${kind}: ${item.title}`}
      source={
        <WorkspaceLink
          to={href}
          aria-label={`${item.task_key ?? (item.kind === "milestone" ? "Milestone" : "Question")}: ${item.title}`}
        >
          {item.kind === "milestone"
            ? (item.task_key ?? item.title)
            : item.task_key
              ? `${project?.name ?? item.project_id} / ${item.task_key}`
              : (project?.name ?? "Workspace")}
        </WorkspaceLink>
      }
    >
      {item.kind === "result" && (
        <h3 className="detail-title">Review {item.title}</h3>
      )}
      {item.kind === "milestone" ? (
        <MilestoneReviewRequest
          path={`workspaces/${workspaceId}`}
          id={item.id}
          refresh={refresh}
          onDirty={dirty}
        />
      ) : item.kind === "question" && !item.task_key ? (
        <QuestionDetail
          projectId={item.project_id!}
          identity={item.id}
          tasks={board.tasks}
          refresh={refresh}
          onDirty={dirty}
          open={(path) => void navigate(path)}
          inline
        />
      ) : ["question", "result", "review", "intervention"].includes(
          item.kind,
        ) && item.task_key ? (
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
          <Button variant="outline" asChild>
            <WorkspaceLink to={href}>
              {item.next_action ?? "Inspect request"}
            </WorkspaceLink>
          </Button>
        </>
      )}
    </RequestFrame>
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
  onDirty: (dirty: boolean) => void;
}) {
  const page = usePage<Page>(
    `workspaces/${workspaceId}/view/attention?column=${columns[tab]}${tab === "history" ? "&include_answered=true" : ""}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}`,
    refresh,
  );
  const markDirty = useDirtyItems(onDirty);
  const [seen, setSeen] = useState<Page | null>(null);
  const [retained, setRetained] = useState<Item[]>([]);
  // Do not remove a card under the person's cursor or discard a bound draft when
  // an answer resolves the request. A fresh visit gets a fresh action list.
  if (page.data && page.data !== seen) {
    setSeen(page.data);
    setRetained((previous) => {
      const incoming = new Map(
        page.data!.items.map((item) => [identityOf(item), item]),
      );
      return [
        ...previous.map((item) => incoming.get(identityOf(item)) ?? item),
        ...page.data!.items.filter(
          (item) =>
            !previous.some((old) => identityOf(old) === identityOf(item)),
        ),
      ];
    });
  }
  const items = tab === "needs_me" ? retained : (page.data?.items ?? []);
  return (
    <ContentStack
      space="section"
      role="tabpanel"
      aria-label={label(tab)}
      className="inbox-list"
    >
      <ResourceRetry resources={[page]} />
      {page.loading && !page.data && <p>Loading requests…</p>}
      {page.data && !items.length && (
        <p className="empty-column">
          {tab === "needs_me"
            ? "Nothing needs your attention."
            : "No past items yet."}
        </p>
      )}
      <ol className="request-list">
        {items.map((item) => (
          <li key={identityOf(item)}>
            {item.kind === "permission" ? (
              <PermissionCard
                projectId={item.project_id ?? undefined}
                workspaceId={item.project_id ? undefined : workspaceId}
                id={item.id}
                taskKey={item.task_key}
                refresh={refresh}
              />
            ) : tab === "needs_me" ? (
              <RequestCard
                item={item}
                workspaceId={workspaceId}
                board={board}
                refresh={refresh}
                markDirty={markDirty}
              />
            ) : (
              <AttentionCard
                href={itemHref(item)}
                kind={item.kind === "result" ? "Changes" : label(item.kind)}
                reference={item.task_key ?? "Workspace"}
                title={item.title}
                action={
                  item.state ? (
                    <WorkState state={item.state} />
                  ) : (
                    (item.next_action ?? label(item.status))
                  )
                }
              />
            )}
          </li>
        ))}
      </ol>
      {page.data?.next_offset != null && (
        <Button
          variant="outline"
          disabled={page.loadingMore || page.loading}
          onClick={() => void page.older()}
        >
          Load more
        </Button>
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
  onDirty: (dirty: boolean) => void;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const requested = params.get("inbox");
  const tab: Tab = requested === "history" ? requested : "needs_me";
  const permission = params.get("permission");
  return (
    <ContentStack className="inbox-view" space="section">
      {permission && (
        <EntityOverlay
          title="Tool permission"
          close={() => {
            const next = new URLSearchParams(params);
            next.delete("permission");
            void navigate(`${location.pathname}?${next}`, {
              replace: true,
              state: location.state,
            });
          }}
        >
          <PermissionCard
            projectId={props.projectId}
            workspaceId={props.projectId ? undefined : props.workspaceId}
            id={permission}
            taskKey={null}
            refresh={props.refresh}
          />
        </EntityOverlay>
      )}
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
