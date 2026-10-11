import type { ReactNode } from "react";
import { CollectionRow, DetailHeader } from "./Presentation";
import { workspaceHref } from "./navigation";
import type { Board, Milestone } from "./workspace";

export function MilestoneList({
  board,
  selected,
  open,
}: {
  board: Pick<Board, "tasks" | "milestones">;
  selected?: string;
  open: (id: string) => void;
}) {
  return (
    <ul className="milestone-list">
      {board.milestones.length ? (
        board.milestones.map((milestone) => {
          const tasks = board.tasks.filter(
            (t) => !t.archived && t.milestone_id === milestone.id,
          );
          const done = tasks.filter((t) => t.status === "done").length;
          return (
            <li key={milestone.id}>
              <CollectionRow
                selected={
                  selected === milestone.id || selected === milestone.key
                }
                href={`${workspaceHref(milestone.workspace_id)}/milestones/${encodeURIComponent(milestone.key)}`}
                open={() => open(milestone.id)}
              >
                <strong>
                  {milestone.key} · {milestone.title}
                </strong>
                <span>
                  {done} of {tasks.length} tasks done
                </span>
              </CollectionRow>
            </li>
          );
        })
      ) : (
        <li className="collection-empty">
          No milestones yet. Discuss a useful grouping with your coordinator, or
          add one manually.
        </li>
      )}
    </ul>
  );
}

export function MilestoneDetail({
  milestone,
  close,
  children,
}: {
  milestone: Milestone;
  close: () => void;
  children: ReactNode;
}) {
  return (
    <section className="editor milestone-panel" aria-label="Milestone details">
      <DetailHeader
        title={`${milestone.key} · ${milestone.title}`}
        close={close}
      />
      {children}
    </section>
  );
}
