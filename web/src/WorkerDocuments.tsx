import { useState } from "react";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
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
        <p>
          Defaults for this project’s tasks. Revisions are fixed when the
          coordinator prepares each assignment. Required documents must be read;
          references provide optional supporting material.
        </p>
        <ResourceRetry resources={[page]} />
        {page.loading && <p>Loading documents…</p>}
        {page.data?.items.map((document) => {
          const selected = value.find((item) => item.id === document.id);
          return (
            <ContentStack key={document.id} space="tight">
              <SettingsCheckbox
                checked={!!selected}
                disabled={
                  document.id === "guidance" ||
                  (!selected && value.length >= 21)
                }
                onChange={(event) =>
                  change(
                    event.target.checked
                      ? [
                          ...value,
                          {
                            id: document.id,
                            revision: null,
                            purpose: "required",
                          },
                        ]
                      : value.filter((item) => item.id !== document.id),
                  )
                }
                hint={
                  document.id === "guidance"
                    ? "Required for every project"
                    : selected?.revision
                      ? `Revision ${selected.revision}`
                      : undefined
                }
              >
                {document.title}
              </SettingsCheckbox>
              {selected && document.id !== "guidance" && (
                <label>
                  <span className="detail-metadata">
                    Use {document.title} as
                  </span>
                  <NativeSelect
                    value={selected.purpose}
                    onChange={(event) =>
                      change(
                        value.map((item) =>
                          item.id === document.id
                            ? {
                                ...item,
                                purpose: event.target
                                  .value as Reference["purpose"],
                              }
                            : item,
                        ),
                      )
                    }
                  >
                    <option value="required">Required source</option>
                    <option value="reference">Reference material</option>
                  </NativeSelect>
                </label>
              )}
            </ContentStack>
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
  updates = [],
}: {
  documents: Snapshot[];
  updates?: components["schemas"]["DocumentUpdate"][];
}) {
  if (!documents.length) return null;
  const content = (
    <ContentStack space="content">
      {!!updates.length && (
        <p>
          Newer source revisions are available. Discuss their relevance with the
          coordinator before preparing affected work again. Active attempts keep
          their saved sources.
        </p>
      )}
      <p className="detail-metadata">
        {documents
          .reduce((sum, item) => sum + item.characters, 0)
          .toLocaleString()}{" "}
        characters ·{" "}
        {documents.reduce((sum, item) => sum + item.bytes, 0).toLocaleString()}{" "}
        bytes of source text. Token usage is reported separately by the harness.
      </p>
      <ul>
        {documents.map((item) => (
          <li key={item.id}>
            <WorkspaceLink
              to={`/workspaces/${item.workspace_id}/documents/${item.id}/revisions/${item.revision}`}
            >
              {item.title} · revision {item.revision}
            </WorkspaceLink>
            {" · " + (item.purpose === "reference" ? "Reference" : "Required")}
            {updates
              .filter((update) => update.id === item.id)
              .map((update) => (
                <span key={update.id}>
                  {" · "}
                  <WorkspaceLink
                    to={`/workspaces/${item.workspace_id}/documents/${item.id}/revisions/${update.latest_revision}`}
                  >
                    Revision {update.latest_revision} available
                  </WorkspaceLink>
                </span>
              ))}
          </li>
        ))}
      </ul>
    </ContentStack>
  );
  return <Disclosure summary="Worker documents">{content}</Disclosure>;
}
