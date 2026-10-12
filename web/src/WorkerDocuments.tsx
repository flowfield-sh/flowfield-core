import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { components } from "./api-schema";
import { ContentStack, Disclosure } from "./DetailLayout";
import { ResourceRetry } from "./ResourceRetry";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { WorkspaceLink } from "./WorkspaceLink";
import { useResource } from "./useResource";

type Reference = components["schemas"]["Project"]["worker_documents"][number];
type Snapshot = components["schemas"]["DocumentSnapshot"];

export function WorkerDocumentPicker({
  workspaceId,
  value,
  change,
}: {
  workspaceId: string;
  value: Reference[];
  change: (value: Reference[]) => void;
}) {
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const page = useResource<components["schemas"]["DocumentPage"]>(
    `workspaces/${workspaceId}/documents?limit=20${cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`,
    workspaceId,
  );
  return (
    <Disclosure summary={`Worker documents · ${value.length} selected`}>
      <ContentStack space="content">
        <p className="detail-metadata">
          Defaults for this project’s tasks. Revisions are fixed when the
          coordinator prepares each assignment.
        </p>
        <ResourceRetry resources={[page]} />
        {page.loading && <p>Loading documents…</p>}
        {page.data?.items.map((document) => {
          const selected = value.find((item) => item.id === document.id);
          return (
            <SettingsCheckbox
              key={document.id}
              checked={!!selected}
              disabled={!selected && value.length >= 20}
              onChange={(event) =>
                change(
                  event.target.checked
                    ? [...value, { id: document.id, revision: null }]
                    : value.filter((item) => item.id !== document.id),
                )
              }
              hint={
                selected?.revision ? `Revision ${selected.revision}` : undefined
              }
            >
              {document.title}
            </SettingsCheckbox>
          );
        })}
        <div className="actions">
          {!!cursors.length && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setCursors(cursors.slice(0, -1))}
            >
              Previous documents
            </Button>
          )}
          {page.data?.next_cursor && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                setCursors([...cursors, String(page.data!.next_cursor)])
              }
            >
              More documents
            </Button>
          )}
        </div>
      </ContentStack>
    </Disclosure>
  );
}

export function WorkerDocumentEvidence({
  documents,
}: {
  documents: Snapshot[];
}) {
  if (!documents.length) return null;
  return (
    <Disclosure summary="Worker documents">
      <ContentStack space="content">
        <p className="detail-metadata">
          {documents
            .reduce((sum, item) => sum + item.characters, 0)
            .toLocaleString()}{" "}
          characters ·{" "}
          {documents
            .reduce((sum, item) => sum + item.bytes, 0)
            .toLocaleString()}{" "}
          bytes of source text. Token usage is reported separately by the
          harness.
        </p>
        <ul>
          {documents.map((item) => (
            <li key={item.id}>
              <WorkspaceLink
                to={`/workspaces/${item.workspace_id}/documents/${item.id}/revisions/${item.revision}`}
              >
                {item.title} · revision {item.revision}
              </WorkspaceLink>
            </li>
          ))}
        </ul>
      </ContentStack>
    </Disclosure>
  );
}
