import { Disclosure, DetailSection } from "./DetailLayout";
import { Button } from "@/components/ui/button";
import { useResource } from "./useResource";
import { useState } from "react";
import { Markdown } from "./Markdown";
import { TextChanges } from "./TextChanges";
import { ArrowRight } from "lucide-react";
import {
  label,
  type Milestone,
  type TaskReference,
  type TaskRevision,
} from "./workspace";

type Field = "body" | "title" | "task_type" | "milestone_id" | "dependencies";
const fields: [Field, string][] = [
  ["body", "Description"],
  ["title", "Title"],
  ["task_type", "Type"],
  ["milestone_id", "Milestone"],
  ["dependencies", "Prerequisites"],
];
export function TaskChanges({
  revision,
  path,
  milestones,
  tasks,
}: {
  revision: number;
  path: string;
  milestones: Milestone[];
  tasks: TaskReference[];
}) {
  const [open, setOpen] = useState(false);
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
    if (field === "task_type") return label(record.task_type);
    if (field === "milestone_id")
      return (
        milestones.find((e) => e.id === record.milestone_id)?.title ??
        record.milestone_id ??
        "No milestone"
      );
    if (field === "dependencies")
      return (
        record.dependencies
          .map((id) => tasks.find((t) => t.id === id)?.key ?? id)
          .join(", ") || "None"
      );
    return record[field];
  }
  return (
    <Disclosure
      summary={
        <>{revision === 1 ? "View definition" : "View definition changes"}</>
      }
      className="task-changes history-disclosure"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      {open && (beforeRead.error || afterRead.error) && (
        <Button size="sm" onClick={() => setRetry((v) => v + 1)}>
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
                    {value(before, key)}
                  </span>
                  <ArrowRight size={14} aria-hidden="true" />
                  <span>
                    <span className="sr-only">After: </span>
                    {value(after, key)}
                  </span>
                </div>
              )}
            </DetailSection>
          ))
        ) : (
          <p>No net change across these edits.</p>
        ))}
    </Disclosure>
  );
}
