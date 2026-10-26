import { RecordTitle } from "./RecordTitle";
import { DetailSection } from "./DetailLayout";
import { WorkspaceLink } from "./WorkspaceLink";
import type { components } from "./api-schema";
type Snapshot = components["schemas"]["DocumentSnapshot"];
export function SourceReferences({
  documents,
  updates = [],
  title = "Relevant documents",
}: {
  documents: Snapshot[];
  updates?: components["schemas"]["DocumentUpdate"][];
  title?: string;
}) {
  if (!documents.length) return null;
  const unique = [
    ...new Map(
      documents.map((d) => [
        `${d.workspace_id}:${d.id}:${d.revision}:${d.purpose}`,
        d,
      ]),
    ).values(),
  ];
  return (
    <DetailSection title={title}>
      <ul className="source-reference-list">
        {unique.map((item) => (
          <li key={`${item.id}:${item.revision}:${item.purpose}`}>
            <WorkspaceLink
              to={`/workspaces/${item.workspace_id}/documents/${item.id}/revisions/${item.revision}`}
            >
              {item.title}
            </WorkspaceLink>
            <span className="detail-metadata">
              {" "}
              · <RecordTitle>{`revision #${item.revision}`}</RecordTitle> ·{" "}
              {item.purpose === "reference"
                ? "Reference material"
                : "Required source"}
            </span>
            {updates
              .filter(
                (update) =>
                  update.id === item.id &&
                  update.latest_revision > item.revision,
              )
              .map((update) => (
                <span key={update.id}>
                  {" "}
                  ·{" "}
                  <WorkspaceLink
                    to={`/workspaces/${item.workspace_id}/documents/${item.id}/revisions/${update.latest_revision}`}
                  >
                    <RecordTitle>{`Revision #${update.latest_revision}`}</RecordTitle>{" "}
                    available
                  </WorkspaceLink>
                </span>
              ))}
          </li>
        ))}
      </ul>
    </DetailSection>
  );
}
