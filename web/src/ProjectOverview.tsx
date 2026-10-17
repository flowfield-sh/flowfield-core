import { Button } from "@/components/ui/button";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { DetailHeader } from "./Presentation";
import { Markdown } from "./Markdown";
import { milestoneCards } from "./Milestones";
import { WorkIdentity } from "./WorkIdentity";
import { WorkspaceLink } from "./WorkspaceLink";
import { boardHref, projectHref, taskHref, workspaceHref } from "./navigation";
import type { Project, WorkspaceBoard } from "./workspace";

export function ProjectOverview({
  project,
  board,
  close,
}: {
  project: Project;
  board: WorkspaceBoard;
  close: () => void;
}) {
  const tasks = board.tasks.filter(
    (task) => task.project_id === project.id && !task.archived,
  );
  const milestones = milestoneCards(board).filter(({ milestone }) =>
    tasks.some((task) => task.milestone_id === milestone.id),
  );
  return (
    <section className="editor" aria-label="Project overview">
      <DetailHeader
        title={project.name}
        close={close}
        actions={
          <Button size="sm" variant="outline" asChild>
            <WorkspaceLink to={`${projectHref(project.id)}/edit`}>
              Settings
            </WorkspaceLink>
          </Button>
        }
      />
      <ContentStack space="section">
        {project.description && <Markdown>{project.description}</Markdown>}
        {!!milestones.length && (
          <DetailSection title="Milestones">
            <ul className="work-resource-list">
              {milestones.map(({ milestone, workState }) => (
                <li key={milestone.id}>
                  <WorkspaceLink
                    className="work-navigation-row"
                    to={`${workspaceHref(board.workspace.id)}/milestones/${milestone.key}`}
                  >
                    <WorkIdentity
                      state={workState}
                      itemKey={milestone.key}
                      title={milestone.title}
                    />
                  </WorkspaceLink>
                </li>
              ))}
            </ul>
          </DetailSection>
        )}
        <DetailSection
          title="Tasks"
          actions={
            <Button size="sm" variant="outline" asChild>
              <WorkspaceLink
                to={boardHref(board.workspace.id, { project: project.id })}
              >
                View board
              </WorkspaceLink>
            </Button>
          }
        >
          {tasks.length ? (
            <ul className="work-resource-list">
              {tasks.map((task) => (
                <li key={task.id}>
                  <WorkspaceLink
                    className="work-navigation-row"
                    to={taskHref(project.id, task)}
                  >
                    <WorkIdentity
                      state={task.state}
                      itemKey={task.key}
                      title={task.title}
                    />
                  </WorkspaceLink>
                </li>
              ))}
            </ul>
          ) : (
            <p className="collection-empty">No tasks yet</p>
          )}
        </DetailSection>
        <Disclosure summary="Project details">
          <p className="break-all">{project.path}</p>
        </Disclosure>
      </ContentStack>
    </section>
  );
}
