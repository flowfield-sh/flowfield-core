import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ContentStack } from "./DetailLayout";
import { ProjectBadge } from "./ProjectBadge";
import { WorkspaceLink } from "./WorkspaceLink";
import { projectHref, workspaceHref } from "./navigation";
import type { Project } from "./workspace";

export function ProjectList({
  projects,
  workspaceId,
  workspaceName,
}: {
  projects: Project[];
  workspaceId: string;
  workspaceName: string;
}) {
  return (
    <section className="setup-page" aria-label="Projects">
      <ContentStack space="section" className="welcome">
        <div className="detail-heading-row">
          <h1>{workspaceName}</h1>
          <Button size="sm" asChild>
            <WorkspaceLink to={`${workspaceHref(workspaceId)}/projects/new`}>
              <Plus />
              Add project
            </WorkspaceLink>
          </Button>
        </div>
        {!projects.length && (
          <p>Add your first project, or start planning with the Coordinator.</p>
        )}
        <div className="project-list">
          {projects.map((project) => (
            <WorkspaceLink
              key={project.id}
              to={projectHref(project.id)}
              className="project-list-row"
            >
              <ProjectBadge id={project.id} name={project.name} />
              <ContentStack space="tight" className="min-w-0">
                <strong>{project.name}</strong>
                <span className="detail-metadata break-all">
                  {project.path}
                </span>
              </ContentStack>
            </WorkspaceLink>
          ))}
        </div>
      </ContentStack>
    </section>
  );
}
