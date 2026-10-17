import { useRetainedState } from "./retainedState";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useState } from "react";
import {
  Download,
  File,
  Folder,
  FolderPlus,
  ChevronRight,
  ArrowUp,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ChoiceSelect } from "@/components/ui/choice-select";
import type { components } from "./api-schema";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { LoadMore } from "./LoadMore";
import { ResourceRetry } from "./ResourceRetry";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { taskHref, workspaceHref } from "./navigation";
import { usePage } from "./useResource";
import { request } from "./workspace";
import { reportError } from "./requestFeedback";

type OutputFile = components["schemas"]["OutputFile"];
type FileEntry = components["schemas"]["FileEntry"];
type FileWork = components["schemas"]["FileWork"];

function sizeLabel(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}
const contentHref = (file: OutputFile) =>
  `/api/workspaces/${file.workspace_id}/files/${file.id}/content`;

export function OutputFiles({ files }: { files: OutputFile[] }) {
  return (
    <ul className="divide-y rounded-md border">
      {files.map((file) => (
        <li key={file.id} className="flex min-w-0 items-center gap-2 px-3 py-2">
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
        </li>
      ))}
    </ul>
  );
}

function FileDetails({
  entry,
  changed,
}: {
  entry: FileEntry;
  changed: () => void;
}) {
  const file = entry.file!;
  const [path, setPath] = useState(entry.path);
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState(false);
  async function save() {
    setPending(true);
    try {
      await request(
        `workspaces/${file.workspace_id}/files/${file.id}/location`,
        "PUT",
        { path, expected_revision: entry.revision },
      );
      setEditing(false);
      changed();
    } catch (error) {
      reportError(error, "Could not organize file");
    } finally {
      setPending(false);
    }
  }
  return (
    <DetailSection title={entry.name} aria-label="File details">
      <ContentStack>
        {file.description && <p>{file.description}</p>}
        <p className="detail-metadata">
          {sizeLabel(file.size)} · <Timestamp date={file.created_at} />
          {file.result_version ? ` · Version ${file.result_version}` : ""}
        </p>
        <p>
          <WorkspaceLink to={taskHref(file.scope_id, { key: file.task_key })}>
            {file.task_key} {file.task_title}
          </WorkspaceLink>
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
        </p>
        <div className="actions">
          <Button size="sm" asChild>
            <a href={contentHref(file)} target="_blank" rel="noreferrer">
              Open
            </a>
          </Button>
          <Button size="sm" variant="outline" asChild>
            <a href={`${contentHref(file)}?download=true`} download>
              Download
            </a>
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setPath(entry.path);
              setEditing(!editing);
            }}
          >
            Rename or move
          </Button>
        </div>
        {editing && (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <ContentStack>
              <Label htmlFor="file-location">File path</Label>
              <Input
                id="file-location"
                value={path}
                maxLength={500}
                disabled={pending}
                onChange={(event) => setPath(event.target.value)}
              />
              <p className="detail-metadata">
                Use / to place the file in a folder. Submitted versions keep
                their original names.
              </p>
              <div className="actions">
                <Button
                  size="sm"
                  disabled={pending || !path.trim() || path === entry.path}
                >
                  Save
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={pending}
                  onClick={() => setEditing(false)}
                >
                  Cancel
                </Button>
              </div>
            </ContentStack>
          </form>
        )}
        <Disclosure summary="Submitted version">
          <ContentStack>
            <p>{file.name}</p>
            {file.result_id && (
              <WorkspaceLink
                to={`${taskHref(file.scope_id, { key: file.task_key }, "result")}/${file.result_id}`}
              >
                View result {file.result_version}
              </WorkspaceLink>
            )}
          </ContentStack>
        </Disclosure>
      </ContentStack>
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
  const [sort, setSort] = useRetainedState(`files:${workspaceId}:sort`, "name");
  const [selected, setSelected] = useRetainedState<string | null>(
    `files:${workspaceId}:chosen`,
    null,
  );
  const [creating, setCreating] = useState(false);
  const [folder, setFolder] = useState("");
  const [pending, setPending] = useState(false);
  const [revision, setRevision] = useState(0);
  const base = `workspaces/${workspaceId}/files`;
  const groups = usePage<components["schemas"]["FileWorkPage"]>(
    `${base}/work`,
    refresh,
    true,
  );
  const params = new URLSearchParams({ path, sort });
  if (work?.task_key) params.set("task_key", work.task_key);
  if (work?.milestone_id) params.set("milestone_id", work.milestone_id);
  const resource = usePage<components["schemas"]["FileDirectory"]>(
    `${base}/browse?${params}`,
    `${refresh}:${revision}`,
    true,
  );
  const entry = resource.data?.items.find((item) => item.id === selected);
  function navigate(next: string) {
    setPath(next);
    setSelected(null);
    setCreating(false);
  }
  async function createFolder() {
    setPending(true);
    try {
      const destination = [path, folder.trim()].filter(Boolean).join("/");
      await request(`${base}/folders`, "POST", { path: destination });
      setFolder("");
      navigate(destination);
      setRevision((value) => value + 1);
    } catch (error) {
      reportError(error, "Could not create folder");
    } finally {
      setPending(false);
    }
  }
  const parts = path.split("/").filter(Boolean);
  return (
    <section className="setup-page @container" aria-label="Files">
      <div className="grid min-h-0 gap-4 @xl:grid-cols-[minmax(9rem,12rem)_minmax(0,1fr)]">
        <nav
          aria-label="Files by work"
          className="min-w-0 @xl:border-r @xl:pr-4"
        >
          <ContentStack>
            <Button
              variant={work ? "ghost" : "secondary"}
              size="sm"
              className="justify-start"
              onClick={() => {
                setWork(null);
                navigate("");
              }}
            >
              All files
            </Button>
            <ResourceRetry resources={[groups]}>Retry work</ResourceRetry>
            {(["Milestones", "Tasks"] as const).map((kind) => (
              <ContentStack key={kind} space="tight">
                {!!groups.data?.items.some((item) =>
                  kind === "Tasks" ? item.task_key : item.milestone_id,
                ) && <h3 className="detail-metadata px-2">{kind}</h3>}
                {groups.data?.items
                  .filter((item) =>
                    kind === "Tasks" ? item.task_key : item.milestone_id,
                  )
                  .map((item) => (
                    <Button
                      key={item.id}
                      size="sm"
                      variant={work?.id === item.id ? "secondary" : "ghost"}
                      className="h-auto min-h-8 justify-start whitespace-normal text-left"
                      aria-pressed={work?.id === item.id}
                      onClick={() => {
                        setWork(item);
                        navigate("");
                      }}
                    >
                      {item.task_key ? `${item.task_key} ` : ""}
                      {item.title}
                    </Button>
                  ))}
              </ContentStack>
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
        <ContentStack space="section" className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <nav
              aria-label="File location"
              className="flex min-w-0 flex-1 flex-wrap items-center gap-1"
            >
              <Button size="sm" variant="ghost" onClick={() => navigate("")}>
                {work ? (work.task_key ?? work.title) : "All files"}
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
            <Button
              size="sm"
              variant="outline"
              onClick={() => setCreating(!creating)}
            >
              <FolderPlus />
              New folder
            </Button>
          </div>
          {creating && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void createFolder();
              }}
            >
              <ContentStack>
                <Label htmlFor="new-folder">Folder name</Label>
                <Input
                  id="new-folder"
                  value={folder}
                  maxLength={200}
                  disabled={pending}
                  onChange={(event) => setFolder(event.target.value)}
                />
                <div className="actions">
                  <Button size="sm" disabled={pending || !folder.trim()}>
                    Create folder
                  </Button>
                  <Button
                    size="sm"
                    type="button"
                    variant="outline"
                    disabled={pending}
                    onClick={() => setCreating(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </ContentStack>
            </form>
          )}
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
                      <span className="min-w-0 flex-1 break-all">
                        {item.name}
                      </span>
                      {item.file && (
                        <span className="detail-metadata shrink-0">
                          {sizeLabel(item.file.size)}
                          {item.file.result_version
                            ? ` · v${item.file.result_version}`
                            : ""}
                        </span>
                      )}
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
          {entry?.file && (
            <FileDetails
              key={`${entry.id}:${entry.revision}`}
              entry={entry}
              changed={() => {
                setSelected(null);
                setRevision((value) => value + 1);
              }}
            />
          )}
        </ContentStack>
      </div>
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
