import {
  useState,
  useEffect,
  useLayoutEffect,
  useRef,
  useSyncExternalStore,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Home } from "lucide-react";
import { usePanelRef } from "react-resizable-panels";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import "./workspace-frame.css";
import { followLink } from "./navigation";
import { SettingsLink } from "./FlowfieldSettings";
import { ActionTooltip } from "./ActionTooltip";

export type WorkspaceEntry = {
  id: string;
  name: string;
  href: string;
  inboxCount: number;
};
const compactQuery = "(max-width: 1023px)";
function subscribeCompact(callback: () => void) {
  const query = window.matchMedia(compactQuery);
  query.addEventListener("change", callback);
  return () => query.removeEventListener("change", callback);
}

function WorkspaceNavigation({
  workspaces,
  activeWorkspaceId,
  onWorkspaceSelect,
  footer,
  onHome,
  settingsActive,
  navigation,
  queue,
}: {
  workspaces: WorkspaceEntry[];
  activeWorkspaceId: string;
  onWorkspaceSelect: (workspace: WorkspaceEntry) => void;
  footer?: ReactNode;
  onHome?: () => void;
  settingsActive?: boolean;
  navigation?: ReactNode;
  queue?: ReactNode;
}) {
  const { setOpenMobile, isMobile } = useSidebar();
  return (
    <Sidebar collapsible="offcanvas">
      <SidebarHeader className="workspace-sidebar-header">
        <div className="workspace-global-actions">
          <ActionTooltip label="Home">
            <Button asChild size="icon-sm" variant="ghost" aria-label="Home">
              <a
                href="/"
                onClick={(event) =>
                  followLink(event, () => {
                    onHome?.();
                    setOpenMobile(false);
                  })
                }
              >
                <Home />
              </a>
            </Button>
          </ActionTooltip>
          <SettingsLink active={!!settingsActive} />
          {isMobile && <SidebarTrigger aria-label="Close navigation" />}
        </div>
        <NativeSelect
          size="sm"
          aria-label="Workspace"
          className="bg-background dark:bg-background"
          displayValue={
            workspaces.find((workspace) => workspace.id === activeWorkspaceId)
              ?.name ?? "Workspaces"
          }
          value={activeWorkspaceId}
          onChange={(event) => {
            const workspace = workspaces.find(
              (w) => w.id === event.target.value,
            );
            if (workspace) onWorkspaceSelect(workspace);
            setOpenMobile(false);
          }}
        >
          {!activeWorkspaceId && <option value="">Workspaces</option>}
          {workspaces.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
              {w.inboxCount ? " (" + w.inboxCount + ")" : ""}
            </option>
          ))}
        </NativeSelect>
      </SidebarHeader>
      <SidebarContent
        className="workspace-sidebar-content"
        onClick={(event) => {
          if ((event.target as HTMLElement).closest("a")) setOpenMobile(false);
        }}
      >
        {navigation}
      </SidebarContent>
      <SidebarFooter>
        {queue}
        {footer}
      </SidebarFooter>
    </Sidebar>
  );
}

// Owns layout only. Conversation state, navigation and work remain with their callers.
export function WorkspaceFrame({
  workspaces,
  activeWorkspaceId,
  onWorkspaceSelect,
  coordinator,
  work,
  footer,
  onHome,
  settingsActive,
  workLocation,
  navigation,
  queue,
}: {
  workspaces: WorkspaceEntry[];
  activeWorkspaceId: string;
  onWorkspaceSelect: (workspace: WorkspaceEntry) => void;
  workLocation?: string;
  navigation?: ReactNode;
  queue?: ReactNode;
  coordinator?: ReactNode | ((visible: boolean) => ReactNode);
  work: ReactNode;
  footer?: ReactNode;
  onHome?: () => void;
  settingsActive?: boolean;
}) {
  const compact = useSyncExternalStore(
    subscribeCompact,
    () => window.matchMedia(compactQuery).matches,
    () => false,
  );
  // Changing defaultSize during a drag re-registers the panel and ends the drag.
  // The library owns live sizing; this value is only the initial preference.
  const [initialWidth] = useState(() => {
    try {
      return Math.max(
        360,
        Math.min(
          800,
          Number(localStorage.getItem("flowfield:coordinator-width")) || 440,
        ),
      );
    } catch {
      return 440;
    }
  });
  const coordinatorPanel = usePanelRef();
  const desktopWidth = useRef(initialWidth);
  const wasCompact = useRef(compact);
  useLayoutEffect(() => {
    if (wasCompact.current && !compact)
      coordinatorPanel.current?.resize(desktopWidth.current);
    wasCompact.current = compact;
  }, [compact, coordinatorPanel]);
  const [surface, setSurface] = useState("work");
  useEffect(() => {
    const show = () => setSurface("coordinator");
    window.addEventListener("flowfield:show-coordinator", show);
    return () => window.removeEventListener("flowfield:show-coordinator", show);
  }, []);
  const [location, setLocation] = useState(workLocation);
  if (location !== workLocation) {
    setLocation(workLocation);
    setSurface("work");
  }
  return (
    <SidebarProvider
      defaultOpen
      className="workspace-frame"
      style={
        {
          "--sidebar-width": "260px",
          "--sidebar-width-icon": "3.5rem",
        } as CSSProperties
      }
    >
      <WorkspaceNavigation
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        onWorkspaceSelect={onWorkspaceSelect}
        footer={footer}
        navigation={navigation}
        queue={queue}
        onHome={onHome}
        settingsActive={settingsActive}
      />
      <main className="workspace-main">
        {coordinator == null ? (
          <div className="workspace-pane workspace-work-only">
            <div className="workspace-mobile-bar md:hidden">
              <SidebarTrigger aria-label="Open workspaces" />
              <span>Flowfield</span>
            </div>
            <div className="workspace-standalone-content">{work}</div>
          </div>
        ) : (
          <Tabs
            value={surface}
            onValueChange={setSurface}
            className="workspace-surfaces"
          >
            {compact && (
              <div className="workspace-surface-switch">
                <SidebarTrigger
                  className="md:hidden"
                  aria-label="Open workspaces"
                />
                <TabsList aria-label="Workspace surface">
                  <TabsTrigger value="coordinator">Coordinator</TabsTrigger>
                  <TabsTrigger value="work">Work</TabsTrigger>
                </TabsList>
              </div>
            )}
            <ResizablePanelGroup
              orientation="horizontal"
              disabled={compact}
              className="workspace-panels"
            >
              <ResizablePanel
                id="coordinator"
                panelRef={coordinatorPanel}
                defaultSize={`${initialWidth}px`}
                onResize={(size) => {
                  if (!compact && !wasCompact.current) {
                    desktopWidth.current = size.inPixels;
                    try {
                      localStorage.setItem(
                        "flowfield:coordinator-width",
                        String(size.inPixels),
                      );
                    } catch {
                      /* Optional preference. */
                    }
                  }
                }}
                minSize={
                  compact
                    ? surface === "coordinator"
                      ? "100%"
                      : "0%"
                    : "360px"
                }
                maxSize={
                  compact
                    ? surface === "coordinator"
                      ? "100%"
                      : "0%"
                    : undefined
                }
              >
                <TabsContent
                  forceMount
                  value="coordinator"
                  aria-label="Coordinator"
                  className="workspace-pane"
                  data-inactive={compact && surface !== "coordinator"}
                  inert={compact && surface !== "coordinator"}
                  aria-hidden={compact && surface !== "coordinator"}
                >
                  {typeof coordinator === "function"
                    ? coordinator(!compact || surface === "coordinator")
                    : coordinator}
                </TabsContent>
              </ResizablePanel>
              <ResizableHandle
                aria-label="Resize coordinator and work"
                className={compact ? "hidden" : "workspace-divider"}
              />
              <ResizablePanel
                id="work"
                minSize={
                  compact ? (surface === "work" ? "100%" : "0%") : "320px"
                }
                maxSize={
                  compact ? (surface === "work" ? "100%" : "0%") : undefined
                }
              >
                <TabsContent
                  forceMount
                  value="work"
                  aria-label="Workspace work"
                  className="workspace-pane"
                  data-inactive={compact && surface !== "work"}
                  inert={compact && surface !== "work"}
                  aria-hidden={compact && surface !== "work"}
                >
                  {work}
                </TabsContent>
              </ResizablePanel>
            </ResizablePanelGroup>
          </Tabs>
        )}
      </main>
    </SidebarProvider>
  );
}
