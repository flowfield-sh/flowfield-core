import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ContentStack } from "./DetailLayout";
import { ProjectBadge } from "./ProjectBadge";
import { WorkspaceLink } from "./WorkspaceLink";
import { projectHref } from "./navigation";
import type { Project } from "./workspace";

export function ProjectList({ projects }: { projects: Project[] }) {
  return (
    <section className="setup-page" aria-label="Projects">
      <ContentStack space="section" className="welcome">
        <div className="detail-heading-row">
          <h1>Projects</h1>
          <Button size="sm" asChild>
            <WorkspaceLink to="/new-project">
              <Plus />
              New project
            </WorkspaceLink>
          </Button>
        </div>
        <div className="collection-layout">
          {projects.map((project) => (
            <WorkspaceLink
              key={project.id}
              to={projectHref(project.id)}
              className="collection-row project-list-row"
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
