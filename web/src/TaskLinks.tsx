import { useLocation, useNavigate } from "react-router";
import { entityNavigationState } from "./navigation";
import { TooltipLink } from "./TooltipLink";
import { followLink, taskHref } from "./navigation";
import type { TaskReference } from "./workspace";
export function TaskLink({
  projectId,
  task,
  open,
}: {
  projectId: string;
  task: TaskReference;
  open: (id: string) => void;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const href = taskHref(task.project_id, task);
  return (
    <TooltipLink
      className="task-key"
      href={href}
      tooltip={task.title}
      onClick={(event) =>
        followLink(event, () => {
          if (task.project_id === projectId) open(task.id);
          else
            void navigate(href, {
              state: entityNavigationState(
                location.pathname,
                location.state,
                href,
              ),
            });
        })
      }
    >
      {task.key}
    </TooltipLink>
  );
}

export function BlockedBy({
  projectId,
  tasks,
  open,
  prefix = "Blocked by",
}: {
  projectId: string;
  tasks: TaskReference[];
  open: (id: string) => void;
  prefix?: string;
}) {
  return (
    <span>
      {prefix}{" "}
      {tasks.map((task, index) => (
        <span key={task.key}>
          {index > 0 && ", "}
          <TaskLink projectId={projectId} task={task} open={open} />
        </span>
      ))}
    </span>
  );
}
