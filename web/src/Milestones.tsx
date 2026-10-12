import { ContentStack } from "./DetailLayout";
import { Button } from "@/components/ui/button";
import { Popover } from "radix-ui";
import { Markdown } from "./Markdown";
import { WorkspaceLink } from "./WorkspaceLink";
import { workspaceHref } from "./navigation";
import type { Board } from "./workspace";

export function MilestoneList({
  board,
  open,
  viewTasks,
}: {
  board: Pick<Board, "tasks" | "milestones">;
  open: (id: string) => void;
  viewTasks: (id: string) => void;
}) {
  const cards = board.milestones.map((milestone) => {
    const tasks = board.tasks.filter(
      (t) =>
        t.milestone_id === milestone.id && (!t.archived || t.status === "done"),
    );
    const done = tasks.filter((t) => t.status === "done").length;
    const column =
      tasks.length && done === tasks.length
        ? "Complete"
        : tasks.some((t) => !["backlog", "up_next"].includes(t.status))
          ? "In progress"
          : "Planned";
    return { milestone, tasks, done, column };
  });
  return (
    <div className="milestone-board">
      {["Planned", "In progress", "Complete"].map((column) => (
        <section key={column} className="milestone-column" aria-label={column}>
          <h3 className="detail-title flex items-center gap-2">
            {column}{" "}
            <span className="nav-count">
              {cards.filter((card) => card.column === column).length}
            </span>
          </h3>
          <ContentStack>
            {cards
              .filter((card) => card.column === column)
              .map(({ milestone, tasks, done }) => (
                <article key={milestone.id} className="milestone-card">
                  <ContentStack>
                    <div className="actions justify-between">
                      <span className="detail-metadata">{milestone.key}</span>{" "}
                      <Button size="sm" variant="outline" asChild>
                        <WorkspaceLink
                          to={
                            workspaceHref(milestone.workspace_id) +
                            "/milestones/" +
                            encodeURIComponent(milestone.key)
                          }
                          onClick={(event) => {
                            if (
                              !event.metaKey &&
                              !event.ctrlKey &&
                              !event.shiftKey &&
                              !event.altKey &&
                              event.button === 0
                            ) {
                              event.preventDefault();
                              open(milestone.id);
                            }
                          }}
                        >
                          Edit
                        </WorkspaceLink>
                      </Button>
                    </div>
                    <strong>{milestone.title}</strong>
                    <span>
                      {done} of {tasks.length} tasks done
                    </span>
                    <div className="actions">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => viewTasks(milestone.id)}
                      >
                        View tasks
                      </Button>
                      <Popover.Root>
                        <Popover.Trigger asChild>
                          <Button size="sm" variant="outline">
                            Description
                          </Button>
                        </Popover.Trigger>
                        <Popover.Portal>
                          <Popover.Content
                            sideOffset={8}
                            align="start"
                            className="composer-settings milestone-description"
                          >
                            <ContentStack>
                              <h3 className="detail-title">
                                {milestone.title}
                              </h3>
                              <Markdown>
                                {milestone.body || "No description yet."}
                              </Markdown>
                            </ContentStack>
                          </Popover.Content>
                        </Popover.Portal>
                      </Popover.Root>
                    </div>
                  </ContentStack>
                </article>
              ))}
            {!cards.some((card) => card.column === column) && (
              <p className="collection-empty">No milestones</p>
            )}
          </ContentStack>
        </section>
      ))}
    </div>
  );
}
