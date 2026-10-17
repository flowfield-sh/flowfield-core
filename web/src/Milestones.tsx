import { Board } from "./Board";
import { MilestoneBadge } from "./MilestoneBadge";
import { WorkState } from "./WorkState";
import type { components } from "./api-schema";
import { WorkCard } from "./WorkCard";
import { WorkspaceLink } from "./WorkspaceLink";
import { boardHref, workspaceHref } from "./navigation";

export function milestoneCards(
  board: Pick<
    components["schemas"]["BrowserWorkspaceBoard"],
    | "tasks"
    | "milestones"
    | "milestone_verifications"
    | "milestone_delivery_ids"
    | "attention_work_keys"
  >,
) {
  const attention = new Set(board.attention_work_keys ?? []);
  return board.milestones.map((milestone) => {
    const members = board.tasks.filter((t) => t.milestone_id === milestone.id);
    const tasks = members.filter((t) => !t.archived || t.status === "done");
    const done = tasks.filter((t) => t.status === "done").length;
    const verification = board.milestone_verifications?.[milestone.id];
    const state = verification?.status ?? "";
    const column =
      state === "accepted" ||
      (!milestone.acceptance_criteria.trim() &&
        !board.milestone_delivery_ids?.includes(milestone.id) &&
        tasks.length > 0 &&
        done === tasks.length)
        ? "Complete"
        : ["ready", "promoting", "delivery_blocked"].includes(state)
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
      verification,
      workState: tasks.find((task) => attention.has(task.key))?.state ??
        milestoneState(
          verification,
          column !== "Complete" && tasks.length > 0 && done === tasks.length,
        ) ?? {
          label: column,
          tone:
            column === "Complete" ? ("complete" as const) : ("idle" as const),
        },
      archived: !!members.length && members.every((t) => t.archived),
    };
  });
}

export function MilestoneList({
  board,
}: {
  board: Pick<
    components["schemas"]["BrowserWorkspaceBoard"],
    | "tasks"
    | "milestones"
    | "milestone_verifications"
    | "milestone_delivery_ids"
    | "attention_work_keys"
  >;
}) {
  const cards = milestoneCards(board);
  return (
    <Board className="milestone-board" aria-label="Milestones">
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
              .map(
                ({ milestone, tasks, done, archived, column, workState }) => (
                  <WorkCard key={milestone.id}>
                    <div className="work-card-heading">
                      <MilestoneBadge milestone={milestone} linked={false} />
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
                    {column !== "Planned" && column !== "Complete" && (
                      <WorkState state={workState} />
                    )}
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
                ),
              )}
            {!cards.some((card) => card.column === column) && (
              <p className="empty-column">No milestones yet</p>
            )}
          </div>
        </section>
      ))}
    </Board>
  );
}

export function milestoneState(
  value: components["schemas"]["MilestoneVerificationSummary"] | undefined,
  tasksComplete: boolean,
): components["schemas"]["WorkState"] | null {
  if (!value)
    return tasksComplete
      ? { label: "Ready for verification", tone: "waiting" }
      : null;
  const counts =
    value.checks_passed == null
      ? null
      : `${value.checks_passed} of ${value.checks_total} checks passed`;
  if (value.status === "verifying" && value.review_status === "waiting")
    return { label: "Waiting for your answer", tone: "waiting" };
  if (value.status === "verifying" && value.review_status === "queued")
    return { label: "Milestone review queued", tone: "waiting" };
  switch (value.status) {
    case "preparing":
      return { label: "Preparing combined candidate", tone: "active" };
    case "promoting":
      return { label: "Promoting verified result", tone: "active" };
    case "delivery_blocked":
      return { label: "Promotion needs your attention", tone: "attention" };
    case "ready":
      return { label: counts ?? "Ready for review", tone: "waiting" };
    case "failed":
      return {
        label:
          value.checks_passed === value.checks_total
            ? "Verification needs follow-up"
            : (counts ?? "Checks need follow-up"),
        tone: "attention",
      };
    case "verifying":
      return { label: "Verifying", tone: "active" };
    case "stale":
      return { label: "Checks out of date", tone: "waiting" };
    case "changes_requested":
      return { label: "Changes requested", tone: "attention" };
    default:
      return null;
  }
}
