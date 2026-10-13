import { WorkCard } from "./WorkCard";
import { WorkspaceLink } from "./WorkspaceLink";
import { boardHref, workspaceHref } from "./navigation";
import type { Board } from "./workspace";

export function MilestoneList({
  board,
}: {
  board: Pick<Board, "tasks" | "milestones">;
}) {
  const cards = board.milestones.map((milestone) => {
    const members = board.tasks.filter((t) => t.milestone_id === milestone.id);
    const tasks = members.filter((t) => !t.archived || t.status === "done");
    const done = tasks.filter((t) => t.status === "done").length;
    const column =
      tasks.length && done === tasks.length
        ? "Complete"
        : tasks.some((t) => !["backlog", "up_next"].includes(t.status))
          ? "In progress"
          : "Planned";
    return {
      milestone,
      tasks,
      done,
      column,
      archived: !!members.length && members.every((t) => t.archived),
    };
  });
  return (
    <div className="board three-column-board" aria-label="Milestones">
      {["Planned", "In progress", "Complete"].map((column) => (
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
              .map(({ milestone, tasks, done, archived }) => (
                <WorkCard key={milestone.id}>
                  <div className="work-card-heading">
                    <span className="task-key">{milestone.key}</span>
                  </div>
                  <span className="task-identity">
                    <strong>
                      <WorkspaceLink
                        className="task-card-link"
                        to={`${workspaceHref(milestone.workspace_id)}/milestones/${milestone.key}`}
                      >
                        {milestone.title}
                      </WorkspaceLink>
                    </strong>
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
