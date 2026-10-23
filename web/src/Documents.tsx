import type { DirtyCallback } from "./retainedState";
import { useCallback, useContext, useEffect, useState } from "react";
import { DocumentUsage } from "./DocumentUsage";
import { useDirtyItems } from "./useDirtyItems";
import { ResourceTitles } from "./WorkspaceTabs";
import { FileText, Pencil, Eye, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { components } from "./api-schema";
import type { AdjacentSnapshot } from "./AdjacentView";
import { ContentStack } from "./DetailLayout";
import { EntityPane } from "./EntityPane";
import { HeaderAction } from "./HeaderAction";
import { DetailHeader } from "./Presentation";
import { Markdown, MarkdownField } from "./Markdown";
import { RevisionHistory } from "./RevisionHistory";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { useResource } from "./useResource";
import { request, type Project } from "./workspace";
import { workspaceHref } from "./navigation";

type Document = components["schemas"]["Document"];
type DocumentPage = components["schemas"]["DocumentPage"];
const fields = (record?: Document) => ({
  title: record?.title ?? "",
  body: record?.body ?? "",
});

export function Documents({
  workspaceId,
  identity,
  projects,
  revision,
  pathname,
  refresh,
  onDirty,
  navigate,
  publishView,
  close,
}: {
  workspaceId: string;
  identity?: string;
  projects: Project[];
  revision?: string;
  pathname: string;
  refresh: string;
  onDirty: DirtyCallback;
  navigate: (path: string, replace?: boolean) => void;
  publishView: (value: AdjacentSnapshot) => void;
  close: () => void;
}) {
  const base = `${workspaceHref(workspaceId)}/documents`;
  const path = `workspaces/${workspaceId}/documents`;
  const latest = useResource<Document>(
    identity ? `${path}/${identity}` : null,
    refresh,
  );
  const historical = useResource<Document>(
    identity && revision ? `${path}/${identity}?revision=${revision}` : null,
    refresh,
  );
  const document = revision ? historical.data : latest.data;
  const labelResource = useContext(ResourceTitles);
  useEffect(() => {
    if (latest.data)
      labelResource?.(`${base}/${latest.data.id}`, latest.data.title);
  }, [latest.data, base, labelResource]);
  const view = identity
    ? document
      ? {
          surface: "document" as const,
          document_id: document.id,
          document_revision: document.revision,
        }
      : null
    : { surface: "documents" as const };
  const contextKey = JSON.stringify(view);
  useEffect(() => {
    publishView({
      workspaceId,
      pathname,
      context:
        contextKey === "null"
          ? null
          : { view: JSON.parse(contextKey), task: null },
    });
  }, [workspaceId, pathname, contextKey, publishView]);
  if (!identity)
    return (
      <section className="setup-page documents-page" aria-label="Documents">
        <ContentStack space="section" className="collection-layout">
          <DocumentList
            path={path}
            base={base}
            refresh={refresh}
            projects={projects}
          />
        </ContentStack>
      </section>
    );
  return (
    <EntityPane
      label="Document details"
      identity={`${identity}:${revision ?? "current"}`}
      close={close}
      returnFocusHref={`${base}/${identity}`}
    >
      {document && latest.data ? (
        <DocumentEditor
          key={`${identity}:${revision ?? "current"}`}
          incoming={document}
          latest={latest.data}
          historical={document.revision !== latest.data.revision}
          refresh={refresh}
          path={path}
          base={base}
          onDirty={onDirty}
          close={close}
          editing={pathname.endsWith("/edit")}
          viewDocument={() => navigate(`${base}/${identity}`, true)}
          read={async (signal) => {
            const fresh = await request<Document>(
              `${path}/${identity}`,
              "GET",
              undefined,
              signal,
            );
            if (signal.aborted)
              throw new DOMException("Read cancelled", "AbortError");
            latest.invalidate();
            latest.setData(fresh);
            return revision ? document : fresh;
          }}
          saved={(value, method) => {
            if (method !== "GET") {
              latest.invalidate();
              latest.setData(value);
              historical.invalidate();
              historical.setData(value);
              if (revision) navigate(`${base}/${value.id}`, true);
            }
          }}
        />
      ) : (
        <section className="editor">
          <DetailHeader
            title={
              latest.error || historical.error
                ? "Could not load document"
                : "Loading document…"
            }
            close={close}
            closeLabel="Back"
          />
          {(latest.error || historical.error) && (
            <ContentStack>
              <p role="alert">{latest.error || historical.error}</p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  latest.retry();
                  historical.retry();
                }}
              >
                Retry document
              </Button>
            </ContentStack>
          )}
        </section>
      )}
    </EntityPane>
  );
}
function DocumentList({
  path,
  base,
  refresh,
  projects,
}: {
  projects: Project[];
  path: string;
  base: string;
  refresh: string;
}) {
  const [after, setAfter] = useState<string | null>(null);
  const resource = useResource<DocumentPage>(
    `${path}?limit=50${after ? `&after=${after}` : ""}`,
    refresh,
  );
  return (
    <>
      <h2 className="sr-only">Documents</h2>
      {resource.error && (
        <Button size="sm" variant="outline" onClick={resource.retry}>
          Retry documents
        </Button>
      )}
      {!resource.data && !resource.error && <p>Loading documents…</p>}
      <div className="project-list">
        {[...(resource.data?.items ?? [])]
          .sort((a, b) =>
            a.id === "guidance"
              ? -1
              : b.id === "guidance"
                ? 1
                : a.title.localeCompare(b.title),
          )
          .map((item) => (
            <WorkspaceLink
              className="project-list-row"
              key={item.id}
              to={`${base}/${item.id}`}
            >
              <FileText className="size-5 shrink-0" />
              <ContentStack space="tight" className="min-w-0">
                <strong>{item.title}</strong>
                <span className="detail-metadata">
                  {item.id === "guidance" ? "Main guidance · " : ""}Revision #
                  {item.revision} · <Timestamp date={item.created_at} />
                </span>
                <span className="detail-metadata">
                  Projects:{" "}
                  {projects
                    .filter(
                      (project) =>
                        item.id === "guidance" ||
                        project.worker_documents.some(
                          (ref) => ref.id === item.id,
                        ),
                    )
                    .map((project) => project.name)
                    .join(", ") || "None"}
                </span>
              </ContentStack>
            </WorkspaceLink>
          ))}
      </div>
      <div className="pagination-controls actions">
        {after && (
          <Button
            size="sm"
            variant="outline"
            disabled={resource.loading}
            onClick={() => setAfter(null)}
          >
            First documents
          </Button>
        )}
        {resource.data?.next_cursor && (
          <Button
            size="sm"
            variant="outline"
            disabled={resource.loading}
            onClick={() => setAfter(String(resource.data!.next_cursor))}
          >
            More documents
          </Button>
        )}
      </div>
    </>
  );
}

function DocumentEditor({
  incoming,
  latest,
  historical,
  refresh,
  path,
  base,
  onDirty,
  saved,
  read,
  close,
  editing,
  viewDocument,
}: {
  incoming: Document;
  latest: Document;
  historical: boolean;
  refresh: string;
  path: string;
  base: string;
  onDirty: DirtyCallback;
  saved: (record: Document, method: string) => void;
  read: (signal: AbortSignal) => Promise<Document>;
  close: () => void;
  editing: boolean;
  viewDocument: () => void;
}) {
  const markDirty = useDirtyItems(onDirty);
  const [usageDirty, setUsageDirty] = useState(false);
  const markBody = useCallback<DirtyCallback>(
    (dirty, retained) => {
      markDirty("body", dirty, retained);
    },
    [markDirty],
  );
  const markUsage = useCallback<DirtyCallback>(
    (dirty, retained) => {
      setUsageDirty(dirty);
      markDirty("usage", dirty, retained);
    },
    [markDirty],
  );
  const state = useRecordEditor({
    incoming,
    fields,
    onDirty: markBody,
    saved: (value, method) => {
      saved(value, method);
      if (method === "PUT" && !usageDirty) viewDocument();
    },
    read,
    recordName: "document",
    retainedKey: `${path}/${incoming.id}:${historical ? incoming.revision : "latest"}`,
    path: (record) => `${path}/${record!.id}`,
  });
  const {
    loaded,
    values,
    change,
    busy,
    dirty,
    element: editor,
    cancel,
  } = state;
  const selected = editing ? loaded! : incoming;
  return (
    <section
      ref={editor}
      className="editor documents-page"
      aria-label={editing ? "Edit document" : "Document"}
    >
      <DetailHeader
        title={selected.title}
        close={close}
        closeLabel="Back"
        actions={
          <div className="actions">
            {historical || selected.deleted_at ? (
              <>
                <HeaderAction label="Current document" asChild>
                  <WorkspaceLink to={`${base}/${selected.id}`}>
                    <FileText />
                  </WorkspaceLink>
                </HeaderAction>
                <HeaderAction
                  label="Restore"
                  tooltip="Restore this document revision"
                  disabled={
                    busy ||
                    (!selected.deleted_at &&
                      latest.revision === selected.revision)
                  }
                  onClick={() =>
                    void state.run(
                      "POST",
                      {
                        expected_revision: latest.revision,
                        revision: selected.revision,
                        author: "human",
                      },
                      "/restore",
                    )
                  }
                >
                  <RotateCcw />
                </HeaderAction>
              </>
            ) : (
              <HeaderAction
                label={editing ? "View document" : "Edit"}
                tooltip={editing ? "View document" : "Edit document"}
                asChild
              >
                <WorkspaceLink
                  to={`${base}/${selected.id}${editing ? "" : "/edit"}`}
                >
                  {editing ? <Eye /> : <Pencil />}
                </WorkspaceLink>
              </HeaderAction>
            )}
          </div>
        }
      />
      <div className="document-layout">
        <ContentStack space="section" className="document-content">
          <DocumentUsage
            path={`${path}/${selected.id}/usage`}
            refresh={refresh}
            onDirty={markUsage}
            editing={
              editing &&
              !historical &&
              !selected.deleted_at &&
              selected.id !== "guidance"
            }
          />
          {editing && <EditorFeedback state={state} />}
          {selected.deleted_at && (
            <p role="status">
              This document was deleted. Restore a revision to use it again.
            </p>
          )}
          {!historical && !selected.deleted_at && editing ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void state.run("PUT", {
                  ...values,
                  expected_revision: selected.revision,
                  author: "human",
                });
              }}
            >
              <fieldset
                disabled={busy}
                className="content-stack"
                data-space="section"
              >
                <Label className="field block">
                  Document title
                  <Input
                    required
                    maxLength={200}
                    value={values.title}
                    onChange={(event) => change("title", event.target.value)}
                  />
                </Label>
                <MarkdownField
                  label="Content"
                  rows={16}
                  value={values.body}
                  onChange={(value) => change("body", value)}
                />
                <div className="actions">
                  <Button size="sm" disabled={!values.title.trim() || !dirty}>
                    Save document
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      cancel(() => {
                        if (!usageDirty) viewDocument();
                      });
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </fieldset>
            </form>
          ) : (
            <Markdown>
              {selected.body ||
                (selected.id === "guidance"
                  ? "No shared guidance yet. Write it here or discuss it with the Coordinator."
                  : "This document is empty.")}
            </Markdown>
          )}
        </ContentStack>
        <RevisionHistory
          key={selected.id}
          path={`${path}/${selected.id}`}
          href={`${base}/${selected.id}`}
          revision={latest.revision}
          selected={selected.revision}
        />
      </div>
    </section>
  );
}
