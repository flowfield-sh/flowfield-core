import { ResourceScroll } from "./ResourceScroll";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router";
import { ExternalLink, X } from "lucide-react";
import { DragDropProvider, PointerSensor } from "@dnd-kit/react";
import { Accessibility, PointerActivationConstraints } from "@dnd-kit/dom";
import { useSortable, isSortable } from "@dnd-kit/react/sortable";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { ActionTooltip } from "./ActionTooltip";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";
import { canonicalHref, followLink, workspaceHref } from "./navigation";
import type { WorkspaceBoard } from "./workspace";

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
  // Run result opens a disclosure on the same result; it is not another view.
  if (view.pathname.includes("/results/")) view.searchParams.delete("run");
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
  else if (intent === "open") {
    const target = new URL(url, "http://flowfield.local");
    const currentIndex = tab.index;
    if (
      target.pathname.includes("/results/") &&
      target.searchParams.get("run") === "1"
    )
      tab = {
        ...tab,
        history: tab.history.map((value, index) =>
          index === currentIndex ? url : value,
        ),
      };
  } else if (tab.history[tab.index] !== url) {
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
export function OpenInWorkTab({
  href: target,
  label = "Open in work tab",
}: { href?: string; label?: string } = {}) {
  const location = useLocation();
  const open = useContext(ResourceNavigation);
  if (!open) return null;
  const href = target ?? location.pathname + location.search + location.hash;
  return (
    <ActionTooltip label={label}>
      <Button size="icon-sm" variant="ghost" asChild>
        <a
          href={href}
          aria-label={label}
          onClick={(event) => followLink(event, () => open(href, "open"))}
        >
          <ExternalLink />
        </a>
      </Button>
    </ActionTooltip>
  );
}
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
  const suffix = settings ? " · Settings" : "";
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
        tasks: "Tasks Board",
        documents: "Documents",
        files: "Files",
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
        : "Tasks Board")
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
  const currentRoot = canonicalResourceRoot(tab.history[tab.index], board);
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
            onKeyDown={(event) => {
              if (
                index > 0 &&
                event.altKey &&
                ["ArrowLeft", "ArrowRight"].includes(event.key)
              ) {
                event.preventDefault();
                event.stopPropagation();
                controller.reorder(
                  index,
                  index + (event.key === "ArrowLeft" ? -1 : 1),
                );
              }
              if (index > 0 && event.key === "Delete") {
                event.preventDefault();
                controller.close(tab.id);
                restoreFocus();
              }
            }}
          >
            <span ref={label}>{name}</span>{" "}
            {index === 0 && !!board?.needs_you_count && (
              <span className="nav-count needs-attention">
                {Math.min(99, board.needs_you_count)}
                {board.needs_you_count > 99 ? "+" : ""}
              </span>
            )}
            {currentRoot.endsWith("/documents") && board && (
              <span className="nav-count">{board.document_count}</span>
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
  const strip = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState({ left: false, right: false });
  useLayoutEffect(() => {
    const host = strip.current;
    const list = host?.querySelector<HTMLElement>('[role="tablist"]');
    if (!host || !list) return;
    const measure = () => {
      const left = host.scrollLeft > 1;
      const right = host.scrollWidth - host.clientWidth - host.scrollLeft > 1;
      setOverflow((current) =>
        current.left === left && current.right === right
          ? current
          : { left, right },
      );
    };
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY))
        return;
      if (host.scrollWidth <= host.clientWidth) return;
      event.preventDefault();
      const unit =
        event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? host.clientWidth
            : 1;
      host.scrollLeft += event.deltaY * unit;
    };
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    observer.observe(list);
    host.addEventListener("scroll", measure);
    host.addEventListener("wheel", wheel, { passive: false });
    measure();
    return () => {
      observer.disconnect();
      host.removeEventListener("scroll", measure);
      host.removeEventListener("wheel", wheel);
    };
  }, [state]);
  const restoreFocus = () =>
    requestAnimationFrame(() =>
      strip.current
        ?.querySelector<HTMLElement>('[role="tab"][data-state="active"]')
        ?.focus(),
    );
  useLayoutEffect(() => {
    const host = strip.current;
    const active = host?.querySelector<HTMLElement>(
      '.workspace-tab[data-active="true"]',
    );
    if (!host || !active) return;
    const reveal = () =>
      active.scrollIntoView({ block: "nearest", inline: "nearest" });
    const observer = new ResizeObserver(reveal);
    observer.observe(host);
    observer.observe(active);
    reveal();
    return () => observer.disconnect();
  }, [state?.active]);
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
                        draggable:
                          "Use Enter to select, Alt+Left or Alt+Right to reorder, and Delete to close this tab.",
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
              className="workspace-tab-strip"
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
