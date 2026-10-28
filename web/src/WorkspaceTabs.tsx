import { useTabOverflow } from "./useTabOverflow";
import { ResourceScroll } from "./ResourceScroll";
import {
  createContext,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router";
import { X } from "lucide-react";
import { DragDropProvider, PointerSensor } from "@dnd-kit/react";
import { Accessibility, PointerActivationConstraints } from "@dnd-kit/dom";
import { useSortable, isSortable } from "@dnd-kit/react/sortable";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";
import { canonicalHref, workspaceHref, workspacePaths } from "./navigation";
import { matchPath } from "react-router";
import { request, type WorkspaceBoard } from "./workspace";

export type NavigationIntent = "within" | "open";
export const ResourceNavigation = createContext<
  ((url: string, intent?: NavigationIntent) => void) | null
>(null);
export const ResourceTitles = createContext<
  ((url: string, title: string) => void) | null
>(null);
export const ResourceBackAvailable = createContext(true);
type Tab = {
  id: string;
  root: string;
  history: string[];
  index: number;
};
type TabState = { workspace: string; active: string; tabs: Tab[] };
const limit = 24;
function isInboxView(url: string, root: string) {
  const target = new URL(url, "http://flowfield.local");
  return target.pathname === root;
}
export function resourceRoot(url: string) {
  const path = url.split(/[?#]/)[0];
  if (path.includes("/requests/") || path.includes("/results/")) {
    const project = new URL(url, "http://flowfield.local").searchParams.get(
      "project_id",
    );
    return path + (project ? `?project_id=${encodeURIComponent(project)}` : "");
  }
  if (path.startsWith("/settings/")) return "/settings";
  return path.replace(
    /(\/tasks\/[^/]+|\/documents\/[^/]+|\/edit)(?:\/.*)?$/,
    "$1",
  );
}
export function canonicalResourceRoot(
  url: string,
  board: WorkspaceBoard | null,
) {
  const root = resourceRoot(url);
  const key = decodeURIComponent(root.split("/").at(-1) ?? "");
  const task = board?.tasks.find(
    (task) =>
      root.startsWith(`/workspaces/${task.workspace_id}/tasks/`) &&
      (key === task.id || key === task.key),
  );
  if (task) return `/workspaces/${task.workspace_id}/tasks/${task.key}`;
  return root;
}
function exactView(url: string, board: WorkspaceBoard | null) {
  const view = new URL(url, "http://flowfield.local");
  const match = view.pathname.match(
    /^(\/workspaces\/[^/]+\/tasks\/)([^/]+)(.*)$/,
  );
  if (match) {
    const task = board?.tasks.find(
      (task) =>
        task.id === decodeURIComponent(match[2]) ||
        task.key === decodeURIComponent(match[2]),
    );
    if (task) view.pathname = match[1] + task.key + match[3];
  }
  view.searchParams.sort();
  return view.pathname + view.search + view.hash;
}
function initial(workspace: string): TabState {
  const root = workspaceHref(workspace) + "/inbox";
  return {
    workspace,
    active: root,
    tabs: [{ id: root, root, history: [root], index: 0 }],
  };
}
function valid(value: unknown, workspace: string): value is TabState {
  if (!value || typeof value !== "object") return false;
  const v = value as TabState;
  return (
    v.workspace === workspace &&
    Array.isArray(v.tabs) &&
    v.tabs.length > 0 &&
    v.tabs.length <= limit &&
    v.tabs.every((tab) => !!tab && typeof tab === "object") &&
    v.tabs[0]?.id === workspaceHref(workspace) + "/inbox" &&
    v.tabs[0]?.root === workspaceHref(workspace) + "/inbox" &&
    new Set(v.tabs.map((tab) => tab.id)).size === v.tabs.length &&
    v.tabs.some((t) => t.id === v.active) &&
    v.tabs.every(
      (t) =>
        !!t &&
        typeof t.id === "string" &&
        typeof t.root === "string" &&
        t.root.startsWith("/") &&
        !t.root.startsWith("//") &&
        Array.isArray(t.history) &&
        t.history.length > 0 &&
        t.history.length <= 40 &&
        t.history.every(
          (p) =>
            typeof p === "string" &&
            p.startsWith("/") &&
            !p.startsWith("//") &&
            !p.includes("\\") &&
            p.length < 4096,
        ) &&
        Number.isInteger(t.index) &&
        t.index >= 0 &&
        t.index < t.history.length,
    )
  );
}
function load(workspace: string) {
  try {
    const value: unknown = JSON.parse(
      localStorage.getItem(`flowfield:tabs:${workspace}`) ?? "null",
    );
    if (valid(value, workspace)) return value;
  } catch {
    /* Unavailable storage does not prevent navigation. */
  }
  return initial(workspace);
}
function knownLocation(url: string, workspace: string) {
  try {
    const path = new URL(url, "http://flowfield.local").pathname;
    return workspacePaths.some((pattern) => {
      const match = matchPath(pattern, path);
      return (
        !!match &&
        (!match.params.workspaceId || match.params.workspaceId === workspace)
      );
    });
  } catch {
    return false;
  }
}
function visit(
  state: TabState,
  url: string,
  intent: NavigationIntent,
  replace = false,
  board: WorkspaceBoard | null = null,
): TabState {
  const root = canonicalResourceRoot(url, board);
  const inbox = state.tabs[0];
  if (
    intent === "within" &&
    state.active === inbox.id &&
    !isInboxView(url, inbox.root)
  )
    intent = "open";
  if (isInboxView(url, inbox.root)) {
    const updated = { ...inbox, history: [url], index: 0 };
    return {
      ...state,
      active: inbox.id,
      tabs: [updated, ...state.tabs.slice(1)],
    };
  }
  let tab =
    intent === "open"
      ? state.tabs.find(
          (t) => exactView(t.history[t.index], board) === exactView(url, board),
        )
      : state.tabs.find((t) => t.id === state.active);
  if (!tab)
    tab = {
      id: state.tabs.some((t) => t.id === root) ? crypto.randomUUID() : root,
      root,
      history: [url],
      index: 0,
    };
  else if (intent !== "open" && tab.history[tab.index] !== url) {
    const history = [
      ...tab.history.slice(0, tab.index + (replace ? 0 : 1)),
      url,
    ].slice(-40);
    tab = { ...tab, history, index: history.length - 1 };
  }
  let tabs = state.tabs.some((t) => t.id === tab.id)
    ? state.tabs.map((t) => (t.id === tab.id ? tab : t))
    : [...state.tabs, tab];
  if (tabs.length > limit) tabs = [tabs[0], ...tabs.slice(-(limit - 1))];
  return { ...state, active: tab.id, tabs };
}

export function useWorkspaceTabs(
  workspace: string | undefined,
  change: (
    url: string,
    replace?: boolean,
    state?: Record<string, unknown>,
  ) => void,
  owners: Record<string, string>,
  board: WorkspaceBoard | null,
  refresh: unknown,
) {
  const location = useLocation();
  const [saved, setSaved] = useState<TabState | null>(null);
  const [restore, setRestore] = useState<string | null>(null);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const labelResource = useCallback((url: string, title: string) => {
    const root = resourceRoot(url);
    setTitles((previous) =>
      previous[root] === title
        ? previous
        : Object.fromEntries(
            [
              ...Object.entries(previous).filter(([key]) => key !== root),
              [root, title],
            ].slice(-128),
          ),
    );
  }, []);
  const path = canonicalHref(
    location.pathname + location.search + location.hash,
    owners,
  );
  const incoming = location.state?.workspaceTabs as unknown;
  if (restore && path === restore) setRestore(null);
  let state =
    workspace && board?.workspace.id === workspace
      ? saved?.workspace === workspace
        ? saved
        : load(workspace)
      : null;
  if (workspace && state) {
    if (valid(incoming, workspace)) state = incoming;
    const active = state.tabs.find((t) => t.id === state?.active);
    const restoring =
      saved?.workspace !== workspace &&
      !incoming &&
      path === workspaceHref(workspace) + "/inbox" &&
      active?.id !== state.tabs[0].id
        ? (active?.history[active.index] ?? null)
        : restore;
    if (restoring !== restore) setRestore(restoring);
    if (!restoring && active?.history[active.index] !== path)
      state = visit(
        state,
        path,
        valid(incoming, workspace) ? "within" : "open",
        false,
        board,
      );
  }
  const serialized = JSON.stringify(state);
  if (JSON.stringify(saved) !== serialized) setSaved(state);
  useEffect(() => {
    const next = JSON.parse(serialized) as TabState | null;
    if (next)
      try {
        localStorage.setItem(`flowfield:tabs:${next.workspace}`, serialized);
      } catch {
        /* Optional preferences. */
      }
  }, [serialized]);
  useLayoutEffect(() => {
    if (!restore || restore === path) return;
    change(restore, true, { workspaceTabs: JSON.parse(serialized) });
  }, [change, serialized, restore, path]);
  useLayoutEffect(() => {
    // Direct entries need the same history receipt as in-app navigation. Without
    // it, browser Back invents a new tab and loses that tab's scroll/focus state.
    if (
      workspace &&
      !restore &&
      serialized !== "null" &&
      !valid(incoming, workspace)
    )
      change(path, true, {
        ...location.state,
        workspaceTabs: JSON.parse(serialized),
      });
  }, [workspace, restore, serialized, incoming, change, path, location.state]);
  // One bounded shell read checks inactive tabs too. No content mounts, result
  // preparation or streams are needed. A failed read never means deletion.
  const latest = useRef(state);
  useLayoutEffect(() => {
    latest.current = JSON.parse(serialized) as TabState | null;
  }, [serialized]);
  const locations = JSON.stringify(
    state?.tabs.map((tab) => tab.history[tab.index]) ?? [],
  );
  useEffect(() => {
    if (!workspace || locations === "[]") return;
    const controller = new AbortController();
    let reading = false;
    async function reconcile() {
      if (reading || controller.signal.aborted) return;
      reading = true;
      try {
        const urls = JSON.parse(locations) as string[];
        const params = new URLSearchParams();
        urls
          .filter((url) => knownLocation(url, workspace!))
          .forEach((url) => params.append("location", url));
        const present = await request<Record<string, boolean>>(
          `workspaces/${workspace}/view/resource-presence?${params}`,
          "GET",
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        const missing = new Set(
          urls.filter(
            (url) => !knownLocation(url, workspace!) || present[url] === false,
          ),
        );
        const current = latest.current;
        if (!missing.size || !current || current.workspace !== workspace)
          return;
        const tabs = current.tabs
          .filter(
            (tab, index) => index === 0 || !missing.has(tab.history[tab.index]),
          )
          .map((tab) => {
            const history = tab.history.filter((url) => !missing.has(url));
            const index = tab.history
              .slice(0, tab.index)
              .filter((url) => !missing.has(url)).length;
            return { ...tab, history, index };
          });
        const active = tabs.find((tab) => tab.id === current.active) ?? tabs[0];
        const next = { ...current, tabs, active: active.id };
        if (JSON.stringify(next) !== JSON.stringify(current))
          change(active.history[active.index], active.id === current.active, {
            workspaceTabs: next,
          });
      } catch {
        // Connection recovery and the next workspace update retry this check.
      } finally {
        reading = false;
      }
    }
    void reconcile();
    window.addEventListener("focus", reconcile);
    window.addEventListener("flowfield:service-recovered", reconcile);
    return () => {
      controller.abort();
      window.removeEventListener("focus", reconcile);
      window.removeEventListener("flowfield:service-recovered", reconcile);
    };
  }, [workspace, locations, refresh, change]);
  const go = useCallback(
    (url: string, intent: NavigationIntent = "within", replace = false) => {
      url = canonicalHref(url, owners);
      const s = JSON.parse(serialized) as TabState | null;
      const next = s ? visit(s, url, intent, replace, board) : null;
      const selected = next?.tabs.find((tab) => tab.id === next.active);
      change(
        selected ? selected.history[selected.index] : url,
        replace,
        next ? { workspaceTabs: next } : undefined,
      );
    },
    [change, serialized, owners, board],
  );
  const move = (next: TabState, url: string) =>
    change(url, false, { workspaceTabs: next });
  return {
    state,
    titles,
    labelResource,
    go,
    select(id: string) {
      const tab = state?.tabs.find((t) => t.id === id);
      if (state && tab) {
        // Older saved tabs may have navigated a resource inside pinned Inbox.
        if (
          id === state.tabs[0].id &&
          !isInboxView(tab.history[tab.index], tab.root)
        )
          go(tab.root, "open");
        else move({ ...state, active: id }, tab.history[tab.index]);
      }
    },
    close(id: string) {
      if (!state || id === state.tabs[0].id) return;
      const tabs = state.tabs.filter((t) => t.id !== id);
      const active =
        state.active === id
          ? tabs[Math.max(0, state.tabs.findIndex((t) => t.id === id) - 1)]
          : tabs.find((t) => t.id === state.active)!;
      move({ ...state, tabs, active: active.id }, active.history[active.index]);
    },
    reorder(from: number, to: number) {
      if (
        !state ||
        from < 1 ||
        to < 1 ||
        from >= state.tabs.length ||
        to >= state.tabs.length
      )
        return;
      const tabs = [...state.tabs];
      const [tab] = tabs.splice(from, 1);
      tabs.splice(to, 0, tab);
      const active = tabs.find((tab) => tab.id === state.active)!;
      change(active.history[active.index], true, {
        workspaceTabs: { ...state, tabs },
      });
    },
    step(delta: number) {
      const tab = state?.tabs.find((t) => t.id === state.active);
      if (
        !state ||
        !tab ||
        tab.index + delta < 0 ||
        tab.index + delta >= tab.history.length
      )
        return;
      const next = { ...tab, index: tab.index + delta };
      move(
        { ...state, tabs: state.tabs.map((t) => (t.id === tab.id ? next : t)) },
        next.history[next.index],
      );
    },
  };
}
export type WorkspaceTabsController = ReturnType<typeof useWorkspaceTabs>;
function title(
  url: string,
  board: WorkspaceBoard | null,
  titles: Record<string, string>,
) {
  const location = new URL(url, "http://flowfield.local");
  const root = canonicalResourceRoot(url, board);
  if (root.includes("/results/")) return titles[root] ?? "Result";
  if (root.includes("/requests/")) return titles[root] ?? "Request";
  const settings = location.pathname.endsWith("/settings");
  const suffix = settings
    ? " · Settings"
    : location.pathname.endsWith("/edit") && root.includes("/documents/")
      ? " · Edit"
      : "";
  if (titles[root]) return titles[root] + suffix;
  const key = decodeURIComponent(root.split("/").at(-1) ?? "");
  const task = board?.tasks.find(
    (t) =>
      root.startsWith(`/workspaces/${t.workspace_id}/tasks/`) &&
      (key === t.key || key === t.id),
  );
  if (task)
    return `${task.key} ${location.pathname.endsWith("/settings") ? "Settings" : task.title}`;
  const project = board?.projects.find(
    (p) => location.pathname === `/projects/${p.id}/overview`,
  );
  if (project) return project.name;
  return (
    (
      {
        inbox: "Inbox",
        tasks: "Task Board",
        documents: "Documents",
        files: "Files",
        usage: "Usage",
        projects: "Projects",
        archive: "Archive",
        settings: "Flowfield settings",
        "new-workspace": "New workspace",
        edit: root.startsWith("/projects/")
          ? "Project settings"
          : "Workspace settings",
        new: "Add project",
      } as Record<string, string>
    )[key] ??
    (root.includes("/documents/")
      ? "Document"
      : root.includes("/tasks/")
        ? key
        : "Task Board")
  );
}
function ResourceTab({
  tab,
  index,
  controller,
  board,
  restoreFocus,
}: {
  tab: Tab;
  index: number;
  controller: WorkspaceTabsController;
  board: WorkspaceBoard | null;
  restoreFocus: () => void;
}) {
  const { ref, handleRef, isDragging } = useSortable({
    id: tab.id,
    index,
    disabled: index === 0,
  });
  const name =
    index === 0
      ? "Inbox"
      : title(tab.history[tab.index], board, controller.titles);
  const label = useRef<HTMLSpanElement>(null);
  const [overflowing, setOverflowing] = useState(false);
  useLayoutEffect(() => {
    const node = label.current;
    if (!node) return;
    const measure = () => setOverflowing(node.scrollWidth > node.clientWidth);
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    measure();
    return () => observer.disconnect();
  }, [name]);
  const active = tab.id === controller.state?.active;
  return (
    <div
      ref={index > 0 ? ref : undefined}
      className="workspace-tab"
      data-active={active}
      data-dragging={isDragging}
    >
      <Tooltip open={overflowing ? undefined : false} disableHoverableContent>
        <TooltipTrigger asChild>
          <TabsTrigger
            ref={index > 0 ? handleRef : undefined}
            value={tab.id}
            aria-label={
              index === 0 && board?.needs_you_count
                ? "Inbox " + board.needs_you_count
                : name
            }
            data-state={active ? "active" : "inactive"}
            data-slot="tabs-trigger"
            onClick={() => {
              if (!active) controller.select(tab.id);
              else if (
                index === 0 &&
                !isInboxView(tab.history[tab.index], tab.root)
              )
                controller.go(tab.root, "open");
            }}
          >
            <span ref={label}>{name}</span>{" "}
            {index === 0 && !!board?.needs_you_count && (
              <span className="nav-count needs-attention border">
                {Math.min(99, board.needs_you_count)}
                {board.needs_you_count > 99 ? "+" : ""}
              </span>
            )}
          </TabsTrigger>
        </TooltipTrigger>
        <TooltipContent>{name}</TooltipContent>
      </Tooltip>
      {index > 0 && (
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={"Close " + name}
          onClick={() => {
            controller.close(tab.id);
            restoreFocus();
          }}
        >
          <X />
        </Button>
      )}
    </div>
  );
}
export function WorkspaceTabs({
  controller,
  board,
  children,
}: {
  controller: WorkspaceTabsController;
  board: WorkspaceBoard | null;
  children: ReactNode;
}) {
  const state = controller.state;
  const { strip, overflow } = useTabOverflow(
    state,
    state?.active,
    '.workspace-tab[data-active="true"]',
  );
  const restoreFocus = () =>
    requestAnimationFrame(() =>
      strip.current
        ?.querySelector<HTMLElement>('[role="tab"][data-state="active"]')
        ?.focus(),
    );
  if (!state) return children;
  return (
    <ResourceTitles.Provider value={controller.labelResource}>
      <ResourceBackAvailable.Provider
        value={state.tabs.find((tab) => tab.id === state.active)!.index > 0}
      >
        <Tabs
          className="workspace-resource-tabs"
          value={state.active}
          onValueChange={controller.select}
          activationMode="manual"
        >
          <DragDropProvider
            sensors={[
              PointerSensor.configure({
                activationConstraints: [
                  new PointerActivationConstraints.Distance({ value: 6 }),
                ],
              }),
            ]}
            plugins={(defaults) =>
              defaults.map((plugin) =>
                plugin === Accessibility
                  ? Accessibility.configure({
                      screenReaderInstructions: {
                        draggable: "",
                      },
                    })
                  : plugin,
              )
            }
            onDragEnd={(event) => {
              if (event.canceled) return;
              const { source } = event.operation;
              if (isSortable(source))
                controller.reorder(source.initialIndex, source.index);
            }}
          >
            <div
              className="workspace-tab-strip tab-overflow"
              ref={strip}
              data-overflow-left={overflow.left}
              data-overflow-right={overflow.right}
            >
              <TooltipProvider delayDuration={400} skipDelayDuration={0}>
                <TabsList variant="line" aria-label="Open resources">
                  {state.tabs.map((tab, index) => (
                    <ResourceTab
                      key={tab.id}
                      tab={tab}
                      index={index}
                      controller={controller}
                      board={board}
                      restoreFocus={restoreFocus}
                    />
                  ))}
                </TabsList>
              </TooltipProvider>
            </div>
          </DragDropProvider>
          <TabsContent
            value={state.active}
            className="workspace-resource-content"
          >
            <ResourceScroll tab={state.active}>{children}</ResourceScroll>
          </TabsContent>
        </Tabs>
      </ResourceBackAvailable.Provider>
    </ResourceTitles.Provider>
  );
}
