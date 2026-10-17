import { File } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { components } from "./api-schema";
import { ContentStack, DetailSection } from "./DetailLayout";
import { LoadMore } from "./LoadMore";
import { ResourceRetry } from "./ResourceRetry";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { taskHref, workspaceHref } from "./navigation";
import { usePage } from "./useResource";
import { label } from "./workspace";

type OutputFile = components["schemas"]["OutputFile"];
type OutputPage = components["schemas"]["OutputPage"];

function sizeLabel(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function OutputFiles({ files }: { files: OutputFile[] }) {
  return (
    <div className="project-list">
      {files.map((file) => {
        const content = `/api/workspaces/${file.workspace_id}/files/${file.id}/content`;
        return (
          <div className="project-list-row" key={file.id}>
            <File className="size-5 shrink-0" aria-hidden="true" />
            <ContentStack space="content" className="min-w-0 flex-1">
              <ContentStack space="tight">
                <strong className="break-all">{file.name}</strong>
                {file.description && <p>{file.description}</p>}
                <span className="detail-metadata">
                  {sizeLabel(file.size)} · <Timestamp date={file.created_at} />
                  {file.result_status && ` · ${label(file.result_status)}`}
                </span>
                <span className="detail-metadata">
                  {file.scope_id ? (
                    <WorkspaceLink
                      to={
                        file.result_id
                          ? `${taskHref(file.scope_id, { key: file.task_key }, "result")}/${file.result_id}`
                          : taskHref(file.scope_id, { key: file.task_key })
                      }
                    >
                      {file.task_key}: {file.task_title}
                      {file.result_version
                        ? ` · Result ${file.result_version}`
                        : ""}
                    </WorkspaceLink>
                  ) : (
                    file.task_title
                  )}
                  {file.milestone_id && (
                    <>
                      {" "}
                      ·{" "}
                      <WorkspaceLink
                        to={`${workspaceHref(file.workspace_id)}/milestones/${file.milestone_id}`}
                      >
                        {file.milestone_title}
                      </WorkspaceLink>
                    </>
                  )}
                </span>
              </ContentStack>
              <div className="actions">
                <Button size="sm" variant="outline" asChild>
                  <a href={content} target="_blank" rel="noreferrer">
                    Open
                  </a>
                </Button>
                <Button size="sm" variant="outline" asChild>
                  <a href={`${content}?download=true`} download>
                    Download
                  </a>
                </Button>
              </div>
            </ContentStack>
          </div>
        );
      })}
    </div>
  );
}

export function Files({
  workspaceId,
  refresh,
}: {
  workspaceId: string;
  refresh: string;
}) {
  const resource = usePage<OutputPage>(
    `workspaces/${workspaceId}/files`,
    refresh,
    true,
  );
  return (
    <section className="setup-page" aria-label="Files">
      <ContentStack space="section" className="collection-layout">
        <h2 className="sr-only">Files</h2>
        <ResourceRetry resources={[resource]}>Retry files</ResourceRetry>
        {!resource.data && !resource.error && <p>Loading files…</p>}
        {resource.data &&
          (resource.data.items.length ? (
            <OutputFiles files={resource.data.items} />
          ) : (
            <p className="collection-empty">No files yet</p>
          ))}
        {resource.data?.next_before && (
          <LoadMore
            disabled={resource.loading || resource.loadingMore}
            onClick={() => void resource.older()}
          >
            Load more files
          </LoadMore>
        )}
      </ContentStack>
    </section>
  );
}

export function ResultFiles({ files }: { files: OutputFile[] }) {
  return files.length ? (
    <DetailSection title="Files">
      <OutputFiles files={files} />
    </DetailSection>
  ) : null;
}
