import { useRetainedState } from "./retainedState";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Download, File, Folder, ChevronRight, ArrowUp } from "lucide-react";
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
import { usePage } from "./useResource";
import { OutputPreview, outputHref as contentHref } from "./OutputPreview";

type OutputFile = components["schemas"]["OutputFile"];
type FileEntry = components["schemas"]["FileEntry"];
type FileWork = components["schemas"]["FileWork"];

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
            <a
              className="min-w-0 flex-1 break-all"
              href={contentHref(file)}
              target="_blank"
              rel="noreferrer"
            >
              {file.name}
            </a>
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
          <OutputPreview key={file.id} file={file} compact />
        </li>
      ))}
    </ul>
  );
}

function FileDetails({ entry }: { entry: FileEntry }) {
  const file = entry.file!;
  return (
    <DetailSection
      title={entry.name}
      aria-label="File details"
      className="file-details min-h-0"
    >
      <p className="detail-metadata">
        {sizeLabel(file.size)} · <Timestamp date={file.created_at} />
      </p>
      <div className="actions">
        <WorkspaceLink to={taskHref(file.workspace_id, { key: file.task_key })}>
          <Badge variant="outline">{file.task_key}</Badge>
        </WorkspaceLink>
        {file.result_id && (
          <WorkspaceLink to={resultHref(file.workspace_id, file.result_id)}>
            Source result
          </WorkspaceLink>
        )}
      </div>
      <div className="actions">
        <Button size="sm" asChild>
          <a href={contentHref(file)} target="_blank" rel="noreferrer">
            Open
          </a>
        </Button>
        <Button size="sm" variant="outline" asChild>
          <a href={contentHref(file) + "?download=true"} download>
            Download
          </a>
        </Button>
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
  const [work, setWork] = useRetainedState<FileWork | null>(
    `files:${workspaceId}:work`,
    null,
  );
  const [path, setPath] = useRetainedState(`files:${workspaceId}:path`, "");
  const [sort, setSort] = useRetainedState(
    `files:${workspaceId}:sort`,
    "newest",
  );
  const [selected, setSelected] = useRetainedState<string | null>(
    `files:${workspaceId}:chosen`,
    null,
  );
  const base = `workspaces/${workspaceId}/files`;
  const groups = usePage<components["schemas"]["FileWorkPage"]>(
    `${base}/work`,
    refresh,
    true,
  );
  const params = new URLSearchParams({ path, sort });
  if (work?.task_key) params.set("task_key", work.task_key);
  const resource = usePage<components["schemas"]["FileDirectory"]>(
    `${base}/browse?${params}`,
    refresh,
    true,
  );
  const entry = resource.data?.items.find((item) => item.id === selected);
  function navigate(next: string) {
    setPath(next);
    setSelected(null);
  }
  const parts = path.split("/").filter(Boolean);
  return (
    <section className="files-page @container p-5" aria-label="Files">
      <div className="grid h-full min-h-0 grid-cols-[6rem_minmax(0,1fr)] grid-rows-[minmax(0,1fr)_auto] gap-3 @2xl:grid-cols-[7rem_minmax(10rem,1fr)_minmax(0,2fr)] @2xl:grid-rows-[minmax(0,1fr)]">
        <nav
          aria-label="Files by work"
          className="files-scroll min-h-0 min-w-0 overflow-y-auto border-r pr-2"
        >
          <ContentStack>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-full justify-start px-2 text-sm"
              onClick={() => {
                setWork(null);
                navigate("");
              }}
            >
              All files
            </Button>
            <ResourceRetry resources={[groups]}>Retry work</ResourceRetry>
            {groups.data?.items.map((item) => (
              <Tooltip key={item.id}>
                <TooltipTrigger asChild>
                  <button
                    className="block w-full rounded px-2 py-2 text-left text-sm hover:bg-accent focus-visible:outline-ring aria-pressed:bg-accent aria-pressed:font-medium"
                    aria-pressed={work?.id === item.id}
                    onClick={() => {
                      setWork(item);
                      navigate("");
                    }}
                  >
                    <Badge variant="outline">{item.task_key}</Badge>
                  </button>
                </TooltipTrigger>
                <TooltipContent side="right">
                  {item.task_key} {item.title}
                </TooltipContent>
              </Tooltip>
            ))}
            {groups.data?.next_offset != null && (
              <LoadMore
                disabled={groups.loading || groups.loadingMore}
                onClick={() => void groups.older()}
              >
                More work
              </LoadMore>
            )}
          </ContentStack>
        </nav>
        <ContentStack
          space="section"
          className="files-scroll min-h-0 min-w-0 overflow-y-auto"
          aria-label="File listing"
        >
          <div className="flex flex-wrap items-center gap-2">
            <nav
              aria-label="File location"
              className="flex min-w-0 flex-1 flex-wrap items-center gap-1"
            >
              <Button size="sm" variant="ghost" onClick={() => navigate("")}>
                {work ? (
                  <Badge variant="outline">{work.task_key ?? work.title}</Badge>
                ) : (
                  "All files"
                )}
              </Button>
              {parts.map((part, index) => (
                <span key={index} className="inline-flex min-w-0 items-center">
                  <ChevronRight
                    className="size-3 shrink-0"
                    aria-hidden="true"
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-auto whitespace-normal break-all"
                    onClick={() =>
                      navigate(parts.slice(0, index + 1).join("/"))
                    }
                  >
                    {part}
                  </Button>
                </span>
              ))}
            </nav>
          </div>
          <div className="flex items-center justify-between gap-2">
            {path ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => navigate(parts.slice(0, -1).join("/"))}
              >
                <ArrowUp />
                Up
              </Button>
            ) : (
              <span />
            )}
            <ChoiceSelect
              compact
              value={sort}
              label="Sort files"
              options={[
                { value: "name", label: "Name" },
                { value: "newest", label: "Newest" },
                { value: "type", label: "Type" },
              ]}
              onChange={setSort}
            />
          </div>
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
                          ? navigate(item.path)
                          : setSelected(item.id)
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
                            {!work && (
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
        <aside
          key={entry?.id}
          aria-label="Selected file"
          className="files-scroll flex flex-col col-span-2 max-h-[40vh] min-h-0 min-w-0 overflow-y-auto border-t pt-4 @2xl:col-span-1 @2xl:max-h-none @2xl:border-t-0 @2xl:border-l @2xl:pt-0 @2xl:pl-4"
        >
          {entry?.file && <FileDetails key={entry.id} entry={entry} />}
          {!entry?.file && (
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
