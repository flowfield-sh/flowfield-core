import { Board as ScrollableBoard } from "./Board";
import { ConnectionRecovery } from "./ConnectionRecovery";
import {
  checkServiceConnection,
  serviceIsUnavailable,
  useServiceUnavailable,
} from "./serviceConnection";
import { WorkIdentity } from "./WorkIdentity";
import { TaskLabels } from "./TaskLabels";
import { hasRetainedDrafts, type DirtyCallback } from "./retainedState";
import {
  ResourceNavigation,
  WorkspaceTabs,
  useWorkspaceTabs,
} from "./WorkspaceTabs";
import { WorkNavigation, WorkList } from "./WorkNavigation";
import { useLocation } from "react-router";
import { ProjectOwners, ScopedAnchor } from "./WorkspaceLink";
import { reportError } from "./requestFeedback";
import { Toaster } from "@/components/ui/sonner";
import { LabelCatalog } from "./labelContext";
import { MilestoneBadge } from "./MilestoneBadge";
import { AnnouncementProvider } from "./Announcements";
import { TaskNeeds, TaskState, hasTaskNeeds } from "./TaskNeeds";
import { WorkspaceList, NewWorkspace } from "./WorkspaceEntry";
import { Documents } from "./Documents";
import { ProjectList } from "./ProjectList";
import { SetupInstructions } from "./SetupInstructions";
import { canonicalHref } from "./navigation";
import type { AdjacentContext, AdjacentSnapshot } from "./AdjacentView";
import { CoordinatorChat, type ChatDrafts } from "./CoordinatorChat";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { WorkspaceFrame } from "./WorkspaceFrame";
import { useTheme } from "./AppearanceSettings";
import { FlowfieldSettings } from "./FlowfieldSettings";
import { ContentStack } from "./DetailLayout";
import { WorkCard } from "./WorkCard";
import { NativeSelect } from "@/components/ui/native-select";
import { Button } from "@/components/ui/button";
import { EntityPane } from "./EntityPane";
import { DetailHeader } from "./Presentation";
import { useResource } from "./useResource";
import { TaskDetail } from "./TaskDetail";
import { ProjectEditor } from "./ProjectEditor";
import { WorkspaceEditor } from "./WorkspaceEditor";
import { QueueControls } from "./Workers";
import { RequestDetail } from "./RequestDetail";
import { AttentionBoard } from "./AttentionBoard";
import { MilestoneDetail } from "./MilestoneDetail";
import { MilestoneList } from "./Milestones";
import { ArchiveAttribution, TaskIdentity, DraftBadge } from "./Presentation";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Board,
  WorkspaceBoard as SharedBoard,
  compareTasks,
  columns,
  Milestone,
  label,
  Project,
  WorkspaceRecord,
  request,
  Task,
  TaskCard,
} from "./workspace";
import { QuestionPage } from "./Inbox";
import { DiscardChangesDialog } from "./DiscardChangesDialog";
import {
  boardHref,
  followLink,
  projectHref,
  workspaceHref,
  taskHref,
  useWorkspaceNavigation,
} from "./navigation";

type Selection = { kind: "task" | "milestone" | "project"; id: string };

export function App() {
  const contextLocation = useLocation();
  const appearance = useTheme();
  const unavailable = useServiceUnavailable();
  const [chatDrafts] = useState<ChatDrafts>(() => new Map());
  const [adjacent, setAdjacent] = useState<AdjacentSnapshot | null>(null);
  const [chatSettingsDirty, setChatSettingsDirty] = useState(false);
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const recovered = () => {
      setRefresh((value) => value + 1);
      window.dispatchEvent(new Event("flowfield:reconnected"));
    };
    window.addEventListener("flowfield:service-recovered", recovered);
    return () =>
      window.removeEventListener("flowfield:service-recovered", recovered);
  }, []);

  const [projectRefresh, setProjectRefresh] = useState<Record<string, number>>(
    {},
  );
  const [unsaved, setUnsaved] = useState(false);
  const navigationDirty = useCallback<DirtyCallback>(
    (value, retained) => setUnsaved(value && !retained),
    [],
  );
  const [knownOwners, setKnownOwners] = useState<Record<string, string>>({});
  const {
    params,
    pathname,
    changeLocation: rawChangeLocation,
    notFound,
    discardChanges,
  } = useWorkspaceNavigation(
    unsaved,
    setUnsaved,
    chatSettingsDirty,
    knownOwners,
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
    params.workspaceId ??
    knownOwners[projectId ?? ""] ??
    projectWorkspace?.id ??
    (pathname.startsWith("/settings/") || pathname === "/new-workspace"
      ? (contextLocation.state?.workspaceTabs?.workspace as string | undefined)
      : undefined);
  // Resolving a project alias changes the URL, not the work the human opened.
  // Use its known workspace before board loading registers all member projects.
  const workLocation = canonicalHref(
    pathname,
    projectId && workspaceId
      ? { ...knownOwners, [projectId]: workspaceId }
      : knownOwners,
  ).split("?")[0];
  const wrongWorkspace = !!(
    params.workspaceId &&
    projectWorkspace &&
    params.workspaceId !== projectWorkspace.id
  );
  const workspace = workspaces.find((item) => item.id === workspaceId);
  const workspaceRefresh = `${refresh}:${JSON.stringify(
    Object.entries(projectRefresh).filter(
      ([id]) => knownOwners[id] === workspaceId,
    ),
  )}`;
  const { data: attentionCounts } = useResource<Record<string, number>>(
    "workspaces/attention-counts",
    `${refresh}:${JSON.stringify(projectRefresh)}`,
  );
  const tabOwners = useMemo(
    () =>
      projectId && workspaceId
        ? { ...knownOwners, [projectId]: workspaceId }
        : knownOwners,
    [knownOwners, projectId, workspaceId],
  );
  const boardResource = useResource<SharedBoard>(
    workspaceId ? `workspaces/${workspaceId}/view/board` : null,
    workspaceRefresh,
  );
  const board = boardResource.data;
  const tabs = useWorkspaceTabs(
    workspaceId,
    rawChangeLocation,
    tabOwners,
    board,
  );
  const tabGo = tabs.go;
  const changeLocation = useCallback(
    (url: string, replace = false) => tabGo(url, "within", replace),
    [tabGo],
  );
  const openResource = useCallback(
    (url: string) => tabGo(url, "open"),
    [tabGo],
  );
  if (
    board &&
    board.projects.some(
      (project) => knownOwners[project.id] !== board.workspace.id,
    )
  )
    setKnownOwners((previous) => ({
      ...previous,
      ...Object.fromEntries(
        board.projects.map((project) => [project.id, board.workspace.id]),
      ),
    }));
  const connectionStatus = unavailable
    ? "Disconnected"
    : connected
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
    let streaming = false;
    let streamNeedsRefresh = false;
    // Health recovery already refreshes reads and catalogs. A later stream-open
    // signal for the same outage must not discover the model catalogs again.
    const recovered = () => {
      streamNeedsRefresh = false;
    };
    const disconnected = () => {
      if (streaming) streamNeedsRefresh = true;
      streaming = false;
      setLive(false);
    };
    function changed(change: { projects: string[] | null }) {
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
    }
    function saved(event: Event) {
      const { path } = (event as CustomEvent<{ path: string }>).detail;
      // Claiming notification delivery does not change workspace data.
      if (path === "announcements/poll") return;
      const project = /^projects\/([^/]+)\//.exec(path)?.[1];
      // Confirmed local writes refresh cards and counts even while SSE reconnects.
      changed({ projects: project ? [decodeURIComponent(project)] : null });
    }
    function connect() {
      events?.close();
      events = new EventSource("/api/events");
      events.onopen = () => {
        streaming = true;
        setLive(true);
        if (opened && streamNeedsRefresh && !serviceIsUnavailable())
          window.dispatchEvent(new Event("flowfield:reconnected"));
        streamNeedsRefresh = false;
        opened = true;
      };
      events.onerror = () => {
        disconnected();
        void checkServiceConnection();
      };
      events.addEventListener("change", (event) => {
        changed(JSON.parse(event.data) as { projects: string[] | null });
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
      disconnected();
    }
    if (navigator.onLine) connect();
    window.addEventListener("online", connect);
    window.addEventListener("offline", offline);
    window.addEventListener("flowfield:service-recovered", recovered);
    window.addEventListener("flowfield:mutation", saved);
    return () => {
      events?.close();
      window.removeEventListener("online", connect);
      window.removeEventListener("offline", offline);
      window.removeEventListener("flowfield:service-recovered", recovered);
      window.removeEventListener("flowfield:mutation", saved);
    };
  }, [connected]);
  useEffect(() => {
    function guard(e: BeforeUnloadEvent) {
      if (
        hasRetainedDrafts() ||
        unsaved ||
        chatSettingsDirty ||
        [...chatDrafts.values()].some((value) => value.text)
      )
        e.preventDefault();
    }
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [unsaved, chatSettingsDirty, chatDrafts]);
  function navigate(action: () => void) {
    action();
  }
  function backFromGlobal() {
    const tab = tabs.state?.tabs.find((item) => item.id === tabs.state?.active);
    if (tab?.fresh && tab.index === 0) return;
    if (tab && tab.index > 0) {
      let index = tab.index - 1;
      while (
        index > 0 &&
        pathname.startsWith("/settings/") &&
        tab.history[index].startsWith("/settings/")
      )
        index--;
      tabs.step(index - tab.index);
    } else changeLocation(workspaceId ? workspaceHref(workspaceId) : "/");
  }
  const settingsPage = (
    <EntityPane
      label="Flowfield settings"
      identity="settings"
      returnFocusHref={workspaceId ? workspaceHref(workspaceId) : "/"}
      close={backFromGlobal}
    >
      <DetailHeader title="Settings" close={backFromGlobal} />
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
    </EntityPane>
  );
  const newWorkspacePage = (
    <EntityPane
      label="New workspace"
      identity="new-workspace"
      returnFocusHref="/"
      close={backFromGlobal}
    >
      <DetailHeader title="New workspace" close={backFromGlobal} />
      <NewWorkspace
        onDirty={setUnsaved}
        created={(workspace) => {
          setWorkspaces((items) => [
            ...items.filter((item) => item.id !== workspace.id),
            workspace,
          ]);
          setRefresh((value) => value + 1);
          rawChangeLocation(workspaceHref(workspace.id), true);
        }}
      />
    </EntityPane>
  );
  return (
    <ProjectOwners.Provider value={knownOwners}>
      <ResourceNavigation.Provider value={openResource}>
        <AnnouncementProvider navigate={openResource}>
          <Toaster theme={appearance.theme} />
          <ConnectionRecovery />
          <WorkspaceFrame
            workLocation={workLocation}
            workspaces={workspaces.map((workspace) => ({
              id: workspace.id,
              name: workspace.name,
              href: workspaceHref(workspace.id),
              inboxCount: attentionCounts?.[workspace.id] ?? 0,
            }))}
            activeWorkspaceId={workspaceId ?? ""}
            onWorkspaceSelect={(workspace) => rawChangeLocation(workspace.href)}
            onHome={() => changeLocation("/")}
            settingsActive={pathname.startsWith("/settings/")}
            navigation={
              board && workspaceId ? (
                <WorkNavigation
                  key={workspaceId}
                  board={board}
                  open={openResource}
                  current={
                    tabs.state?.tabs.find((t) => t.id === tabs.state?.active)
                      ?.root ?? pathname
                  }
                />
              ) : undefined
            }
            queue={
              board && workspaceId ? (
                <QueueControls
                  workspaceId={workspaceId}
                  refresh={workspaceRefresh}
                  problems={board.projects
                    .filter((p) => board.project_problems[p.id])
                    .map((p) => ({
                      projectId: p.id,
                      name: p.name,
                      message: board.project_problems[p.id],
                    }))}
                />
              ) : undefined
            }
            footer={
              <div className="workspace-utilities">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <p
                      className="connection"
                      role="status"
                      tabIndex={0}
                      aria-label={connectionStatus}
                    >
                      <span
                        className={
                          live && !unavailable ? "live-dot" : "live-dot offline"
                        }
                      />
                      <span data-sidebar="label">{connectionStatus}</span>
                    </p>
                  </TooltipTrigger>
                  <TooltipContent side="right">
                    {connectionStatus}
                  </TooltipContent>
                </Tooltip>
              </div>
            }
            coordinator={
              connected &&
              workspaceId &&
              workspace &&
              !notFound &&
              !wrongWorkspace &&
              (!projectId ||
                (projectWorkspace?.id ?? knownOwners[projectId]) ===
                  workspaceId)
                ? (visible) => (
                    <CoordinatorChat
                      key={workspaceId}
                      workspaceId={workspaceId}
                      viewPath={pathname}
                      adjacent={
                        pathname.startsWith("/settings/") ||
                        pathname === "/new-workspace"
                          ? { view: null, task: null }
                          : adjacent?.workspaceId === workspaceId &&
                              adjacent.pathname === pathname
                            ? adjacent.context
                            : null
                      }
                      refresh={workspaceRefresh}
                      drafts={chatDrafts}
                      onSettingsDirty={setChatSettingsDirty}
                      controlsActive={visible}
                      offerInitialSetup={pathname === boardHref(workspaceId)}
                    />
                  )
                : undefined
            }
            work={
              <>
                {!connected ? (
                  <section className="welcome">
                    <h1>
                      {loading
                        ? "Opening your workspace"
                        : "Could not load workspaces"}
                    </h1>
                    {!loading && !unavailable && (
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
                ) : notFound || wrongWorkspace ? (
                  <section className="welcome">
                    <h1>Page not found</h1>
                    <p>Choose a workspace to continue.</p>
                  </section>
                ) : pathname.startsWith("/settings/") && !workspaceId ? (
                  settingsPage
                ) : workspaceId &&
                  workspace &&
                  (!projectId ||
                    (projectWorkspace?.id ?? knownOwners[projectId]) ===
                      workspaceId) ? (
                  <WorkspaceTabs controller={tabs} board={board}>
                    <ResourceNavigation.Provider value={tabs.go}>
                      {pathname.startsWith("/settings/") ? (
                        settingsPage
                      ) : pathname === "/new-workspace" ? (
                        newWorkspacePage
                      ) : (
                        <WorkspaceResource
                          boardResource={boardResource}
                          publishView={setAdjacent}
                          key={workspaceId}
                          workspaceId={workspaceId}
                          projectId={projectId}
                          refresh={workspaceRefresh}
                          params={params}
                          pathname={pathname}
                          closeEntity={(fallback) => {
                            const tab = tabs.state?.tabs.find(
                              (t) => t.id === tabs.state?.active,
                            );
                            if (tab?.fresh && tab.index === 0) return;
                            if (
                              /^\/(?:workspaces|projects)\/[^/]+\/edit(?:\/|$)/.test(
                                pathname,
                              )
                            ) {
                              const origin =
                                tab?.history
                                  .slice(0, tab.index)
                                  .reduce(
                                    (previous, url, index) =>
                                      /^\/(?:workspaces|projects)\/[^/]+\/edit(?:\/|$)/.test(
                                        url,
                                      )
                                        ? previous
                                        : index,
                                    -1,
                                  ) ?? -1;
                              if (tab && origin >= 0)
                                tabs.step(origin - tab.index);
                              else changeLocation(fallback);
                            } else if (tab && tab.index > 0) tabs.step(-1);
                            else changeLocation(fallback);
                          }}
                          changeLocation={changeLocation}
                          navigate={navigate}
                          setUnsaved={navigationDirty}
                        />
                      )}
                    </ResourceNavigation.Provider>
                  </WorkspaceTabs>
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
                  newWorkspacePage
                ) : (
                  <WorkspaceList
                    workspaces={workspaces}
                    attentionCounts={attentionCounts}
                  />
                )}
              </>
            }
          />
          <DiscardChangesDialog {...discardChanges} />
        </AnnouncementProvider>
      </ResourceNavigation.Provider>
    </ProjectOwners.Provider>
  );
}

function WorkspaceResource({
  publishView,
  workspaceId,
  boardResource,
  projectId,
  refresh,
  params,
  pathname,
  changeLocation,
  navigate,
  setUnsaved,
  closeEntity,
}: {
  workspaceId: string;
  publishView: (snapshot: AdjacentSnapshot) => void;
  boardResource: ReturnType<typeof useResource<SharedBoard>>;
  projectId?: string;
  refresh: string;
  params: Record<string, string | undefined>;
  pathname: string;
  changeLocation: (url: string, replace?: boolean) => void;
  navigate: (action: () => void) => void;
  setUnsaved: DirtyCallback;
  closeEntity: (fallback: string) => void;
}) {
  const [retry, setRetry] = useState(0);
  const path = `workspaces/${workspaceId}`;
  const projectPath = projectId
    ? `projects/${encodeURIComponent(projectId)}`
    : "";
  const base = workspaceHref(workspaceId);
  const { data: board, setData: setBoard, error, invalidate } = boardResource;
  const questionId = params.questionId ?? params.relatedQuestionId;
  const questionSelected = questionId !== undefined;
  const requestSelected = !!params.requestId;
  const taskRef = params.taskKey;
  const {
    data: taskDetail,
    error: detailError,
    errorCode: detailErrorCode,
    loading: detailLoading,
  } = useResource<Task>(
    taskRef && projectId
      ? `${projectPath}/view/tasks/${encodeURIComponent(taskRef)}`
      : null,
    `${refresh}:${retry}:${pathname}`,
  );
  const historicalMilestone = useResource<Milestone>(
    params.milestoneId && params.milestoneRevision
      ? `${path}/milestones/${params.milestoneId}?revision=${params.milestoneRevision}`
      : null,
    refresh,
  );
  const selectedMilestone = board?.milestones.find(
    (item) => item.id === params.milestoneId || item.key === params.milestoneId,
  );
  const viewedMilestone = params.milestoneRevision
    ? historicalMilestone.data
    : selectedMilestone;
  const selectedTask = board?.tasks.find(
    (t) =>
      t.project_id === projectId && (t.key === taskRef || t.id === taskRef),
  );
  const workspaceSettings =
    pathname === `${base}/edit` ||
    pathname.startsWith(`${base}/edit/`) ||
    ["workers", "coordinator"].includes(params.projectTab ?? "");
  const selection: Selection | null = workspaceSettings
    ? null
    : questionSelected || requestSelected
      ? null
      : taskRef
        ? { kind: "task", id: selectedTask?.key ?? taskRef }
        : params.milestoneId
          ? { kind: "milestone", id: params.milestoneId! }
          : projectId &&
              (pathname === `${projectHref(projectId)}/edit` ||
                pathname.startsWith(`${projectHref(projectId)}/edit/`))
            ? { kind: "project", id: projectId }
            : null;
  const background = pathname;
  const inView = (view: string) =>
    background === `${base}/${view}` ||
    background.startsWith(`${base}/${view}/`) ||
    (!!projectId &&
      (background === `${projectHref(projectId)}/${view}` ||
        background.startsWith(`${projectHref(projectId)}/${view}/`)));
  const projectsView =
    background === `${base}/projects` || background === `${base}/projects/new`;
  const allWorkView = pathname === `${base}/work`;
  const documentsView = pathname.startsWith(`${base}/documents`);
  const addingProject = pathname.endsWith("/projects/new");
  const selectedProject = board?.projects.find(
    (project) => project.id === projectId,
  );
  const location = useLocation();
  const query = new URLSearchParams(location.search);
  const projectFilter = query.get("project") ?? "";
  const labelFilter = query.get("label") ?? "";
  const filter = query.get("milestone") ?? "";
  function changeFilter(name: string, value: string) {
    const next = new URLSearchParams(location.search);
    if (value) next.set(name, value);
    else next.delete(name);
    changeLocation(pathname + (next.size ? "?" + next : ""));
  }
  const milestonesView = inView("milestones");
  const showInbox = inView("inbox");
  const archive = inView("archive");
  const surface = requestSelected
    ? "request"
    : params.milestoneId
      ? "milestone"
      : taskRef
        ? "task"
        : projectsView
          ? "projects"
          : milestonesView
            ? "milestones"
            : showInbox
              ? "inbox"
              : archive
                ? "archive"
                : "board";
  const filtered = surface === "board" || surface === "archive";
  useEffect(() => {
    if (
      filtered &&
      board &&
      labelFilter &&
      !board.labels.some((item) => item.id === labelFilter && !item.deleted_at)
    ) {
      const next = new URLSearchParams(location.search);
      next.delete("label");
      changeLocation(pathname + (next.size ? "?" + next : ""), true);
    }
  }, [filtered, board, labelFilter, location.search, pathname, changeLocation]);
  const adjacentContext: AdjacentContext | null =
    !requestSelected &&
    board &&
    (!taskRef || (taskDetail && !detailError)) &&
    (!params.milestoneId || viewedMilestone)
      ? {
          view: {
            surface,
            project_id:
              surface === "task"
                ? projectId
                : filtered || surface === "inbox"
                  ? projectFilter || null
                  : null,
            milestone_id:
              surface === "milestone"
                ? viewedMilestone?.id
                : filtered && filter && filter !== "none"
                  ? filter
                  : null,
            milestone_revision:
              surface === "milestone" ? viewedMilestone?.revision : null,
            verification_id:
              surface === "milestone"
                ? new URLSearchParams(location.search).get("verification")
                : null,
            without_milestone: filtered && filter === "none",
            label_id: filtered ? labelFilter || null : null,
          },
          task:
            taskRef && taskDetail
              ? {
                  project_id: taskDetail.project_id,
                  task_id: taskDetail.id,
                  task_revision: taskDetail.revision,
                  result_id:
                    params.tab === "result" || params.tab === "changes"
                      ? (params.runId ?? null)
                      : params.tab === "conversation" &&
                          params.runId?.startsWith("result:")
                        ? params.runId.slice(7)
                        : null,
                }
              : null,
        }
      : null;
  const viewSnapshot = JSON.stringify(adjacentContext);
  useEffect(() => {
    if (documentsView || requestSelected) return;
    publishView({
      workspaceId,
      pathname,
      context: JSON.parse(viewSnapshot) as AdjacentContext | null,
    });
  }, [
    workspaceId,
    pathname,
    viewSnapshot,
    publishView,
    documentsView,
    requestSelected,
  ]);
  function selectionUrl(next: Selection | null, archived = archive) {
    if (!next)
      return (
        base +
        (milestonesView ? "/milestones" : archived ? "/archive" : "/tasks") +
        (query.size ? "?" + query : "")
      );
    if (next.kind === "project") return projectHref(next.id!) + "/edit";
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
  function savedProject(item: Project) {
    invalidate();
    setBoard((current) =>
      current
        ? {
            ...current,
            projects: current.projects.map((project) =>
              project.id === item.id ? item : project,
            ),
          }
        : current,
    );
    setUnsaved(false);
    setRetry((value) => value + 1);
  }
  if (!board)
    return (
      <section className="welcome content-stack" data-space="section">
        <h1>{error ? "Could not open workspace" : "Loading board…"}</h1>
        {error && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              boardResource.retry();
              setRetry((v) => v + 1);
            }}
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
  const incoming =
    selection?.kind === "project"
      ? selectedProject
      : selection?.kind === "task"
        ? (taskDetail ?? undefined)
        : viewedMilestone;
  const editorPanel = (
    <>
      {selection?.id &&
        !incoming &&
        (selection.kind === "milestone" && params.milestoneRevision ? (
          <section className="editor">
            <DetailHeader
              title={
                historicalMilestone.error
                  ? "Could not load milestone revision"
                  : "Loading milestone…"
              }
              close={() => choose(null)}
            />
            {historicalMilestone.error && (
              <Button
                size="sm"
                variant="outline"
                onClick={historicalMilestone.retry}
              >
                Retry milestone revision
              </Button>
            )}
          </section>
        ) : selection.kind === "task" && detailLoading && !detailError ? (
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
        incoming &&
        (selection.kind === "project" && incoming ? (
          <ProjectEditor
            workspaceId={board.workspace.id}
            key={selection.id}
            incoming={incoming as Project}
            path={projectPath}
            hasTasks={board.tasks.some((task) => task.project_id === projectId)}
            onDirty={setUnsaved}
            saved={savedProject}
            close={() => choose(null)}
          />
        ) : selection.kind === "milestone" ? (
          <MilestoneDetail
            board={board}
            key={`${selection.id}:${params.milestoneRevision ?? "latest"}`}
            milestone={incoming as Milestone}
            refresh={refresh}
            onDirty={setUnsaved}
            latest={selectedMilestone}
            historical={!!params.milestoneRevision}
            path={path}
            close={() => choose(null)}
          />
        ) : incoming && selectedProject ? (
          <TaskDetail
            workspaceId={board.workspace.id}
            key={selection.id}
            task={incoming as Task}
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
            close={() => choose(null)}
            openTask={(id) => choose({ kind: "task", id })}
          />
        ) : null)}
    </>
  );
  return (
    <LabelCatalog value={board.labels}>
      {requestSelected && (
        <RequestDetail
          publishView={publishView}
          workspaceId={workspaceId}
          kind={params.requestKind!}
          identity={params.requestId!}
          projectId={query.get("project_id") ?? undefined}
          board={board}
          refresh={refresh}
          onDirty={setUnsaved}
          close={() => closeEntity(`${base}/inbox`)}
        />
      )}
      {!selection &&
        !workspaceSettings &&
        !addingProject &&
        !questionSelected &&
        !requestSelected && (
          <div
            className={`${documentsView && params.documentId ? "workspace-detail-content " : ""}workspace-work-content ${!allWorkView && !projectsView && !documentsView && !showInbox && !archive ? "workspace-task-board" : ""}`}
          >
            {!allWorkView && !projectsView && !documentsView && (
              <div className="board-toolbar">
                {!milestonesView && (
                  <div className="actions board-filters">
                    <NativeSelect
                      size="sm"
                      aria-label="Filter by project"
                      value={projectFilter}
                      onChange={(event) =>
                        changeFilter("project", event.target.value)
                      }
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
                        aria-label="Filter by milestone"
                        value={filter}
                        onChange={(e) =>
                          changeFilter("milestone", e.target.value)
                        }
                      >
                        <option value="">All work</option>
                        <option value="none">Without a milestone</option>
                        {board.milestones.map((milestone) => (
                          <option key={milestone.id} value={milestone.id}>
                            {milestone.key} · {milestone.title}
                          </option>
                        ))}
                      </NativeSelect>
                    )}
                    {!showInbox && (
                      <NativeSelect
                        size="sm"
                        aria-label="Filter by label"
                        value={labelFilter}
                        onChange={(event) =>
                          changeFilter("label", event.target.value)
                        }
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
                  </div>
                )}

                <div className="actions">
                  {projectFilter && !milestonesView && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        choose({ kind: "project", id: projectFilter })
                      }
                    >
                      Project settings
                    </Button>
                  )}
                </div>
              </div>
            )}
            {allWorkView ? (
              <WorkList board={board} all />
            ) : projectsView ? (
              <ProjectList
                projects={board.projects}
                problems={board.project_problems}
                workspaceId={workspaceId}
              />
            ) : documentsView ? (
              <Documents
                key={workspaceId}
                workspaceId={workspaceId}
                close={() => closeEntity(`${base}/documents`)}
                identity={params.documentId}
                revision={params.documentRevision}
                pathname={pathname}
                projects={board.projects}
                refresh={refresh}
                onDirty={setUnsaved}
                navigate={changeLocation}
                publishView={publishView}
              />
            ) : milestonesView ? (
              <MilestoneList board={board} />
            ) : showInbox ? (
              <AttentionBoard
                board={board}
                onDirty={setUnsaved}
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
                    onClick={() => {
                      boardResource.retry();
                      setRetry((v) => v + 1);
                    }}
                  >
                    Refresh board
                  </Button>
                )}
                {!board.projects.length && !archive && (
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
                    <ScrollableBoard aria-label="Task board">
                      {columns.map((column) => {
                        const tasks = visible
                          .filter((t) => t.status === column)
                          .sort(compareTasks);
                        return (
                          <section
                            className={`column ${column}`}
                            aria-label={label(column)}
                            key={column}
                          >
                            <h2>
                              {label(column)}
                              <span className="count">{tasks.length}</span>
                            </h2>
                            <div
                              className="column-cards"
                              tabIndex={0}
                              role="region"
                              aria-label={`${label(column)} tasks`}
                            >
                              {tasks.map((task) => (
                                <div className="card-shell" key={task.key}>
                                  <WorkCard>
                                    <TaskIdentity
                                      task={task}
                                      title={
                                        <ScopedAnchor
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
                                        </ScopedAnchor>
                                      }
                                      metadata={
                                        <>
                                          <MilestoneBadge
                                            projectId={task.project_id}
                                            milestone={board.milestones.find(
                                              (m) => m.id === task.milestone_id,
                                            )}
                                          />
                                          <DraftBadge
                                            publicationStatus={
                                              task.publication_status
                                            }
                                          />
                                        </>
                                      }
                                    />
                                    {(task.state?.tone !== "idle" ||
                                      !!task.document_updates?.length ||
                                      !!task.milestone_update) && (
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
                                        pendingCode={
                                          board.pending_code[task.key]
                                        }
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
                                  </WorkCard>
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
                    </ScrollableBoard>
                  )}
                </div>
              </ContentStack>
            )}
          </div>
        )}
      {(selection?.kind === "task" || selection?.kind === "milestone") &&
      selection.id ? (
        <EntityPane
          label={
            selection.kind === "milestone"
              ? "Milestone details"
              : "Task details"
          }
          identity={`${selection.id}:${params.milestoneRevision ?? "latest"}`}
          close={() => choose(null)}
          returnFocusHref={selectionUrl(selection)}
        >
          {editorPanel}
        </EntityPane>
      ) : (
        selection && (
          <EntityPane
            identity={`${selection.kind}:${selection.id}`}
            label="Project details"
            returnFocusHref={selectionUrl(selection)}
            close={() => choose(null)}
          >
            {editorPanel}
          </EntityPane>
        )
      )}
      {addingProject && (
        <EntityPane
          label="Add a project"
          identity="new-project"
          returnFocusHref={base + "/projects"}
          close={() => closeEntity(base + "/projects")}
        >
          <SetupInstructions
            workspaceId={workspaceId}
            onDirty={setUnsaved}
            close={() => closeEntity(base + "/projects")}
            added={(project) => {
              setUnsaved(false);
              setRetry((v) => v + 1);
              changeLocation(
                boardHref(workspaceId, { project: project.id }),
                true,
              );
            }}
          />
        </EntityPane>
      )}
      {workspaceSettings && (
        <EntityPane
          label="Workspace settings"
          identity={workspaceId}
          returnFocusHref={base}
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
        </EntityPane>
      )}
      {questionId && projectId && (
        <QuestionPage
          projectId={projectId}
          identity={questionId}
          tasks={board.tasks.filter((task) => task.project_id === projectId)}
          refresh={refresh}
          onDirty={setUnsaved}
          open={changeLocation}
          close={() =>
            closeEntity(
              params.relatedQuestionId
                ? pathname
                : projectHref(projectId) + "/inbox",
            )
          }
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
        <ul className="work-resource-list">
          {entries.map(({ task, archived }) => (
            <li key={task.key}>
              <ScopedAnchor
                className="work-navigation-row task-card-link"
                href={taskHref(task.project_id, task)}
                aria-current={
                  selection?.kind === "task" && selection.id === task.key
                    ? "page"
                    : undefined
                }
                onClick={(event) =>
                  followLink(event, () =>
                    choose({ kind: "task", id: task.key }),
                  )
                }
              >
                <WorkIdentity
                  state={task.state}
                  itemKey={task.key}
                  title={task.title}
                />
              </ScopedAnchor>
              <div className="content-stack px-2 pb-2">
                {(task.milestone_id ||
                  task.publication_status === "draft" ||
                  task.labels.length > 0) && (
                  <div className="task-labels">
                    <MilestoneBadge
                      milestone={milestones.find(
                        (m) => m.id === task.milestone_id,
                      )}
                    />
                    <DraftBadge publicationStatus={task.publication_status} />
                    <TaskLabels ids={task.labels} />
                  </div>
                )}
                {task.reconciliation_reason && (
                  <span className="readiness blocked">Needs update</span>
                )}
                <ArchiveAttribution
                  author={archived.updated_by}
                  date={archived.updated_at}
                />
              </div>
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
