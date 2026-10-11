import {
  useState,
  useEffect,
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
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
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
import { CountBadge } from "./CountBadge";
import { ProjectBadge } from "./ProjectBadge";
import { Plus } from "lucide-react";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import "./workspace-frame.css";

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

function WorkspaceItem({
  workspace,
  active,
  select,
}: {
  workspace: WorkspaceEntry;
  active: boolean;
  select: () => void;
}) {
  const text = useRef<HTMLSpanElement>(null);
  const [truncated, setTruncated] = useState(false);
  const { state, isMobile } = useSidebar();
  useEffect(() => {
    const node = text.current;
    if (!node) return;
    const measure = () => setTruncated(node.scrollWidth > node.clientWidth);
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    measure();
    return () => observer.disconnect();
  }, [workspace.name]);
  return (
    <SidebarMenuItem>
      <Tooltip>
        <TooltipTrigger asChild>
          <SidebarMenuButton asChild size="lg" isActive={active}>
            <a
              href={workspace.href}
              aria-label={workspace.name}
              aria-current={active ? "page" : undefined}
              onClick={(event) => {
                if (
                  event.button !== 0 ||
                  event.metaKey ||
                  event.ctrlKey ||
                  event.shiftKey ||
                  event.altKey
                )
                  return;
                event.preventDefault();
                select();
              }}
            >
              <ProjectBadge id={workspace.id} name={workspace.name} />
              <CountBadge count={workspace.inboxCount} label="Inbox" />
              <span ref={text} data-sidebar="label">
                {workspace.name}
              </span>
            </a>
          </SidebarMenuButton>
        </TooltipTrigger>
        {(truncated || (state === "collapsed" && !isMobile)) && (
          <TooltipContent side="right" className="max-w-xs">
            {workspace.name}
          </TooltipContent>
        )}
      </Tooltip>
    </SidebarMenuItem>
  );
}

function WorkspaceNavigation({
  workspaces,
  activeWorkspaceId,
  onWorkspaceSelect,
  footer,
  onHome,
  onAddWorkspace,
}: {
  workspaces: WorkspaceEntry[];
  activeWorkspaceId: string;
  onWorkspaceSelect: (workspace: WorkspaceEntry) => void;
  footer?: ReactNode;
  onHome?: () => void;
  onAddWorkspace?: () => void;
}) {
  const { setOpenMobile, isMobile } = useSidebar();
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="workspace-sidebar-header">
        <a
          href="/"
          onClick={(event) => {
            if (
              !onHome ||
              event.button !== 0 ||
              event.metaKey ||
              event.ctrlKey ||
              event.shiftKey ||
              event.altKey
            )
              return;
            event.preventDefault();
            onHome();
            setOpenMobile(false);
          }}
          aria-label="Flowfield"
          className="workspace-logo"
        >
          <img src="/assets/flowfield.svg" alt="" width={32} height={32} />
        </a>
        {isMobile && <SidebarTrigger aria-label="Close workspaces" />}
      </SidebarHeader>
      <SidebarContent className="group-data-[collapsible=icon]:overflow-y-auto">
        <nav aria-label="Workspaces" className="workspace-projects">
          <p className="workspace-projects-label group-data-[collapsible=icon]:hidden">
            Workspaces
          </p>
          <SidebarMenu className="gap-2">
            {onAddWorkspace && (
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip="New workspace"
                  aria-label="New workspace"
                  onClick={() => {
                    onAddWorkspace();
                    setOpenMobile(false);
                  }}
                >
                  <Plus />
                  <span data-sidebar="label">New workspace</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            )}
            {workspaces.map((workspace) => (
              <WorkspaceItem
                key={workspace.id}
                workspace={workspace}
                active={workspace.id === activeWorkspaceId}
                select={() => {
                  onWorkspaceSelect(workspace);
                  setOpenMobile(false);
                }}
              />
            ))}
          </SidebarMenu>
        </nav>
      </SidebarContent>
      {footer && <SidebarFooter>{footer}</SidebarFooter>}
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
  onAddWorkspace,
  workLocation,
}: {
  workspaces: WorkspaceEntry[];
  activeWorkspaceId: string;
  onWorkspaceSelect: (workspace: WorkspaceEntry) => void;
  workLocation?: string;
  coordinator?: ReactNode | ((visible: boolean) => ReactNode);
  work: ReactNode;
  footer?: ReactNode;
  onHome?: () => void;
  onAddWorkspace?: () => void;
}) {
  const compact = useSyncExternalStore(
    subscribeCompact,
    () => window.matchMedia(compactQuery).matches,
    () => false,
  );
  const [surface, setSurface] = useState("work");
  const [location, setLocation] = useState(workLocation);
  if (location !== workLocation) {
    setLocation(workLocation);
    setSurface("work");
  }
  return (
    <SidebarProvider
      open={false}
      className="workspace-frame"
      style={
        {
          "--sidebar-width": "14rem",
          "--sidebar-width-icon": "3.5rem",
        } as CSSProperties
      }
    >
      <WorkspaceNavigation
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        onWorkspaceSelect={onWorkspaceSelect}
        footer={footer}
        onHome={onHome}
        onAddWorkspace={onAddWorkspace}
      />
      <main className="workspace-main">
        {coordinator == null ? (
          <div className="workspace-pane workspace-work-only">
            <div className="workspace-mobile-bar md:hidden">
              <SidebarTrigger aria-label="Open workspaces" />
              <span>Flowfield</span>
            </div>
            {work}
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
                defaultSize="440px"
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
