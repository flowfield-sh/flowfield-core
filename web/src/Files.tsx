import { useContext } from "react";
import { useLocation, useNavigate } from "react-router";
import { ResourceNavigation } from "./WorkspaceTabs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Download, File, Folder, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ChoiceSelect } from "@/components/ui/choice-select";
import type { components } from "./api-schema";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { LoadMore } from "./LoadMore";
import { ResourceRetry } from "./ResourceRetry";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { resultHref, taskHref } from "./navigation";
import { usePage, useResource } from "./useResource";
import { OutputPreview, outputHref as contentHref } from "./OutputPreview";

type OutputFile = components["schemas"]["OutputFile"];

function sizeLabel(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function OutputFiles({ files }: { files: OutputFile[] }) {
  return (
    <ul className="divide-y rounded-md border">
      {files.map((file) => (
        <li key={file.id} className="content-stack px-3 py-2">
          <div className="flex min-w-0 items-center gap-2">
            <File className="size-4 shrink-0" aria-hidden="true" />
            <WorkspaceLink
              className="min-w-0 flex-1 break-all"
              to={`/workspaces/${file.workspace_id}/files?${new URLSearchParams({ file: file.id, task_key: file.task_key, path: file.name.includes("/") ? file.name.slice(0, file.name.lastIndexOf("/")) : "" })}`}
            >
              {file.name}
            </WorkspaceLink>
            <span className="detail-metadata shrink-0">
              {sizeLabel(file.size)}
            </span>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button size="icon-sm" variant="ghost" asChild>
                  <a
                    href={`${contentHref(file)}?download=true`}
                    download
                    aria-label={`Download ${file.name}`}
                  >
                    <Download />
                  </a>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Download {file.name}</TooltipContent>
            </Tooltip>
          </div>
        </li>
      ))}
    </ul>
  );
}

function FileDetails({ file }: { file: OutputFile }) {
  return (
    <DetailSection
      title={file.name.split("/").at(-1)}
      aria-label="File details"
      className="file-details min-h-0"
    >
      <p className="detail-metadata">
        <WorkspaceLink to={taskHref(file.workspace_id, { key: file.task_key })}>
          <Badge variant="outline">{file.task_key}</Badge>
        </WorkspaceLink>
        {" · "}
        {sizeLabel(file.size)} · <Timestamp date={file.created_at} />
      </p>
      <div className="actions">
        <Button size="sm" variant="outline" asChild>
          <a href={contentHref(file)} target="_blank" rel="noreferrer">
            Open
          </a>
        </Button>
        <Button size="sm" variant="outline" asChild>
          <a href={contentHref(file) + "?download=true"} download>
            Download
          </a>
        </Button>
        {file.result_id && (
          <Button size="sm" variant="outline" asChild>
            <WorkspaceLink to={resultHref(file.workspace_id, file.result_id)}>
              Source
            </WorkspaceLink>
          </Button>
        )}
      </div>
      <OutputPreview key={file.id} file={file} />
    </DetailSection>
  );
}

export function Files({
  workspaceId,
  refresh,
}: {
  workspaceId: string;
  refresh: string;
}) {
  const location = useLocation();
  const routerNavigate = useNavigate();
  const resourceNavigate = useContext(ResourceNavigation);
  const query = new URLSearchParams(location.search);
  const taskKey = query.get("task_key") ?? "";
  const path = query.get("path") ?? "";
  const requestedSort = query.get("sort");
  const sort =
    requestedSort === "name" || requestedSort === "type"
      ? requestedSort
      : "newest";
  const selected = query.get("file");
  function href(changes: Record<string, string | null>) {
    const next = new URLSearchParams(location.search);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    const search = next.toString();
    return location.pathname + (search ? "?" + search : "");
  }
  function navigate(changes: Record<string, string | null>) {
    const target = href(changes);
    if (resourceNavigate) resourceNavigate(target);
    else void routerNavigate(target);
  }
  const base = `workspaces/${workspaceId}/files`;
  const groups = usePage<components["schemas"]["FileWorkPage"]>(
    `${base}/work`,
    refresh,
    true,
  );
  const params = new URLSearchParams({ path, sort });
  if (taskKey) params.set("task_key", taskKey);
  const resource = usePage<components["schemas"]["FileDirectory"]>(
    `${base}/browse?${params}`,
    refresh,
    true,
  );
  // Read the selected record directly so a deep link also works beyond the
  // first directory page, without loading every preceding page.
  const chosen = useResource<OutputFile>(
    selected ? base + "/" + encodeURIComponent(selected) : null,
    refresh,
  );
  const file = chosen.data;
  const tasks = groups.data?.items ?? [];
  const choices =
    taskKey && !tasks.some((item) => item.task_key === taskKey)
      ? [{ id: "task:" + taskKey, task_key: taskKey, title: taskKey }, ...tasks]
      : tasks;
  const parts = path.split("/").filter(Boolean);
  return (
    <section className="files-page @container p-5" aria-label="Files">
      <div className="grid h-full min-h-0 grid-cols-1 grid-rows-[minmax(0,1fr)_auto] gap-4 @2xl:grid-cols-[minmax(14rem,1fr)_minmax(0,2fr)] @2xl:grid-rows-[minmax(0,1fr)]">
        <ContentStack
          space="section"
          className="min-h-0 min-w-0"
          aria-label="File listing"
        >
          <div className="grid grid-cols-[minmax(0,1fr)_7rem] gap-2">
            <ChoiceSelect
              compact
              value={taskKey || "all"}
              label="Filter files by task"
              options={[
                { value: "all", label: "All files" },
                ...choices.map((item) => ({
                  value: item.task_key!,
                  label:
                    item.title === item.task_key
                      ? item.title
                      : `${item.task_key} · ${item.title}`,
                })),
              ]}
              onChange={(value) => {
                navigate({
                  task_key: value === "all" ? null : value,
                  path: null,
                  file: null,
                });
              }}
            />
            <ChoiceSelect
              compact
              value={sort}
              label="Sort files"
              options={[
                { value: "name", label: "Name" },
                { value: "newest", label: "Newest" },
                { value: "type", label: "Type" },
              ]}
              onChange={(value) =>
                navigate({ sort: value === "newest" ? null : value })
              }
            />
          </div>
          <ResourceRetry resources={[groups]}>Retry tasks</ResourceRetry>
          {groups.data?.next_offset != null && (
            <LoadMore
              disabled={groups.loading || groups.loadingMore}
              onClick={() => void groups.older()}
            >
              Load more tasks
            </LoadMore>
          )}
          <ContentStack
            space="section"
            className="files-scroll min-h-0 min-w-0 overflow-y-auto"
          >
            {path && (
              <div className="flex flex-wrap items-center gap-2">
                <nav
                  aria-label="File location"
                  className="flex min-w-0 flex-1 flex-wrap items-center gap-1"
                >
                  <WorkspaceLink
                    className="file-path-link"
                    to={href({ path: null, file: null })}
                  >
                    {taskKey || "All files"}
                  </WorkspaceLink>
                  {parts.map((part, index) => (
                    <span
                      key={index}
                      className="inline-flex min-w-0 items-center gap-1"
                    >
                      <ChevronRight
                        className="size-3 shrink-0"
                        aria-hidden="true"
                      />
                      <WorkspaceLink
                        className="file-path-link"
                        to={href({
                          path: parts.slice(0, index + 1).join("/"),
                          file: null,
                        })}
                        aria-current={
                          index === parts.length - 1 ? "location" : undefined
                        }
                      >
                        {part}
                      </WorkspaceLink>
                    </span>
                  ))}
                </nav>
              </div>
            )}
            <ResourceRetry resources={[resource]}>Retry files</ResourceRetry>
            {!resource.data && !resource.error && <p>Loading files…</p>}
            {resource.data &&
              (resource.data.items.length ? (
                <ul
                  className="divide-y rounded-md border"
                  aria-label="Folder contents"
                >
                  {resource.data.items.map((item) => (
                    <li key={item.id}>
                      <button
                        className="flex w-full min-w-0 items-center gap-3 px-3 py-2 text-left hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring aria-pressed:bg-accent"
                        aria-pressed={selected === item.id}
                        onClick={() =>
                          item.kind === "folder"
                            ? navigate({ path: item.path, file: null })
                            : navigate({ file: item.id })
                        }
                      >
                        {item.kind === "folder" ? (
                          <Folder className="size-4 shrink-0" />
                        ) : (
                          <File className="size-4 shrink-0" />
                        )}
                        <span className="min-w-0 flex-1">
                          <span className="block truncate" title={item.name}>
                            {item.name}
                          </span>
                          {item.file && (
                            <span className="detail-metadata">
                              {!taskKey && (
                                <>
                                  <Badge variant="outline">
                                    {item.file.task_key}
                                  </Badge>
                                  {" · "}
                                </>
                              )}
                              {sizeLabel(item.file.size)} ·{" "}
                              <Timestamp date={item.file.created_at} />
                            </span>
                          )}
                        </span>
                        {item.kind === "folder" && (
                          <ChevronRight className="size-4 shrink-0" />
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="collection-empty">
                  {path ? "This folder is empty" : "No files yet"}
                </p>
              ))}
            {resource.data?.next_offset != null && (
              <LoadMore
                disabled={resource.loading || resource.loadingMore}
                onClick={() => void resource.older()}
              >
                Load more files
              </LoadMore>
            )}
          </ContentStack>
        </ContentStack>
        <aside
          key={selected}
          aria-label="Selected file"
          className="files-scroll flex flex-col max-h-[40vh] min-h-0 min-w-0 overflow-y-auto border-t pt-4 @2xl:max-h-none @2xl:border-t-0 @2xl:border-l @2xl:pt-0 @2xl:pl-4"
        >
          <ResourceRetry resources={[chosen]}>
            Retry selected file
          </ResourceRetry>
          {file && <FileDetails key={file.id} file={file} />}
          {selected && !file && !chosen.error && <p>Loading file…</p>}
          {!selected && (
            <p className="text-sm text-muted-foreground">
              Select a file to see its details.
            </p>
          )}
        </aside>
      </div>
    </section>
  );
}

export function ResultFiles({
  files,
  supporting = false,
}: {
  files: OutputFile[];
  supporting?: boolean;
}) {
  if (!files.length) return null;
  return supporting ? (
    <Disclosure summary="Supporting files">
      <OutputFiles files={files} />
    </Disclosure>
  ) : (
    <DetailSection title="Files">
      <OutputFiles files={files} />
    </DetailSection>
  );
}
