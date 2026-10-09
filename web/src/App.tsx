import { MilestoneBadge } from "./MilestoneBadge";
import { useBrowserNotifications } from "./BrowserNotices";
import { TaskNeeds, TaskState, hasTaskNeeds } from "./TaskNeeds";
import { SetupInstructions } from "./SetupInstructions";
import { taskTab } from "./navigation";
import { NotificationButton, useNotifications } from "./Notifications";
import { CoordinatorChat, type ChatDrafts } from "./CoordinatorChat";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { WorkspaceFrame } from "./WorkspaceFrame";
import { useTheme } from "./AppearanceSettings";
import { FlowfieldSettings, SettingsLink } from "./FlowfieldSettings";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ContentStack } from "./DetailLayout";
import { Card } from "@/components/ui/card";
import {
  Settings,
  Columns3,
  Inbox,
  Flag,
  Archive,
  CircleAlert,
} from "lucide-react";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Button } from "@/components/ui/button";
import { EntityOverlay, EntityPane } from "./EntityOverlay";
import { DetailHeader } from "./Presentation";
import { useResource } from "./useResource";
import { NewTask } from "./NewTask";
import { TaskDetail } from "./TaskDetail";
import { TaskPriority } from "./TaskPriority";
import { ProjectEditor } from "./ProjectEditor";
import { QueueControls } from "./Workers";
import { AttentionBoard } from "./AttentionBoard";
import { MilestoneEditor } from "./MilestoneEditor";
import { MilestoneDetail, MilestoneList } from "./Milestones";
import {
  Attribution,
  CollectionLayout,
  CollectionRow,
  TaskIdentity,
  TaskTypeBadge,
  DraftBadge,
} from "./Presentation";
import { useEffect, useRef, useState } from "react";
import {
  Board,
  compareTasks,
  isUpcoming,
  columns,
  Milestone,
  label,
  Project,
  request,
  Task,
  TaskCard,
  WorkStatus,
} from "./workspace";
import { QuestionOverlay } from "./Inbox";
import { DiscardChangesDialog } from "./DiscardChangesDialog";
import {
  followLink,
  entityPage,
  projectHref,
  taskHref,
  useWorkspaceNavigation,
} from "./navigation";

type Selection = { kind: "task" | "milestone" | "project"; id?: string };
type WorkRecord = Project | Milestone | Task;

export function App() {
  const appearance = useTheme();
  const [chatDrafts] = useState<ChatDrafts>(() => new Map());
  const [chatSettingsDirty, setChatSettingsDirty] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [projectRefresh, setProjectRefresh] = useState<Record<string, number>>(
    {},
  );
  const [unsaved, setUnsaved] = useState(false);
  const [overlayDirty, setOverlayDirty] = useState(false);
  const {
    params,
    pathname,
    changeLocation,
    notFound,
    closeQuestion,
    closeEntity,
    backgroundPath,
    discardChanges,
  } = useWorkspaceNavigation(
    unsaved,
    setUnsaved,
    overlayDirty,
    setOverlayDirty,
    chatSettingsDirty,
  );
  useBrowserNotifications(changeLocation);
  const projectId = params.projectId;
  const { data: attentionCounts } = useResource<Record<string, number>>(
    "projects/attention-counts",
    `${refresh}:${JSON.stringify(projectRefresh)}`,
  );
  const connectionStatus = connected
    ? live
      ? "Connected"
      : navigator.onLine
        ? "Reconnecting…"
        : "Disconnected"
    : loading
      ? "Connecting…"
      : "Disconnected";
  useEffect(() => {
    let active = true;
    request<Project[]>("projects")
      .then((items) => {
        if (active) {
          setProjects(items);
          setConnected(true);
          setError("");
        }
      })
      .catch((e: Error) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refresh]);
  useEffect(() => {
    if (!connected) return;
    let events: EventSource;
    function connect() {
      events?.close();
      events = new EventSource("/api/events");
      events.onopen = () => setLive(true);
      events.onerror = () => setLive(false);
      events.addEventListener("change", (event) => {
        const change = JSON.parse(event.data) as { projects: string[] | null };
        window.dispatchEvent(
          new CustomEvent("flowfield:activity", { detail: change }),
        );
        if (change.projects === null) setRefresh((v) => v + 1);
        else
          setProjectRefresh((previous) => {
            const next = { ...previous };
            for (const id of change.projects!) next[id] = (next[id] ?? 0) + 1;
            return next;
          });
      });
      events.addEventListener("activity", (event) =>
        window.dispatchEvent(
          new CustomEvent("flowfield:activity", {
            detail: JSON.parse(event.data),
          }),
        ),
      );
    }
    function offline() {
      events?.close();
      setLive(false);
    }
    if (navigator.onLine) connect();
    window.addEventListener("online", connect);
    window.addEventListener("offline", offline);
    return () => {
      events?.close();
      window.removeEventListener("online", connect);
      window.removeEventListener("offline", offline);
    };
  }, [connected]);
  useEffect(() => {
    function guard(e: BeforeUnloadEvent) {
      if (
        unsaved ||
        overlayDirty ||
        chatSettingsDirty ||
        [...chatDrafts.values()].some((value) => value.text)
      )
        e.preventDefault();
    }
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [unsaved, overlayDirty, chatSettingsDirty, chatDrafts]);
  function navigate(action: () => void) {
    action();
  }
  return (
    <>
      <WorkspaceFrame
        workLocation={pathname}
        projects={projects.map((project) => ({
          id: project.id,
          name: project.name,
          href: projectHref(project.id),
          needsYou: attentionCounts?.[project.id] ?? 0,
        }))}
        activeProjectId={projectId ?? ""}
        onProjectSelect={(project) => changeLocation(project.href)}
        onHome={() => changeLocation("/")}
        onAddProject={() => changeLocation("/new-project")}
        footer={
          <>
            <SettingsLink active={pathname.startsWith("/settings/")} />
            <NotificationButton />
            <Tooltip>
              <TooltipTrigger asChild>
                <p
                  className="connection"
                  role="status"
                  tabIndex={0}
                  aria-label={connectionStatus}
                >
                  <span className={live ? "live-dot" : "live-dot offline"} />
                  <span data-sidebar="label">{connectionStatus}</span>
                </p>
              </TooltipTrigger>
              <TooltipContent side="right">{connectionStatus}</TooltipContent>
            </Tooltip>
          </>
        }
        coordinator={
          connected && projectId && !notFound
            ? (visible) => (
                <CoordinatorChat
                  key={projectId}
                  projectId={projectId}
                  refresh={`${refresh}:${projectRefresh[projectId] ?? 0}`}
                  drafts={chatDrafts}
                  onSettingsDirty={setChatSettingsDirty}
                  taskKey={params.taskKey}
                  selectedResultId={
                    params.tab === "result" || params.tab === "changes"
                      ? params.runId
                      : params.tab === "conversation" &&
                          params.runId?.startsWith("result:")
                        ? params.runId.slice(7)
                        : undefined
                  }
                  controlsActive={
                    visible &&
                    (!entityPage(pathname) || !!params.taskKey) &&
                    !params.overlayQuestionId
                  }
                />
              )
            : undefined
        }
        work={
          <>
            {error && (
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertTitle>Could not refresh the workspace</AlertTitle>
                <AlertDescription>
                  <p>{error}</p>
                </AlertDescription>
              </Alert>
            )}
            {!connected ? (
              <section className="welcome">
                <h1>
                  {loading ? "Opening your workspace" : "Service unavailable"}
                </h1>
                {!loading && (
                  <>
                    <p>Check that Flowfield is running, then try again.</p>
                    <Button
                      size="sm"
                      onClick={() => {
                        setLoading(true);
                        setRefresh((v) => v + 1);
                      }}
                    >
                      Retry connection
                    </Button>
                  </>
                )}
              </section>
            ) : notFound ? (
              <section className="welcome">
                <h1>Page not found</h1>
                <p>Choose a project to return to your workspace.</p>
              </section>
            ) : pathname.startsWith("/settings/") ? (
              <FlowfieldSettings
                onDirty={setUnsaved}
                appearance={appearance}
                tab={
                  pathname === "/settings/appearance"
                    ? "appearance"
                    : "harnesses"
                }
              />
            ) : projectId ? (
              <ProjectBoard
                key={projectId}
                projectId={projectId}
                refresh={`${refresh}:${projectRefresh[projectId] ?? 0}`}
                params={params}
                pathname={pathname}
                backgroundPath={backgroundPath}
                closeEntity={closeEntity}
                changeLocation={changeLocation}
                navigate={navigate}
                setUnsaved={setUnsaved}
                setOverlayDirty={setOverlayDirty}
                closeQuestion={closeQuestion}
              />
            ) : (
              <SetupInstructions
                added={(project) => {
                  setProjects((items) => [
                    ...items.filter((item) => item.id !== project.id),
                    project,
                  ]);
                  setRefresh((value) => value + 1);
                  changeLocation(projectHref(project.id));
                }}
              />
            )}
          </>
        }
      />
      <DiscardChangesDialog {...discardChanges} />
    </>
  );
}

function ProjectBoard({
  projectId,
  refresh,
  params,
  pathname,
  changeLocation,
  navigate,
  setUnsaved,
  setOverlayDirty,
  closeQuestion,
  closeEntity,
  backgroundPath,
}: {
  projectId: string;
  refresh: string;
  params: Record<string, string | undefined>;
  pathname: string;
  changeLocation: (url: string, replace?: boolean) => void;
  navigate: (action: () => void) => void;
  setUnsaved: (v: boolean) => void;
  setOverlayDirty: (v: boolean) => void;
  closeQuestion: () => void;
  closeEntity: (fallback: string) => void;
  backgroundPath?: string;
}) {
  const { notify } = useNotifications();
  const [retry, setRetry] = useState(0);
  const path = `projects/${encodeURIComponent(projectId)}`;
  const {
    data: board,
    setData: setBoard,
    error,
    invalidate,
  } = useResource<Board>(`${path}/view/board`, `${refresh}:${retry}`);
  const questionSelected = params.questionId !== undefined;
  const taskRef = params.taskKey;
  const {
    data: taskDetail,
    setData: setTaskDetail,
    error: detailError,
    loading: detailLoading,
    invalidate: invalidateDetail,
  } = useResource<Task>(
    taskRef ? `${path}/view/tasks/${encodeURIComponent(taskRef)}` : null,
    `${refresh}:${retry}`,
  );
  const selectedTask = board?.tasks.find(
    (t) => t.key === taskRef || t.id === taskRef,
  );
  const newKind = pathname.includes("/tasks/new")
    ? "task"
    : pathname.endsWith("/milestones/new")
      ? "milestone"
      : null;
  const selection: Selection | null = questionSelected
    ? null
    : taskRef
      ? { kind: "task", id: selectedTask?.id ?? taskRef }
      : params.milestoneId
        ? { kind: "milestone", id: params.milestoneId! }
        : pathname === `${projectHref(projectId)}/edit` ||
            pathname.startsWith(`${projectHref(projectId)}/edit/`)
          ? { kind: "project", id: projectId }
          : newKind === "task" || newKind === "milestone"
            ? { kind: newKind }
            : null;
  const background = backgroundPath ?? pathname;
  const inView = (view: string) =>
    background === `${projectHref(projectId)}/${view}` ||
    background.startsWith(`${projectHref(projectId)}/${view}/`);
  const milestonesView = inView("milestones");
  const showInbox = inView("inbox");
  const [filter, setFilter] = useState("");
  const archive =
    inView("archive") || (!backgroundPath && !!selectedTask?.archived);
  const [dragged, setDragged] = useState<TaskCard | null>(null);
  const [dropTarget, setDropTarget] = useState<{
    status: WorkStatus;
    before: string | null;
  } | null>(null);
  const [prioritizing, setPrioritizing] = useState(false);
  const priorityPending = useRef(false);
  async function prioritize(
    task: TaskCard,
    status: WorkStatus,
    before: string | null = null,
  ) {
    if (priorityPending.current || !isUpcoming(status)) return;
    priorityPending.current = true;
    setPrioritizing(true);
    try {
      await request<Task>(
        `${path}/view/tasks/${encodeURIComponent(task.id)}/prioritize`,
        "POST",
        {
          expected_revision: task.revision,
          status,
          before_id: before,
          author: "human",
        },
      );
    } catch (e) {
      notify({
        key: `priority:${projectId}`,
        project_id: projectId,
        title: "Priority could not change",
        message: (e as Error).message,
      });
    } finally {
      priorityPending.current = false;
      setPrioritizing(false);
      setRetry((v) => v + 1);
    }
  }
  function selectionUrl(next: Selection | null, archived = archive) {
    const base = projectHref(projectId);
    if (!next)
      return (
        base + (milestonesView ? "/milestones" : archived ? "/archive" : "")
      );
    if (next.kind === "project") return base + "/edit";
    if (!next.id) return base + "/" + next.kind + "s/new";
    if (next.kind === "task") {
      const task = board?.tasks.find(
        (t) => t.id === next.id || t.key === next.id,
      );
      return taskHref(projectId, { key: task?.key ?? next.id });
    }
    const milestone = board?.milestones.find(
      (m) => m.id === next.id || m.key === next.id,
    );
    return (
      base + "/milestones/" + encodeURIComponent(milestone?.key ?? next.id)
    );
  }
  function choose(next: Selection | null) {
    if (!next) {
      closeEntity(selectionUrl(null));
      return;
    }
    navigate(() => {
      changeLocation(selectionUrl(next));
    });
  }
  function saved(kind: Selection["kind"], item: WorkRecord) {
    // Invalidate older in-flight snapshots before applying a completed mutation.
    invalidate();
    invalidateDetail();
    if (kind === "task") setTaskDetail(item as Task);
    setBoard((current) => {
      if (!current) return current;
      if (kind === "project") return { ...current, project: item as Project };
      const key = kind === "task" ? "tasks" : "milestones";
      return {
        ...current,
        [key]: [...current[key].filter((v) => v.id !== item.id), item],
      } as Board;
    });
    setUnsaved(false);
    const previous =
      kind === "task" ? board?.tasks.find((t) => t.id === item.id) : null;
    const archiveChanged =
      previous && previous.archived !== (item as Task).archived;
    changeLocation(
      archiveChanged
        ? selectionUrl(null)
        : kind === "task"
          ? taskHref(projectId, item as Task, taskTab(params.tab))
          : kind === "milestone"
            ? `${projectHref(projectId)}/milestones/${(item as Milestone).key}`
            : selectionUrl({ kind, id: item.id }),
      true,
    );
    setRetry((v) => v + 1);
  }
  if (!board)
    return (
      <section className="welcome content-stack" data-space="section">
        <h1>{error ? "Could not open project" : "Loading board…"}</h1>
        {error && (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertTitle>Board unavailable</AlertTitle>
            <AlertDescription>
              <ContentStack>
                <p>{error}</p>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setRetry((v) => v + 1)}
                >
                  Retry board
                </Button>
              </ContentStack>
            </AlertDescription>
          </Alert>
        )}
      </section>
    );
  const visible = board.tasks.filter(
    (t) =>
      t.archived === archive &&
      (!filter ||
        (filter === "none" ? !t.milestone_id : t.milestone_id === filter)),
  );
  const activeTasks = board.tasks.filter((t) => !t.archived);
  const incoming =
    selection?.kind === "project"
      ? board.project
      : selection?.kind === "task"
        ? (taskDetail ?? undefined)
        : board.milestones.find(
            (e) => e.id === selection?.id || e.key === selection?.id,
          );
  const view = showInbox
    ? "inbox"
    : milestonesView
      ? "milestones"
      : archive
        ? "archive"
        : "board";
  function openView(value: string) {
    navigate(() =>
      changeLocation(
        projectHref(projectId) + (value === "board" ? "" : "/" + value),
      ),
    );
  }
  function viewTrigger(value: string) {
    return {
      value,
      // Radix does not emit a value change for the already selected tab.
      onClick: () => {
        if (taskRef && value === view) openView(value);
      },
    };
  }
  const editorPanel = (
    <>
      {selection?.id &&
        !incoming &&
        (selection.kind === "task" && detailLoading && !detailError ? (
          <section className="editor">Loading task…</section>
        ) : (
          <section className="editor" role="alert">
            <DetailHeader
              title={`${label(selection.kind)} not found`}
              close={() => choose(null)}
            />
            {detailError && <p>{detailError}</p>}
            <p>
              This reference does not identify a {selection.kind} in this
              project.
            </p>
          </section>
        ))}
      {selection &&
        (!selection.id || incoming) &&
        (selection.kind === "project" && incoming ? (
          <ProjectEditor
            key={selection.id}
            incoming={incoming as Project}
            path={path}
            hasTasks={board.tasks.length > 0}
            onDirty={setUnsaved}
            saved={(item) => saved("project", item)}
            close={() => choose(null)}
          />
        ) : selection.kind === "milestone" ? (
          <MilestoneEditor
            key={selection.id ?? "new"}
            incoming={incoming as Milestone | undefined}
            path={path}
            onDirty={setUnsaved}
            saved={(item) => saved("milestone", item)}
            close={() => choose(null)}
            viewTasks={
              incoming && (
                <Button
                  size="sm"
                  variant="outline"
                  type="button"
                  onClick={() => {
                    setFilter(incoming.id);
                    changeLocation(projectHref(projectId));
                  }}
                >
                  View tasks
                </Button>
              )
            }
          />
        ) : incoming ? (
          <TaskDetail
            key={selection.id}
            task={incoming as Task}
            path={path}
            board={board}
            setUnsaved={setUnsaved}
            saved={(item) => saved("task", item)}
            close={() => choose(null)}
            openTask={(id) => choose({ kind: "task", id })}
            priorityControls={
              <TaskPriority
                task={incoming as Task}
                busy={prioritizing}
                prioritize={prioritize}
              />
            }
          />
        ) : (
          <NewTask
            key={selection.id ?? "new"}
            path={path}
            board={board}
            setUnsaved={setUnsaved}
            saved={(item) => saved("task", item)}
            close={() => choose(null)}
          />
        ))}
    </>
  );
  return (
    <>
      <header className="workspace-pane-header">
        <h1>{board.project.name}</h1>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Project details"
          onClick={() => choose({ kind: "project", id: projectId })}
        >
          <Settings />
        </Button>
      </header>
      <Tabs
        className="workspace-project-view"
        activationMode="manual"
        value={view}
        onValueChange={openView}
      >
        <div className="workspace-project-tabs">
          <TabsList variant="line" aria-label="Project views">
            <TabsTrigger {...viewTrigger("board")}>
              <Columns3 /> Board
              {activeTasks.length > 0 && (
                <span className="nav-count">{activeTasks.length}</span>
              )}
            </TabsTrigger>
            <TabsTrigger {...viewTrigger("inbox")}>
              <Inbox /> Needs you
              {board.needs_you_count > 0 && (
                <span className="nav-count needs-attention">
                  {board.needs_you_count}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger {...viewTrigger("milestones")}>
              <Flag /> Milestones
              {board.milestones.length > 0 && (
                <span className="nav-count">{board.milestones.length}</span>
              )}
            </TabsTrigger>
            <TabsTrigger {...viewTrigger("archive")}>
              <Archive /> Archive
              {board.tasks.length > activeTasks.length && (
                <span className="nav-count">
                  {board.tasks.length - activeTasks.length}
                </span>
              )}
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent
          value={view}
          className={`workspace-work-content ${!milestonesView && !archive ? "workspace-task-board" : ""}`}
          hidden={!!taskRef}
        >
          <div className="board-toolbar">
            {milestonesView && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => choose({ kind: "milestone" })}
              >
                New milestone
              </Button>
            )}
            {!showInbox && !milestonesView && (
              <div className="actions">
                <Label className="field filter-label">
                  <span className="sr-only">Filter by milestone</span>
                  <NativeSelect
                    size="sm"
                    aria-label="Filter by milestone"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  >
                    <option value="">All work</option>
                    <option value="none">Without a milestone</option>
                    {board.milestones.map((milestone) => (
                      <option key={milestone.id} value={milestone.id}>
                        {milestone.key} · {milestone.title}
                      </option>
                    ))}
                  </NativeSelect>
                </Label>
                <Button
                  size="sm"
                  variant="outline"
                  className="quiet"
                  onClick={() =>
                    choose(
                      filter && filter !== "none"
                        ? { kind: "milestone", id: filter }
                        : { kind: "milestone" },
                    )
                  }
                >
                  {filter && filter !== "none"
                    ? "Edit milestone"
                    : "New milestone"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => choose({ kind: "task" })}
                >
                  New task
                </Button>
              </div>
            )}
          </div>
          {milestonesView ? (
            <section className="collection-view" aria-label="Milestones">
              <CollectionLayout>
                <MilestoneList
                  board={board}
                  selected={selection?.id}
                  open={(id) => choose({ kind: "milestone", id })}
                />
              </CollectionLayout>
            </section>
          ) : showInbox ? (
            <AttentionBoard
              projectId={projectId}
              identity={params.questionId}
              refresh={refresh}
            />
          ) : (
            <ContentStack
              space="section"
              className={archive ? undefined : "board-content"}
            >
              {error && (
                <Alert variant="destructive">
                  <CircleAlert aria-hidden="true" />
                  <AlertTitle>Could not refresh the board</AlertTitle>
                  <AlertDescription>
                    <ContentStack>
                      <p>{error}</p>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setRetry((v) => v + 1)}
                      >
                        Refresh board
                      </Button>
                    </ContentStack>
                  </AlertDescription>
                </Alert>
              )}
              <div className={archive ? "collection-layout" : "board-layout"}>
                {archive ? (
                  <ArchiveList
                    projectId={projectId}
                    tasks={visible}
                    milestones={board.milestones}
                    selection={selection}
                    filtered={!!filter}
                    choose={choose}
                  />
                ) : (
                  <div className="board" aria-label="Task board">
                    {columns.map((column) => {
                      const tasks = visible
                        .filter((t) => t.status === column)
                        .sort(compareTasks);
                      return (
                        <section
                          className={`column ${column} ${dropTarget?.status === column && dropTarget.before === null ? "drop-at-end" : ""}`}
                          aria-label={label(column)}
                          key={column}
                          onDragOver={(e) => {
                            if (
                              !dragged ||
                              archive ||
                              !isUpcoming(column) ||
                              prioritizing
                            )
                              return;
                            e.preventDefault();
                            e.dataTransfer.dropEffect = "move";
                            setDropTarget({ status: column, before: null });
                          }}
                          onDrop={(e) => {
                            if (!dragged || archive || !isUpcoming(column))
                              return;
                            e.preventDefault();
                            if (dropTarget?.before === dragged.id) {
                              setDragged(null);
                              setDropTarget(null);
                              return;
                            }
                            void prioritize(
                              dragged,
                              column,
                              dropTarget?.status === column
                                ? dropTarget.before
                                : null,
                            );
                            setDragged(null);
                            setDropTarget(null);
                          }}
                        >
                          <h2>
                            {label(column)}
                            <span className="count">{tasks.length}</span>
                          </h2>
                          {column === "up_next" && (
                            <QueueControls
                              projectId={projectId}
                              refresh={refresh}
                            />
                          )}
                          <div
                            className="column-cards"
                            tabIndex={0}
                            role="region"
                            aria-label={`${label(column)} tasks`}
                          >
                            {tasks.map((task) => (
                              <div
                                className={`card-shell ${dragged?.id === task.id ? "dragging" : ""} ${dropTarget?.before === task.id ? "drop-before" : ""}`}
                                key={task.id}
                                draggable={
                                  !archive &&
                                  isUpcoming(column) &&
                                  !prioritizing
                                }
                                onDragStart={(e) => {
                                  setDragged(task);
                                  e.dataTransfer.effectAllowed = "move";
                                  e.dataTransfer.setData("text/plain", task.id);
                                }}
                                onDragEnd={() => {
                                  setDragged(null);
                                  setDropTarget(null);
                                }}
                                onDragOver={(e) => {
                                  if (
                                    !dragged ||
                                    archive ||
                                    !isUpcoming(column) ||
                                    prioritizing
                                  )
                                    return;
                                  e.preventDefault();
                                  e.stopPropagation();
                                  e.dataTransfer.dropEffect = "move";
                                  const bounds =
                                    e.currentTarget.getBoundingClientRect();
                                  const upcoming = activeTasks
                                    .filter((t) => t.status === column)
                                    .sort(compareTasks);
                                  const index = upcoming.findIndex(
                                    (t) => t.id === task.id,
                                  );
                                  const before =
                                    e.clientY < bounds.top + bounds.height / 2
                                      ? task.id
                                      : (upcoming[index + 1]?.id ?? null);
                                  setDropTarget({ status: column, before });
                                }}
                              >
                                <Card
                                  className={
                                    selection?.id === task.id &&
                                    selection.kind === "task"
                                      ? "task-card picked gap-1 p-3"
                                      : "task-card gap-1 p-3"
                                  }
                                >
                                  <TaskIdentity
                                    task={task}
                                    title={
                                      <a
                                        className="task-card-link"
                                        aria-label={`${task.key} ${task.title}`}
                                        href={taskHref(projectId, task)}
                                        draggable={false}
                                        onClick={(event) =>
                                          followLink(event, () =>
                                            choose({
                                              kind: "task",
                                              id: task.id,
                                            }),
                                          )
                                        }
                                      >
                                        {task.title}
                                      </a>
                                    }
                                    labels={
                                      <>
                                        <TaskTypeBadge type={task.task_type} />
                                        <DraftBadge
                                          publicationStatus={
                                            task.publication_status
                                          }
                                        />
                                        <MilestoneBadge
                                          milestone={board.milestones.find(
                                            (m) => m.id === task.milestone_id,
                                          )}
                                        />
                                      </>
                                    }
                                  />
                                  {task.state?.tone !== "idle" && (
                                    <TaskState
                                      task={task}
                                      projectId={projectId}
                                      open={(id) =>
                                        choose({ kind: "task", id })
                                      }
                                      linked
                                    />
                                  )}
                                  {hasTaskNeeds(
                                    task,
                                    board.pending_code[task.id],
                                    !task.state,
                                  ) && (
                                    <TaskNeeds
                                      task={task}
                                      projectId={projectId}
                                      pendingCode={board.pending_code[task.id]}
                                      showQuestions={!task.state}
                                      open={(id) =>
                                        choose({ kind: "task", id })
                                      }
                                    />
                                  )}
                                </Card>
                              </div>
                            ))}
                            {!tasks.length && (
                              <p className="empty-column">
                                {column === "backlog"
                                  ? "Ideas and future work"
                                  : column === "up_next"
                                    ? "Choose what matters next"
                                    : column === "done"
                                      ? "Finished outcomes"
                                      : "No work here yet"}
                              </p>
                            )}
                          </div>
                        </section>
                      );
                    })}
                  </div>
                )}
              </div>
            </ContentStack>
          )}
        </TabsContent>
        {selection?.kind === "task" && selection.id ? (
          <EntityPane
            identity={selection.id}
            close={() => choose(null)}
            returnFocusHref={selectionUrl(selection)}
          >
            {editorPanel}
          </EntityPane>
        ) : (
          selection && (
            <EntityOverlay
              identity={`${selection.kind}:${selection.id ?? "new"}`}
              title={
                selection.kind === "task"
                  ? selection.id
                    ? "Task details"
                    : "New task"
                  : selection.kind === "milestone"
                    ? selection.id
                      ? "Milestone details"
                      : "New milestone"
                    : "Project details"
              }
              suspended={!!params.overlayQuestionId}
              close={() => choose(null)}
            >
              {selection.kind === "milestone" ? (
                incoming ? (
                  <MilestoneDetail
                    milestone={incoming as Milestone}
                    close={() => choose(null)}
                  >
                    {editorPanel}
                  </MilestoneDetail>
                ) : (
                  editorPanel
                )
              ) : (
                editorPanel
              )}
            </EntityOverlay>
          )
        )}
      </Tabs>
      {params.questionId && (
        <QuestionOverlay
          projectId={projectId}
          identity={params.questionId}
          tasks={board.tasks}
          refresh={refresh}
          onDirty={setUnsaved}
          open={changeLocation}
          close={() => closeEntity(projectHref(projectId) + "/inbox")}
        />
      )}
      {params.overlayQuestionId && (
        <QuestionOverlay
          projectId={projectId}
          identity={params.overlayQuestionId}
          tasks={board.tasks}
          refresh={refresh}
          onDirty={setOverlayDirty}
          open={changeLocation}
          close={closeQuestion}
        />
      )}
    </>
  );
}

function ArchiveList({
  projectId,
  tasks,
  milestones,
  selection,
  filtered,
  choose,
}: {
  projectId: string;
  tasks: TaskCard[];
  milestones: Milestone[];
  selection: Selection | null;
  filtered: boolean;
  choose: (selection: Selection) => void;
}) {
  const entries = tasks
    .map((task) => ({
      task,
      archived: {
        updated_at: task.archived_at ?? task.updated_at,
        updated_by: task.archived_by ?? task.updated_by,
      },
    }))
    .sort((a, b) =>
      a.archived.updated_at === b.archived.updated_at
        ? a.task.id.localeCompare(b.task.id)
        : a.archived.updated_at > b.archived.updated_at
          ? -1
          : 1,
    );
  return (
    <section className="archive-list" aria-label="Archived tasks">
      {entries.length ? (
        <ul>
          {entries.map(({ task, archived }) => (
            <li key={task.id}>
              <CollectionRow
                className="archive-row"
                timestamp={archived.updated_at}
                selected={
                  selection?.kind === "task" && selection.id === task.id
                }
                href={taskHref(projectId, task)}
                open={() => choose({ kind: "task", id: task.id })}
              >
                <span className="archive-task">
                  <TaskIdentity task={task} />
                  {task.reconciliation_reason && (
                    <span className="readiness blocked">Needs update</span>
                  )}
                  {task.milestone_id && (
                    <span className="archive-labels">
                      <MilestoneBadge
                        linked={false}
                        milestone={milestones.find(
                          (m) => m.id === task.milestone_id,
                        )}
                      />
                    </span>
                  )}
                </span>
                <Attribution
                  author={archived.updated_by}
                  date={archived.updated_at}
                />
              </CollectionRow>
            </li>
          ))}
        </ul>
      ) : (
        <p className="collection-empty">
          {filtered
            ? "No archived tasks match this filter."
            : "No archived tasks yet."}
        </p>
      )}
    </section>
  );
}
