import { Button } from "@/components/ui/button";
import { ContentStack, DetailSection } from "./DetailLayout";
import { DetailHeader } from "./Presentation";
import { Markdown } from "./Markdown";
import { WorkspaceLink } from "./WorkspaceLink";
import { boardHref, projectHref } from "./navigation";
import { useResource } from "./useResource";
import { ResourceRetry } from "./ResourceRetry";
import type { components } from "./api-schema";
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
  const settings = useResource<components["schemas"]["IntegrationSettings"]>(
    `projects/${project.id}/integration`,
    board,
  );
  return (
    <section className="editor" aria-label="Project overview">
      <DetailHeader
        title={project.name}
        close={close}
        actions={
          <div className="actions">
            <Button size="sm" variant="outline" asChild>
              <WorkspaceLink
                to={boardHref(board.workspace.id, { project: project.id })}
              >
                View tasks
              </WorkspaceLink>
            </Button>
            <Button size="sm" variant="outline" asChild>
              <WorkspaceLink to={`${projectHref(project.id)}/edit`}>
                Settings
              </WorkspaceLink>
            </Button>
          </div>
        }
      />
      <ContentStack space="section" className="@container">
        {project.description && <Markdown>{project.description}</Markdown>}
        <ResourceRetry resources={[settings]} />
        <div className="grid gap-4 rounded-lg border p-4 @min-[36rem]:grid-cols-2">
          <DetailSection title="Directory">
            <code className="wrap-anywhere">{project.path}</code>
          </DetailSection>
          {settings.data ? (
            <>
              <DetailSection title="Branch">
                <p>{settings.data.target_branch || "Not configured"}</p>
              </DetailSection>
              <DetailSection title="Validation">
                {settings.data.checks.length ? (
                  <ul className="content-stack">
                    {settings.data.checks.map((command, index) => (
                      <li key={index}>
                        <code className="wrap-anywhere">{command}</code>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>No checks configured</p>
                )}
              </DetailSection>
              <DetailSection title="Runtime">
                {!settings.data.setup_commands.length && (
                  <p>No setup commands</p>
                )}
                {settings.data.setup_commands.map((command, index) => (
                  <code key={index} className="wrap-anywhere">
                    {command}
                  </code>
                ))}
              </DetailSection>
              <DetailSection title="Time limits">
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
                  <dt>Setup</dt>
                  <dd>{settings.data.setup_timeout_seconds}s</dd>
                  <dt>Each check</dt>
                  <dd>{settings.data.check_timeout_seconds}s</dd>
                </dl>
              </DetailSection>
            </>
          ) : (
            !settings.error && <p>Loading settings…</p>
          )}
        </div>
      </ContentStack>
    </section>
  );
}
