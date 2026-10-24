import { RecordTitle } from "./RecordTitle";
import { SourceReferences } from "./SourceReferences";
import { useLabels } from "./labelContext";
import { ContentStack, DetailSection } from "./DetailLayout";
import { Button } from "@/components/ui/button";
import { useResource } from "./useResource";
import { useContext, useState } from "react";
import { ActivityBodyVisible } from "./ActivityEntry";
import { Markdown } from "./Markdown";
import { TextChanges } from "./TextChanges";
import { ArrowRight } from "lucide-react";
import { type TaskReference, type TaskRevision } from "./workspace";

type Field =
  | "body"
  | "title"
  | "labels"
  | "dependencies"
  | "acceptance_criteria"
  | "project_id"
  | "documents"
  | "input_files"
  | "publication";
const fields: [Field, string][] = [
  ["body", "Description"],
  ["title", "Title"],
  ["labels", "Labels"],
  ["dependencies", "Prerequisites"],
  ["acceptance_criteria", "Acceptance criteria"],
  ["project_id", "Project"],
  ["documents", "Selected documents"],
  ["input_files", "Input files"],
  ["publication", "Preparation"],
];
export function TaskChanges({
  revision,
  path,
  tasks,
}: {
  revision: number;
  path: string;
  tasks: TaskReference[];
}) {
  const labels = useLabels();
  const open = useContext(ActivityBodyVisible);
  const [retry, setRetry] = useState(0);
  const beforeRead = useResource<TaskRevision>(
    open && revision > 1 ? `${path}/revisions/${revision - 1}` : null,
    retry,
  );
  const afterRead = useResource<TaskRevision>(
    open ? `${path}/revisions/${revision}` : null,
    retry,
  );
  const before = beforeRead.data,
    after = afterRead.data;
  const changed =
    before && after
      ? fields.filter(
          ([key]) => JSON.stringify(before[key]) !== JSON.stringify(after[key]),
        )
      : [];
  function value(record: TaskRevision, field: Field): string {
    if (field === "labels")
      return (
        record.labels
          .map((id) => labels.find((item) => item.id === id)?.name ?? id)
          .join(", ") || "None"
      );
    if (field === "dependencies")
      return (
        record.dependencies
          .map(
            (id) => tasks.find((t) => t.key === id || t.id === id)?.key ?? id,
          )
          .join(", ") || "None"
      );
    if (field === "documents")
      return (
        record.documents
          .map(
            (item) =>
              `${item.id} · ${item.revision ? `revision #${item.revision}` : "latest revision"} · ${item.purpose}`,
          )
          .join("\n") || "None"
      );
    if (field === "input_files") return record.input_files.join(", ") || "None";
    if (field === "publication")
      return record.publication
        ? `Prepared revision #${record.publication.task_revision} for ${record.publication.completion}${record.publication.target_branch ? ` into ${record.publication.target_branch}` : ""}`
        : "Not prepared";
    return record[field] ?? "None";
  }
  return (
    <ContentStack className="task-changes">
      {open && (beforeRead.error || afterRead.error) && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => setRetry((v) => v + 1)}
        >
          Retry
        </Button>
      )}
      {open &&
        (!after || (revision > 1 && !before)) &&
        !beforeRead.error &&
        !afterRead.error && <p>Loading changes…</p>}
      {open && revision === 1 && after && (
        <DetailSection title={after.title}>
          <Markdown>{after.body || "No description."}</Markdown>
        </DetailSection>
      )}
      {open &&
        before &&
        after &&
        (changed.length ? (
          changed.map(([key, name]) => (
            <DetailSection key={key} title={name}>
              {key === "body" || key === "title" ? (
                <TextChanges
                  before={value(before, key)}
                  after={value(after, key)}
                />
              ) : (
                <div className="field-change">
                  <span>
                    <span className="sr-only">Before: </span>
                    {key === "publication" || key === "documents" ? (
                      <RecordTitle>{value(before, key)}</RecordTitle>
                    ) : (
                      value(before, key)
                    )}
                  </span>
                  <ArrowRight size={14} aria-hidden="true" />
                  <span>
                    <span className="sr-only">After: </span>
                    {key === "publication" || key === "documents" ? (
                      <RecordTitle>{value(after, key)}</RecordTitle>
                    ) : (
                      value(after, key)
                    )}
                  </span>
                </div>
              )}
            </DetailSection>
          ))
        ) : (
          <p>{after.change_note || "Task settings updated."}</p>
        ))}
      {open &&
        after?.publication &&
        JSON.stringify(before?.publication?.documents) !==
          JSON.stringify(after.publication.documents) && (
          <SourceReferences
            documents={after.publication.documents}
            title="Captured sources"
          />
        )}
    </ContentStack>
  );
}
