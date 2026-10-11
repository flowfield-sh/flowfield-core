import { reportError } from "./requestFeedback";
import { Toaster } from "@/components/ui/sonner";
import { TaskLabels } from "./TaskLabels";
import { LabelCatalog } from "./labelContext";
import { MilestoneBadge } from "./MilestoneBadge";
import { AnnouncementProvider } from "./Announcements";
import { TaskNeeds, TaskState, hasTaskNeeds } from "./TaskNeeds";
import { WorkspaceList, NewWorkspace } from "./WorkspaceEntry";
import { ProjectList } from "./ProjectList";
import { SetupInstructions } from "./SetupInstructions";
import { taskTab } from "./navigation";
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
import { ContentStack } from "./DetailLayout";
import { Card } from "@/components/ui/card";
import { Settings, Columns3, Inbox, Flag, Archive, Folder } from "lucide-react";
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
import { WorkspaceEditor } from "./WorkspaceEditor";
import { QueueControls } from "./Workers";
import { AttentionBoard } from "./AttentionBoard";
import { MilestoneEditor } from "./MilestoneEditor";
import { MilestoneDetail, MilestoneList } from "./Milestones";
import {
  Attribution,
  CollectionLayout,
  CollectionRow,
  TaskIdentity,
  DraftBadge,
} from "./Presentation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Board,
  WorkspaceBoard as SharedBoard,
  compareTasks,
  isUpcoming,
  columns,
  Milestone,
  label,
  Project,
  WorkspaceRecord,
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
  workspaceHref,
  taskHref,
  useWorkspaceNavigation,
} from "./navigation";

type Selection = { kind: "task" | "milestone" | "project"; id?: string };
type WorkRecord = Project | Milestone | Task;

export function App() {
  const appearance = useTheme();
  const [chatDrafts] = useState<ChatDrafts>(() => new Map());
  const [chatSettingsDirty, setChatSettingsDirty] = useState(false);
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);
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
  const [knownOwners, setKnownOwners] = useState<Record<string, string>>({});
  const registerProjects = useCallback(
    (workspaceId: string, projects: Project[]) => {
      setKnownOwners((previous) =>
        projects.every((project) => previous[project.id] === workspaceId)
          ? previous
          : {
              ...previous,
              ...Object.fromEntries(
                projects.map((project) => [project.id, workspaceId]),
              ),
            },
      );
    },
    [],
  );
  const projectId = params.projectId;
  const {
    data: projectWorkspace,
    error: projectWorkspaceError,
    retry: retryProjectWorkspace,
  } = useResource<{ id: string }>(
    projectId ? `projects/${projectId}/workspace` : null,
    refresh,
  );
  const workspaceId =
    params.workspaceId ?? knownOwners[projectId ?? ""] ?? projectWorkspace?.id;
  const workspace = workspaces.find((item) => item.id === workspaceId);
  const { data: attentionCounts } = useResource<Record<string, number>>(
    "workspaces/attention-counts",
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
    request<WorkspaceRecord[]>("workspaces")
      .then((items) => {
        if (active) {
          setWorkspaces(items);
          setConnected(true);
        }
      })
      .catch((e: Error) => {
        if (active) reportError(e, "Could not load workspaces", true);
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
    let opened = false;
    function connect() {
      events?.close();
      events = new EventSource("/api/events");
      events.onopen = () => {
        setLive(true);
        if (opened) window.dispatchEvent(new Event("flowfield:reconnected"));
        opened = true;
      };
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
    <AnnouncementProvider navigate={changeLocation}>
      <Toaster theme={appearance.theme} />
      <WorkspaceFrame
        workLocation={pathname}
        workspaces={workspaces.map((workspace) => ({
          id: workspace.id,
          name: workspace.name,
          href: workspaceHref(workspace.id),
          inboxCount: attentionCounts?.[workspace.id] ?? 0,
        }))}
        activeWorkspaceId={workspaceId ?? ""}
        onWorkspaceSelect={(workspace) => changeLocation(workspace.href)}
        onHome={() => changeLocation("/")}
        onAddWorkspace={() => changeLocation("/new-workspace")}
        footer={
          <>
            <SettingsLink active={pathname.startsWith("/settings/")} />
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
          connected && workspaceId && workspace && !notFound
            ? (visible) => (
                <CoordinatorChat
                  key={workspaceId}
                  workspaceId={workspaceId}
                  projectId={projectId}
                  refresh={`${refresh}:${JSON.stringify(projectRefresh)}`}
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
                <p>Choose a workspace to continue.</p>
              </section>
            ) : pathname.startsWith("/settings/") ? (
              <FlowfieldSettings
                onDirty={setUnsaved}
                appearance={appearance}
                tab={
                  pathname === "/settings/appearance"
                    ? "appearance"
                    : pathname === "/settings/notifications"
                      ? "notifications"
                      : pathname === "/settings/updates"
                        ? "updates"
                        : "harnesses"
                }
              />
            ) : workspaceId && workspace ? (
              <WorkspaceBoard
                registerProjects={registerProjects}
                key={workspaceId}
                workspaceId={workspaceId}
                projectId={projectId}
                refresh={refresh + ":" + JSON.stringify(projectRefresh)}
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
            ) : projectId || workspaceId ? (
              <section className="welcome">
                <h1>
                  {params.workspaceId || projectWorkspace
                    ? "Workspace not found"
                    : projectWorkspaceError
                      ? "Could not open project"
                      : "Opening workspace…"}
                </h1>
                {projectWorkspaceError && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={retryProjectWorkspace}
                  >
                    Retry project
                  </Button>
                )}
              </section>
            ) : pathname === "/new-workspace" ? (
              <NewWorkspace
                onDirty={setUnsaved}
                created={(workspace) => {
                  setWorkspaces((items) => [
                    ...items.filter((item) => item.id !== workspace.id),
                    workspace,
                  ]);
                  setRefresh((value) => value + 1);
                  changeLocation(workspaceHref(workspace.id), true);
                }}
              />
            ) : (
              <WorkspaceList workspaces={workspaces} />
            )}
          </>
        }
      />
      <DiscardChangesDialog {...discardChanges} />
    </AnnouncementProvider>
  );
}

function WorkspaceBoard({
  workspaceId,
  registerProjects,
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
  workspaceId: string;
  registerProjects: (workspaceId: string, projects: Project[]) => void;
  projectId?: string;
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
  const [retry, setRetry] = useState(0);
  const path = `workspaces/${workspaceId}`;
  const projectPath = projectId
    ? `projects/${encodeURIComponent(projectId)}`
    : "";
  const base = workspaceHref(workspaceId);
  const {
    data: board,
    setData: setBoard,
    error,
    invalidate,
  } = useResource<SharedBoard>(`${path}/view/board`, `${refresh}:${retry}`);
  useEffect(() => {
    if (board) registerProjects(workspaceId, board.projects);
  }, [board, workspaceId, registerProjects]);
  const questionSelected = params.questionId !== undefined;
  const taskRef = params.taskKey;
  const {
    data: taskDetail,
    setData: setTaskDetail,
    error: detailError,
    errorCode: detailErrorCode,
    loading: detailLoading,
    invalidate: invalidateDetail,
  } = useResource<Task>(
    taskRef && projectId
      ? `${projectPath}/view/tasks/${encodeURIComponent(taskRef)}`
      : null,
    `${refresh}:${retry}`,
  );
  const selectedTask = board?.tasks.find(
    (t) =>
      t.project_id === projectId && (t.key === taskRef || t.id === taskRef),
  );
  const newKind = pathname.includes("/tasks/new")
    ? "task"
    : pathname.endsWith("/milestones/new")
      ? "milestone"
      : null;
  const workspaceSettings =
    pathname === `${base}/edit` ||
    pathname.startsWith(`${base}/edit/`) ||
    ["workers", "coordinator"].includes(params.projectTab ?? "");
  const selection: Selection | null = workspaceSettings
    ? null
    : questionSelected
      ? null
      : taskRef
        ? { kind: "task", id: selectedTask?.key ?? taskRef }
        : params.milestoneId
          ? { kind: "milestone", id: params.milestoneId! }
          : projectId &&
              (pathname === `${projectHref(projectId)}/edit` ||
                pathname.startsWith(`${projectHref(projectId)}/edit/`))
            ? { kind: "project", id: projectId }
            : newKind === "task" || newKind === "milestone"
              ? { kind: newKind }
              : null;
  const background = backgroundPath ?? pathname;
  const inView = (view: string) =>
    background === `${base}/${view}` ||
    background.startsWith(`${base}/${view}/`) ||
    (!!projectId &&
      (background === `${projectHref(projectId)}/${view}` ||
        background.startsWith(`${projectHref(projectId)}/${view}/`)));
  const projectsView = inView("projects");
  const addingProject = pathname.endsWith("/projects/new");
  const selectedProject = board?.projects.find(
    (project) => project.id === projectId,
  );
  const [projectFilter, setProjectFilter] = useState(projectId ?? "");
  const [labelFilter, setLabelFilter] = useState("");
  const [filterRoute, setFilterRoute] = useState(pathname);
  if (filterRoute !== pathname) {
    setFilterRoute(pathname);
    if (
      projectId &&
      !params.taskKey &&
      !params.questionId &&
      !pathname.includes("/edit")
    )
      setProjectFilter(projectId);
  }
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
        `projects/${task.project_id}/view/tasks/${encodeURIComponent(task.id)}/prioritize`,
        "POST",
        {
          expected_revision: task.revision,
          status,
          before_id: before,
          author: "human",
        },
      );
    } catch (e) {
      reportError(e, "Priority could not change");
    } finally {
      priorityPending.current = false;
      setPrioritizing(false);
      setRetry((v) => v + 1);
    }
  }
  function selectionUrl(next: Selection | null, archived = archive) {
    if (!next)
      return (
        base + (milestonesView ? "/milestones" : archived ? "/archive" : "")
      );
    if (next.kind === "project") return projectHref(next.id!) + "/edit";
    if (!next.id) return base + "/" + next.kind + "s/new";
    if (next.kind === "task") {
      const task = board?.tasks.find(
        (t) =>
          t.key === next.id || (t.project_id === projectId && t.id === next.id),
      );
      return taskHref(task?.project_id ?? projectId!, {
        key: task?.key ?? next.id,
      });
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
      if (kind === "project")
        return {
          ...current,
          projects: current.projects.map((project) =>
            project.id === item.id ? (item as Project) : project,
          ),
        };
      const key = kind === "task" ? "tasks" : "milestones";
      return {
        ...current,
        [key]: [
          ...current[key].filter((v) =>
            kind === "task"
              ? (v as TaskCard).key !== (item as Task).key
              : v.id !== item.id,
          ),
          item,
        ],
      } as SharedBoard;
    });
    setUnsaved(false);
    const previous =
      kind === "task"
        ? board?.tasks.find((t) => t.key === (item as Task).key)
        : null;
    const archiveChanged =
      previous && previous.archived !== (item as Task).archived;
    changeLocation(
      archiveChanged
        ? selectionUrl(null)
        : kind === "task"
          ? taskHref(
              (item as Task).project_id,
              item as Task,
              taskTab(params.tab),
            )
          : kind === "milestone"
            ? `${base}/milestones/${(item as Milestone).key}`
            : selectionUrl({ kind, id: item.id }),
      true,
    );
    setRetry((v) => v + 1);
  }
  if (!board)
    return (
      <section className="welcome content-stack" data-space="section">
        <h1>{error ? "Could not open workspace" : "Loading board…"}</h1>
        {error && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setRetry((v) => v + 1)}
          >
            Retry board
          </Button>
        )}
      </section>
    );
  const visible = board.tasks.filter(
    (t) =>
      t.archived === archive &&
      (!projectFilter || t.project_id === projectFilter) &&
      (!labelFilter || t.labels.includes(labelFilter)) &&
      (!filter ||
        (filter === "none" ? !t.milestone_id : t.milestone_id === filter)),
  );
  const activeTasks = board.tasks.filter((t) => !t.archived);
  const incoming =
    selection?.kind === "project"
      ? selectedProject
      : selection?.kind === "task"
        ? (taskDetail ?? undefined)
        : board.milestones.find(
            (e) => e.id === selection?.id || e.key === selection?.id,
          );
  const view = projectsView
    ? "projects"
    : showInbox
      ? "inbox"
      : milestonesView
        ? "milestones"
        : archive
          ? "archive"
          : "board";
  function openView(value: string) {
    navigate(() =>
      changeLocation(base + (value === "board" ? "" : "/" + value)),
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
              title={
                detailError && detailErrorCode !== "not_found"
                  ? `Could not load ${selection.kind}`
                  : `${label(selection.kind)} not found`
              }
              close={() => choose(null)}
            />
            {detailError && detailErrorCode !== "not_found" ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setRetry((v) => v + 1)}
              >
                Retry loading task
              </Button>
            ) : (
              <p>
                This reference does not identify a {selection.kind} in this
                project.
              </p>
            )}
          </section>
        ))}
      {selection &&
        (!selection.id || incoming) &&
        (selection.kind === "project" && incoming ? (
          <ProjectEditor
            key={selection.id}
            incoming={incoming as Project}
            path={projectPath}
            hasTasks={board.tasks.some((task) => task.project_id === projectId)}
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
                    changeLocation(base);
                  }}
                >
                  View tasks
                </Button>
              )
            }
          />
        ) : incoming && selectedProject ? (
          <TaskDetail
            key={selection.id}
            task={incoming as Task}
            path={projectPath}
            board={
              {
                ...board,
                project: selectedProject,
                tasks: board.tasks.filter(
                  (task) => task.project_id === projectId,
                ),
                pending_code: Object.fromEntries(
                  board.tasks
                    .filter((task) => task.project_id === projectId)
                    .map((task) => [
                      task.id,
                      board.pending_code[task.key] ?? [],
                    ]),
                ),
              } as Board
            }
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
            defaultProjectId={projectFilter}
            board={board}
            setUnsaved={setUnsaved}
            saved={(item) => saved("task", item)}
            close={() => choose(null)}
          />
        ))}
    </>
  );
  return (
    <LabelCatalog value={board.labels}>
      <header className="workspace-pane-header">
        <h1>{board.workspace.name}</h1>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Workspace settings"
          onClick={() => changeLocation(`${base}/edit`)}
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
          <TabsList variant="line" aria-label="Workspace views">
            <TabsTrigger {...viewTrigger("board")}>
              <Columns3 /> Board
              {activeTasks.length > 0 && (
                <span className="nav-count">{activeTasks.length}</span>
              )}
            </TabsTrigger>
            <TabsTrigger {...viewTrigger("inbox")}>
              <Inbox /> Inbox
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
            <TabsTrigger {...viewTrigger("projects")}>
              <Folder /> Projects
              <span className="nav-count">{board.projects.length}</span>
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
          className={`workspace-work-content ${!milestonesView && !projectsView && !archive ? "workspace-task-board" : ""}`}
          hidden={!!taskRef}
        >
          {!projectsView && (
            <div className="board-toolbar">
              {!milestonesView && (
                <div className="actions">
                  <NativeSelect
                    size="sm"
                    aria-label="Filter by project"
                    value={projectFilter}
                    onChange={(event) => setProjectFilter(event.target.value)}
                  >
                    <option value="">All projects</option>
                    {board.projects.map((project) => (
                      <option key={project.id} value={project.id}>
                        {project.name}
                      </option>
                    ))}
                  </NativeSelect>
                  {!showInbox && (
                    <NativeSelect
                      size="sm"
                      aria-label="Filter by label"
                      value={labelFilter}
                      onChange={(event) => setLabelFilter(event.target.value)}
                    >
                      <option value="">All labels</option>
                      {board.labels
                        .filter((item) => !item.deleted_at)
                        .map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name}
                          </option>
                        ))}
                    </NativeSelect>
                  )}
                  {projectFilter && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        choose({ kind: "project", id: projectFilter })
                      }
                    >
                      Project settings
                    </Button>
                  )}
                </div>
              )}

              {milestonesView && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => choose({ kind: "milestone" })}
                >
                  New milestone
                </Button>
              )}
              {!showInbox && !milestonesView && !projectsView && (
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
                    disabled={!board.projects.length}
                    onClick={() => choose({ kind: "task" })}
                  >
                    New task
                  </Button>
                </div>
              )}
            </div>
          )}
          {projectsView ? (
            addingProject ? (
              <SetupInstructions
                workspaceId={workspaceId}
                onDirty={setUnsaved}
                added={(project) => {
                  setUnsaved(false);
                  setRetry((v) => v + 1);
                  setProjectFilter(project.id);
                  changeLocation(base, true);
                }}
              />
            ) : (
              <ProjectList
                projects={board.projects}
                problems={board.project_problems}
                workspaceId={workspaceId}
              />
            )
          ) : milestonesView ? (
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
              workspaceId={workspaceId}
              projectId={projectFilter || undefined}
              identity={params.questionId}
              refresh={refresh}
            />
          ) : (
            <ContentStack
              space="section"
              className={archive ? undefined : "board-content"}
            >
              {error && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setRetry((v) => v + 1)}
                >
                  Refresh board
                </Button>
              )}
              {!board.projects.length && (
                <div className="actions px-4">
                  <p>Add a project to start creating tasks.</p>
                  <Button
                    size="sm"
                    onClick={() => changeLocation(`${base}/projects/new`)}
                  >
                    Add project
                  </Button>
                </div>
              )}
              <div className={archive ? "collection-layout" : "board-layout"}>
                {archive ? (
                  <ArchiveList
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
                            if (dropTarget?.before === dragged.key) {
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
                              workspaceId={workspaceId}
                              problems={board.projects
                                .filter(
                                  (project) =>
                                    (!projectFilter ||
                                      project.id === projectFilter) &&
                                    board.project_problems[project.id],
                                )
                                .map((project) => ({
                                  projectId: project.id,
                                  name: project.name,
                                  message: board.project_problems[project.id],
                                }))}
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
                                className={`card-shell ${dragged?.key === task.key ? "dragging" : ""} ${dropTarget?.before === task.key ? "drop-before" : ""}`}
                                key={task.key}
                                draggable={
                                  !archive &&
                                  isUpcoming(column) &&
                                  !prioritizing
                                }
                                onDragStart={(e) => {
                                  setDragged(task);
                                  e.dataTransfer.effectAllowed = "move";
                                  e.dataTransfer.setData(
                                    "text/plain",
                                    task.key,
                                  );
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
                                    (t) => t.key === task.key,
                                  );
                                  const before =
                                    e.clientY < bounds.top + bounds.height / 2
                                      ? task.key
                                      : (upcoming[index + 1]?.key ?? null);
                                  setDropTarget({ status: column, before });
                                }}
                              >
                                <Card
                                  className={
                                    selection?.id === task.key &&
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
                                        href={taskHref(task.project_id, task)}
                                        draggable={false}
                                        onClick={(event) =>
                                          followLink(event, () =>
                                            choose({
                                              kind: "task",
                                              id: task.key,
                                            }),
                                          )
                                        }
                                      >
                                        {task.title}
                                      </a>
                                    }
                                    labels={
                                      <>
                                        <TaskLabels ids={task.labels} />
                                        <DraftBadge
                                          publicationStatus={
                                            task.publication_status
                                          }
                                        />
                                        <MilestoneBadge
                                          projectId={task.project_id}
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
                                      projectId={task.project_id}
                                      open={(id) =>
                                        choose({
                                          kind: "task",
                                          id:
                                            board.tasks.find(
                                              (item) =>
                                                item.project_id ===
                                                  task.project_id &&
                                                (item.id === id ||
                                                  item.key === id),
                                            )?.key ?? id,
                                        })
                                      }
                                      linked
                                    />
                                  )}
                                  {hasTaskNeeds(
                                    task,
                                    board.pending_code[task.key],
                                    !task.state,
                                  ) && (
                                    <TaskNeeds
                                      task={task}
                                      projectId={task.project_id}
                                      pendingCode={board.pending_code[task.key]}
                                      showQuestions={!task.state}
                                      open={(id) =>
                                        choose({
                                          kind: "task",
                                          id:
                                            board.tasks.find(
                                              (item) =>
                                                item.project_id ===
                                                  task.project_id &&
                                                (item.id === id ||
                                                  item.key === id),
                                            )?.key ?? id,
                                        })
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
      {workspaceSettings && (
        <EntityOverlay
          title="Workspace settings"
          identity={workspaceId}
          close={() => closeEntity(base)}
        >
          <WorkspaceEditor
            incoming={board.workspace}
            onDirty={setUnsaved}
            saved={(workspace) => {
              invalidate();
              setBoard((current) =>
                current ? { ...current, workspace } : current,
              );
              setUnsaved(false);
              setRetry((value) => value + 1);
            }}
            close={() => closeEntity(base)}
          />
        </EntityOverlay>
      )}
      {params.questionId && projectId && (
        <QuestionOverlay
          projectId={projectId}
          identity={params.questionId}
          tasks={board.tasks.filter((task) => task.project_id === projectId)}
          refresh={refresh}
          onDirty={setUnsaved}
          open={changeLocation}
          close={() => closeEntity(projectHref(projectId) + "/inbox")}
        />
      )}
      {params.overlayQuestionId && projectId && (
        <QuestionOverlay
          projectId={projectId}
          identity={params.overlayQuestionId}
          tasks={board.tasks.filter((task) => task.project_id === projectId)}
          refresh={refresh}
          onDirty={setOverlayDirty}
          open={changeLocation}
          close={closeQuestion}
        />
      )}
    </LabelCatalog>
  );
}

function ArchiveList({
  tasks,
  milestones,
  selection,
  filtered,
  choose,
}: {
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
        ? a.task.key.localeCompare(b.task.key)
        : a.archived.updated_at > b.archived.updated_at
          ? -1
          : 1,
    );
  return (
    <section className="archive-list" aria-label="Archived tasks">
      {entries.length ? (
        <ul>
          {entries.map(({ task, archived }) => (
            <li key={task.key}>
              <CollectionRow
                className="archive-row"
                timestamp={archived.updated_at}
                selected={
                  selection?.kind === "task" && selection.id === task.key
                }
                href={taskHref(task.project_id, task)}
                open={() => choose({ kind: "task", id: task.key })}
              >
                <span className="archive-task">
                  <TaskIdentity task={task} />
                  {task.reconciliation_reason && (
                    <span className="readiness blocked">Needs update</span>
                  )}
                  {task.milestone_id && (
                    <span className="archive-labels">
                      <MilestoneBadge
                        projectId={task.project_id}
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
