import { useContext } from "react";
import { ProjectOwners } from "./WorkspaceLink";
import { canonicalHref } from "./navigation";
import { useLocation, useNavigate } from "react-router";
import { ResourceNavigation } from "./WorkspaceTabs";
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
  const resourceNavigate = useContext(ResourceNavigation);
  const href = canonicalHref(
    taskHref(task.workspace_id, task),
    useContext(ProjectOwners),
  );
  return (
    <TooltipLink
      className="task-key"
      href={href}
      tooltip={task.title}
      onClick={(event) =>
        followLink(event, () => {
          if (task.workspace_id === projectId) open(task.id);
          else if (resourceNavigate) resourceNavigate(href);
          else void navigate(href, { state: location.state });
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
