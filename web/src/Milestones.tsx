import { WorkCard } from "./WorkCard";
import { WorkspaceLink } from "./WorkspaceLink";
import { boardHref, workspaceHref } from "./navigation";
import type { Board } from "./workspace";

export function MilestoneList({
  board,
}: {
  board: Pick<Board, "tasks" | "milestones" | "milestone_states">;
}) {
  const cards = board.milestones.map((milestone) => {
    const members = board.tasks.filter((t) => t.milestone_id === milestone.id);
    const tasks = members.filter((t) => !t.archived || t.status === "done");
    const done = tasks.filter((t) => t.status === "done").length;
    const state = board.milestone_states?.[milestone.id] ?? "";
    const column =
      state === "accepted" ||
      (!milestone.acceptance_criteria.trim() &&
        tasks.length > 0 &&
        done === tasks.length)
        ? "Complete"
        : state === "ready"
          ? "Review"
          : state || (tasks.length && done === tasks.length)
            ? "In progress"
            : tasks.some((t) => !["backlog", "up_next"].includes(t.status))
              ? "In progress"
              : "Planned";
    return {
      milestone,
      tasks,
      done,
      column,
      state,
      archived: !!members.length && members.every((t) => t.archived),
    };
  });
  return (
    <div className="board milestone-board" aria-label="Milestones">
      {["Planned", "In progress", "Review", "Complete"].map((column) => (
        <section key={column} className="column" aria-label={column}>
          <h2>
            {column}
            <span className="count">
              {cards.filter((card) => card.column === column).length}
            </span>
          </h2>
          <div
            className="column-cards"
            role="region"
            aria-label={`${column} milestones`}
            tabIndex={0}
          >
            {cards
              .filter((card) => card.column === column)
              .map(({ milestone, tasks, done, archived, state }) => (
                <WorkCard key={milestone.id}>
                  <div className="work-card-heading">
                    <span className="task-key">{milestone.key}</span>
                  </div>
                  <span className="task-identity">
                    <strong>
                      <WorkspaceLink
                        className="task-card-link"
                        to={`${workspaceHref(milestone.workspace_id)}/milestones/${milestone.key}${state === "ready" ? "?tab=review" : ""}`}
                      >
                        {milestone.title}
                      </WorkspaceLink>
                    </strong>
                  </span>
                  <span className="detail-metadata">
                    {(
                      {
                        verifying: "Verifying",
                        ready: "",
                        failed: "Verification needs follow-up",
                        stale: "Verification out of date",
                        changes_requested: "Changes requested",
                        accepted: "",
                      } as Record<string, string>
                    )[state] ??
                      (milestone.acceptance_criteria.trim() &&
                      tasks.length &&
                      done === tasks.length
                        ? "Tasks complete · Not yet verified"
                        : "")}
                  </span>
                  <WorkspaceLink
                    className="detail-metadata"
                    to={boardHref(
                      milestone.workspace_id,
                      { milestone: milestone.id },
                      archived,
                    )}
                  >
                    {done} of {tasks.length} tasks done
                  </WorkspaceLink>
                </WorkCard>
              ))}
            {!cards.some((card) => card.column === column) && (
              <p className="empty-column">No milestones yet</p>
            )}
          </div>
        </section>
      ))}
    </div>
  );
}
