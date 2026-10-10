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

export type WorkspaceProject = {
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

function ProjectItem({
  project,
  active,
  select,
}: {
  project: WorkspaceProject;
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
  }, [project.name]);
  return (
    <SidebarMenuItem>
      <Tooltip>
        <TooltipTrigger asChild>
          <SidebarMenuButton asChild size="lg" isActive={active}>
            <a
              href={project.href}
              aria-label={project.name}
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
              <ProjectBadge id={project.id} name={project.name} />
              <CountBadge count={project.inboxCount} label="Inbox" />
              <span ref={text} data-sidebar="label">
                {project.name}
              </span>
            </a>
          </SidebarMenuButton>
        </TooltipTrigger>
        {(truncated || (state === "collapsed" && !isMobile)) && (
          <TooltipContent side="right" className="max-w-xs">
            {project.name}
          </TooltipContent>
        )}
      </Tooltip>
    </SidebarMenuItem>
  );
}

function ProjectNavigation({
  projects,
  activeProjectId,
  onProjectSelect,
  footer,
  onHome,
  onAddProject,
}: {
  projects: WorkspaceProject[];
  activeProjectId: string;
  onProjectSelect: (project: WorkspaceProject) => void;
  footer?: ReactNode;
  onHome?: () => void;
  onAddProject?: () => void;
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
        {isMobile && <SidebarTrigger aria-label="Close projects" />}
      </SidebarHeader>
      <SidebarContent className="group-data-[collapsible=icon]:overflow-y-auto">
        <nav aria-label="Projects" className="workspace-projects">
          <p className="workspace-projects-label group-data-[collapsible=icon]:hidden">
            Projects
          </p>
          <SidebarMenu className="gap-2">
            {onAddProject && (
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip="Add project"
                  aria-label="Add project"
                  onClick={() => {
                    onAddProject();
                    setOpenMobile(false);
                  }}
                >
                  <Plus />
                  <span data-sidebar="label">Add project</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            )}
            {projects.map((project) => (
              <ProjectItem
                key={project.id}
                project={project}
                active={project.id === activeProjectId}
                select={() => {
                  onProjectSelect(project);
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
  projects,
  activeProjectId,
  onProjectSelect,
  coordinator,
  work,
  footer,
  onHome,
  onAddProject,
  workLocation,
}: {
  projects: WorkspaceProject[];
  activeProjectId: string;
  onProjectSelect: (project: WorkspaceProject) => void;
  workLocation?: string;
  coordinator?: ReactNode | ((visible: boolean) => ReactNode);
  work: ReactNode;
  footer?: ReactNode;
  onHome?: () => void;
  onAddProject?: () => void;
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
      <ProjectNavigation
        projects={projects}
        activeProjectId={activeProjectId}
        onProjectSelect={onProjectSelect}
        footer={footer}
        onHome={onHome}
        onAddProject={onAddProject}
      />
      <main className="workspace-main">
        {coordinator == null ? (
          <div className="workspace-pane workspace-work-only">
            <div className="workspace-mobile-bar md:hidden">
              <SidebarTrigger aria-label="Open projects" />
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
                  aria-label="Open projects"
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
                  aria-label="Project work"
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
