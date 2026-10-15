import { ResourceScroll } from "./ResourceScroll";
import {
  createContext,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router";
import { ArrowLeft, ArrowRight, ExternalLink, X } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { ActionTooltip } from "./ActionTooltip";
import { canonicalHref, followLink, workspaceHref } from "./navigation";
import type { WorkspaceBoard } from "./workspace";

export type NavigationIntent = "within" | "open";
export const ResourceNavigation = createContext<
  ((url: string, intent?: NavigationIntent) => void) | null
>(null);
export const ResourceTitles = createContext<
  ((url: string, title: string) => void) | null
>(null);
type Tab = { id: string; root: string; history: string[]; index: number };
type TabState = { workspace: string; active: string; tabs: Tab[] };
const limit = 24;
export function resourceRoot(url: string) {
  const path = url.split(/[?#]/)[0];
  return path.replace(
    /(\/tasks\/[^/]+|\/documents\/[^/]+|\/milestones\/[^/]+|\/edit)(?:\/.*)?$/,
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
      root.startsWith(`/projects/${task.project_id}/tasks/`) &&
      (key === task.id || key === task.key),
  );
  if (task) return `/projects/${task.project_id}/tasks/${task.key}`;
  const milestone = board?.milestones.find(
    (milestone) =>
      root.includes("/milestones/") &&
      (key === milestone.id || key === milestone.key),
  );
  return milestone
    ? `${workspaceHref(board!.workspace.id)}/milestones/${milestone.key}`
    : root;
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
    v.tabs[0]?.id === workspaceHref(workspace) + "/inbox" &&
    v.tabs[0]?.root === workspaceHref(workspace) + "/inbox" &&
    new Set(v.tabs.map((tab) => tab.id)).size === v.tabs.length &&
    v.tabs.some((t) => t.id === v.active) &&
    v.tabs.every(
      (t) =>
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
      sessionStorage.getItem(`flowfield:tabs:${workspace}`) ?? "null",
    );
    if (valid(value, workspace)) return value;
  } catch {
    /* Unavailable storage does not prevent navigation. */
  }
  return initial(workspace);
}
function visit(
  state: TabState,
  url: string,
  intent: NavigationIntent,
  replace = false,
  board: WorkspaceBoard | null = null,
): TabState {
  const root = canonicalResourceRoot(url, board);
  let tab =
    intent === "open"
      ? state.tabs.find((t) => canonicalResourceRoot(t.root, board) === root)
      : state.tabs.find((t) => t.id === state.active);
  if (!tab) tab = { id: root, root, history: [url], index: 0 };
  else if (intent === "open")
    tab = {
      ...tab,
      history: root === url ? [root] : [root, url],
      index: root === url ? 0 : 1,
    };
  else if (tab.history[tab.index] !== url) {
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
) {
  const location = useLocation();
  const [saved, setSaved] = useState<TabState | null>(null);
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
  let state =
    workspace && board?.workspace.id === workspace
      ? saved?.workspace === workspace
        ? saved
        : load(workspace)
      : null;
  if (workspace && state) {
    if (valid(incoming, workspace)) state = incoming;
    const active = state.tabs.find((t) => t.id === state?.active);
    if (active?.history[active.index] !== path)
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
        sessionStorage.setItem(`flowfield:tabs:${next.workspace}`, serialized);
      } catch {
        /* Optional preferences. */
      }
  }, [serialized]);
  const go = useCallback(
    (url: string, intent: NavigationIntent = "within", replace = false) => {
      url = canonicalHref(url, owners);
      const s = JSON.parse(serialized) as TabState | null;
      const next = s ? visit(s, url, intent, replace, board) : null;
      change(url, replace, next ? { workspaceTabs: next } : undefined);
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
        if (id === state.tabs[0].id) go(tab.root, "open");
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
  root: string,
  board: WorkspaceBoard | null,
  titles: Record<string, string>,
) {
  if (titles[root]) return titles[root];
  const key = decodeURIComponent(root.split("/").at(-1) ?? "");
  const task = board?.tasks.find(
    (t) =>
      root.startsWith(`/projects/${t.project_id}/tasks/`) &&
      (key === t.key || key === t.id),
  );
  if (task) return `${task.key} ${task.title}`;
  const milestone = board?.milestones.find(
    (m) => root.includes("/milestones/") && (key === m.key || key === m.id),
  );
  if (milestone) return `${milestone.key} ${milestone.title}`;
  return (
    (
      {
        inbox: "Inbox",
        documents: "Documents",
        projects: "Projects",
        milestones: "Milestones",
        archive: "Archive",
        work: "All work",
        edit: "Settings",
        new: "Add project",
      } as Record<string, string>
    )[key] ??
    (root.includes("/documents/")
      ? "Document"
      : root.includes("/tasks/")
        ? key
        : "Board")
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
  const strip = useRef<HTMLDivElement>(null);
  useEffect(() => {
    strip.current
      ?.querySelector<HTMLElement>('[data-state="active"]')
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [state?.active]);
  if (!state) return children;
  const active = state.tabs.find((t) => t.id === state.active)!;
  return (
    <ResourceTitles.Provider value={controller.labelResource}>
      <Tabs
        className="workspace-resource-tabs"
        value={state.active}
        onValueChange={controller.select}
        activationMode="manual"
      >
        <div className="workspace-tab-strip" ref={strip}>
          <TabsList variant="line" aria-label="Open resources">
            {state.tabs.map((tab, index) => (
              <div className="workspace-tab" key={tab.id}>
                <ActionTooltip
                  label={title(tab.root, board, controller.titles)}
                >
                  <TabsTrigger
                    value={tab.id}
                    aria-label={
                      index === 0 && board?.needs_you_count
                        ? `Inbox ${board.needs_you_count}`
                        : undefined
                    }
                    data-state={tab.id === state.active ? "active" : "inactive"}
                    data-slot="tabs-trigger"
                    onClick={() => {
                      if (
                        index === 0 &&
                        tab.id === state.active &&
                        tab.history[tab.index] !== tab.root
                      )
                        controller.go(tab.root, "open");
                    }}
                  >
                    <span>{title(tab.root, board, controller.titles)}</span>
                    {index === 0 && !!board?.needs_you_count && (
                      <span className="nav-count needs-attention">
                        {Math.min(99, board.needs_you_count)}
                        {board.needs_you_count > 99 ? "+" : ""}
                      </span>
                    )}
                    {tab.root.endsWith("/documents") && board && (
                      <span className="nav-count"> {board.document_count}</span>
                    )}
                  </TabsTrigger>
                </ActionTooltip>
                {index > 0 && (
                  <ActionTooltip
                    label={`Close ${title(tab.root, board, controller.titles)}`}
                  >
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`Close ${title(tab.root, board, controller.titles)}`}
                      onClick={() => {
                        controller.close(tab.id);
                        requestAnimationFrame(() =>
                          strip.current
                            ?.querySelector<HTMLElement>(
                              '[data-state="active"]',
                            )
                            ?.focus(),
                        );
                      }}
                    >
                      <X />
                    </Button>
                  </ActionTooltip>
                )}
              </div>
            ))}
          </TabsList>
        </div>
        {active.history.length > 1 && (
          <nav className="workspace-tab-history" aria-label="Resource history">
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Back in tab"
              disabled={active.index === 0}
              onClick={() => controller.step(-1)}
            >
              <ArrowLeft />
            </Button>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Forward in tab"
              disabled={active.index === active.history.length - 1}
              onClick={() => controller.step(1)}
            >
              <ArrowRight />
            </Button>
            <Button size="sm" variant="ghost" asChild>
              <a
                href={active.history[active.index]}
                onClick={(event) =>
                  followLink(event, () =>
                    controller.go(active.history[active.index], "open"),
                  )
                }
              >
                <ExternalLink /> Open in work tab
              </a>
            </Button>
          </nav>
        )}
        <TabsContent
          value={state.active}
          className="workspace-resource-content"
        >
          <ResourceScroll tab={state.active}>{children}</ResourceScroll>
        </TabsContent>
      </Tabs>
    </ResourceTitles.Provider>
  );
}
