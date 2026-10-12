import { useEffect, useState } from "react";
import { FileText, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { components } from "./api-schema";
import type { AdjacentSnapshot } from "./AdjacentView";
import { ContentStack, DetailHeading } from "./DetailLayout";
import { ConfirmButton } from "./ConfirmButton";
import { EntityOverlay } from "./EntityOverlay";
import { DetailHeader } from "./Presentation";
import { Markdown, MarkdownField } from "./Markdown";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { useResource } from "./useResource";
import { authorLabel, request } from "./workspace";
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
  revision,
  pathname,
  refresh,
  onDirty,
  navigate,
  close,
  publishView,
}: {
  workspaceId: string;
  identity?: string;
  revision?: string;
  pathname: string;
  refresh: string;
  onDirty: (value: boolean) => void;
  navigate: (path: string, replace?: boolean) => void;
  publishView: (value: AdjacentSnapshot) => void;
  close: () => void;
}) {
  const base = `${workspaceHref(workspaceId)}/documents`;
  const path = `workspaces/${workspaceId}/documents`;
  const isNew = identity === "new";
  const latest = useResource<Document>(
    identity && !isNew ? `${path}/${identity}` : null,
    refresh,
  );
  const historical = useResource<Document>(
    identity && revision ? `${path}/${identity}?revision=${revision}` : null,
    refresh,
  );
  const document = revision ? historical.data : latest.data;
  const view =
    identity && !isNew
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
  return (
    <section className="setup-page documents-page" aria-label="Documents">
      <ContentStack space="section" className="collection-layout">
        {!identity || isNew ? (
          <DocumentList path={path} base={base} refresh={refresh} />
        ) : (
          <>
            <div className="actions">
              <Button variant="link" size="sm" asChild>
                <WorkspaceLink to={base}>All documents</WorkspaceLink>
              </Button>
            </div>
            {document && latest.data ? (
              <DocumentEditor
                key={`${identity}:${revision ?? "current"}`}
                close={close}
                incoming={document ?? undefined}
                latest={latest.data ?? undefined}
                historical={!!revision}
                path={path}
                base={base}
                onDirty={onDirty}
                read={async () => {
                  const fresh = await request<Document>(`${path}/${identity}`);
                  latest.invalidate();
                  latest.setData(fresh);
                  return revision ? document! : fresh;
                }}
                saved={(value, method) => {
                  if (method !== "GET") {
                    latest.invalidate();
                    latest.setData(value);
                    historical.invalidate();
                    historical.setData(value);
                    navigate(
                      value.deleted_at ? base : `${base}/${value.id}`,
                      true,
                    );
                  }
                }}
              />
            ) : (
              <ContentStack>
                <p role={latest.error || historical.error ? "alert" : "status"}>
                  {latest.error || historical.error || "Loading document…"}
                </p>
                {(latest.error || historical.error) && (
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
                )}
              </ContentStack>
            )}
          </>
        )}
      </ContentStack>
      {isNew && (
        <EntityOverlay
          title="New document"
          identity="new-document"
          close={close}
        >
          <DocumentEditor
            historical={false}
            path={path}
            base={base}
            onDirty={onDirty}
            close={close}
            read={async () => {
              throw new Error("No document yet");
            }}
            saved={(value) => {
              onDirty(false);
              navigate(base + "/" + value.id, true);
            }}
          />
        </EntityOverlay>
      )}
    </section>
  );
}

function DocumentList({
  path,
  base,
  refresh,
}: {
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
      <div className="actions justify-end">
        <h2 className="sr-only">Documents</h2>
        <Button size="sm" asChild>
          <WorkspaceLink to={`${base}/new`}>
            <Plus />
            New document
          </WorkspaceLink>
        </Button>
      </div>
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
                  {item.id === "guidance" ? "Main guidance · " : ""}Revision{" "}
                  {item.revision} · <Timestamp date={item.created_at} />
                </span>
              </ContentStack>
            </WorkspaceLink>
          ))}
      </div>
      <div className="actions justify-end">
        {after && (
          <Button size="sm" variant="outline" onClick={() => setAfter(null)}>
            First documents
          </Button>
        )}
        {resource.data?.next_cursor && (
          <Button
            size="sm"
            variant="outline"
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
  path,
  base,
  onDirty,
  saved,
  read,
  close,
}: {
  incoming?: Document;
  latest?: Document;
  historical: boolean;
  path: string;
  base: string;
  onDirty: (value: boolean) => void;
  saved: (record: Document, method: string) => void;
  read: () => Promise<Document>;
  close: () => void;
}) {
  const state = useRecordEditor({
    incoming,
    fields,
    onDirty,
    saved,
    read,
    recordName: "document",
    path: (record) => path + (record ? `/${record.id}` : ""),
  });
  const {
    loaded,
    values,
    change,
    busy,
    dirty,
    editing,
    setEditing,
    element: editor,
    cancel,
  } = state;
  return (
    <section
      ref={editor}
      className={`document-layout ${loaded ? "" : "document-new"}`}
      aria-label={loaded ? "Document" : "New document"}
    >
      <ContentStack space="section" className="document-content">
        {!loaded && <DetailHeader title="New document" close={close} />}
        {loaded && (
          <div className="document-heading">
            <DetailHeading
              title={loaded?.title ?? "New document"}
              titleAs="h2"
            />
            {loaded && !historical && !editing && !loaded.deleted_at && (
              <div className="actions">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setEditing(true)}
                >
                  Edit document
                </Button>
                {loaded.id !== "guidance" && (
                  <ConfirmButton
                    size="sm"
                    variant="outline"
                    title="Delete document?"
                    description="Remove this document from the list? Past revisions and prepared worker context are retained."
                    action={() =>
                      void state.run("DELETE", {
                        expected_revision: loaded.revision,
                        author: "human",
                      })
                    }
                  >
                    Delete document
                  </ConfirmButton>
                )}
              </div>
            )}
          </div>
        )}
        <EditorFeedback state={state} />
        {(historical || loaded?.deleted_at) && latest && loaded ? (
          <ContentStack>
            {loaded.deleted_at && (
              <p role="status">
                This document was deleted. Restore a revision to use it again.
              </p>
            )}
            <div className="actions justify-end">
              <Button size="sm" variant="outline" asChild>
                <WorkspaceLink to={`${base}/${loaded.id}`}>
                  Current document
                </WorkspaceLink>
              </Button>
              <Button
                size="sm"
                disabled={
                  busy ||
                  (!loaded.deleted_at && latest.revision === loaded.revision)
                }
                onClick={() =>
                  void state.run(
                    "POST",
                    {
                      expected_revision: latest.revision,
                      revision: loaded.revision,
                      author: "human",
                    },
                    "/restore",
                  )
                }
              >
                Restore this revision
              </Button>
            </div>
          </ContentStack>
        ) : null}
        {!historical && !loaded?.deleted_at && editing ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void state.run(loaded ? "PUT" : "POST", {
                ...values,
                ...(loaded ? { expected_revision: loaded.revision } : {}),
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
              <div className="actions justify-end">
                <Button
                  size="sm"
                  disabled={!values.title.trim() || (!!loaded && !dirty)}
                >
                  {loaded ? "Save document" : "Create document"}
                </Button>
                {loaded && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={cancel}
                  >
                    Cancel
                  </Button>
                )}
              </div>
            </fieldset>
          </form>
        ) : (
          <>
            <Markdown>
              {loaded?.body ||
                (loaded?.id === "guidance"
                  ? "No shared guidance yet. Write it here or discuss it with the Coordinator."
                  : "This document is empty.")}
            </Markdown>
          </>
        )}
      </ContentStack>
      {loaded && (
        <RevisionHistory
          key={loaded.id}
          path={`${path}/${loaded.id}`}
          href={`${base}/${loaded.id}`}
          revision={latest?.revision ?? loaded.revision}
          selected={loaded.revision}
        />
      )}
    </section>
  );
}

function RevisionHistory({
  path,
  href,
  revision,
  selected,
}: {
  path: string;
  href: string;
  revision: number;
  selected: number;
}) {
  const [before, setBefore] = useState<number | null>(null);
  const resource = useResource<DocumentPage>(
    `${path}/history?limit=20${before ? `&before=${before}` : ""}`,
    revision,
  );
  return (
    <aside className="document-history" aria-label="Revision history">
      <ContentStack>
        <h3 className="detail-title">Revisions</h3>
        <nav className="document-revisions" aria-label="Document revisions">
          {resource.error && (
            <Button size="sm" variant="outline" onClick={resource.retry}>
              Retry history
            </Button>
          )}
          {!resource.data && !resource.error && <p>Loading history…</p>}
          {resource.data?.items.map((item) => (
            <WorkspaceLink
              className="document-revision"
              key={item.revision}
              aria-label={`Revision ${item.revision}`}
              aria-current={item.revision === selected ? "page" : undefined}
              to={`${href}/revisions/${item.revision}`}
            >
              <span>
                Revision {item.revision}
                {item.revision === revision ? " · Current" : ""}
              </span>
              <span className="detail-metadata">
                {item.author === "migration" || item.author === "human"
                  ? item.author === "migration"
                    ? "Flowfield"
                    : "You"
                  : authorLabel(item.author)}{" "}
                · <Timestamp date={item.created_at} />
              </span>
            </WorkspaceLink>
          ))}
        </nav>
        <div className="actions justify-end">
          {before && (
            <Button size="sm" variant="outline" onClick={() => setBefore(null)}>
              Latest revisions
            </Button>
          )}
          {resource.data?.next_cursor && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setBefore(Number(resource.data!.next_cursor))}
            >
              Older revisions
            </Button>
          )}
        </div>
      </ContentStack>
    </aside>
  );
}
