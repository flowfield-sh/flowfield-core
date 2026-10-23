import type { ReactNode } from "react";
import {
  Columns3,
  FileText,
  Files,
  Folder,
  Inbox,
  Settings,
} from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { WorkIdentity } from "./WorkIdentity";
import { followLink, resultHref, taskHref, workspaceHref } from "./navigation";
import { WorkspaceLink } from "./WorkspaceLink";
import { ResourceRetry } from "./ResourceRetry";
import { useResource } from "./useResource";
import type { WorkspaceBoard } from "./workspace";
import type { components } from "./api-schema";

function RecentLink({
  href,
  current,
  open,
  itemKey,
  title,
  state,
  detail,
}: {
  href: string;
  current: string;
  open: (url: string) => void;
  itemKey?: string | null;
  title: string;
  state: components["schemas"]["WorkState"] | null;
  detail?: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <WorkspaceLink
          to={href}
          className="work-navigation-row"
          aria-label={`${itemKey ? itemKey + " " : ""}${title}`}
          aria-current={current === href ? "page" : undefined}
          onClick={(e) => followLink(e, () => open(href))}
        >
          <WorkIdentity
            state={state}
            itemKey={itemKey ?? undefined}
            title={title}
            decorative
          />
        </WorkspaceLink>
      </TooltipTrigger>
      <TooltipContent side="right">
        {itemKey} {title}
        {detail ?? (state ? ` · ${state.label}` : "")}
      </TooltipContent>
    </Tooltip>
  );
}

export function WorkNavigation({
  board,
  open,
  current,
}: {
  board: WorkspaceBoard;
  open: (url: string) => void;
  current: string;
}) {
  const base = workspaceHref(board.workspace.id);
  const attention = useResource<components["schemas"]["AttentionPage"]>(
    `workspaces/${board.workspace.id}/view/attention?column=action&limit=5`,
    board,
  );
  const delivered = useResource<components["schemas"]["DeliveredPage"]>(
    `workspaces/${board.workspace.id}/view/delivered?limit=5`,
    board,
  );
  const attentionKeys = new Set(board.attention_work_keys);
  const progress = board.tasks
    .filter(
      (t) =>
        !t.archived &&
        !attentionKeys.has(t.key) &&
        !["backlog", "up_next", "done"].includes(t.status),
    )
    .sort(
      (a, b) =>
        b.activity_at.localeCompare(a.activity_at) ||
        b.key.localeCompare(a.key, undefined, { numeric: true }),
    )
    .slice(0, 5);
  const resources = [
    { name: "Inbox", href: base + "/inbox", Icon: Inbox },
    { name: "Task Board", href: base + "/tasks", Icon: Columns3 },
    { name: "Documents", href: base + "/documents", Icon: FileText },
    { name: "Files", href: base + "/files", Icon: Files },
    { name: "Projects", href: base + "/projects", Icon: Folder },
  ];
  return (
    <>
      <nav className="workspace-resources" aria-label="Workspace resources">
        {resources.map(({ name, href, Icon }) => (
          <a
            key={name}
            href={href}
            aria-label={
              name === "Inbox" && board.needs_you_count
                ? `Inbox ${board.needs_you_count}`
                : undefined
            }
            aria-current={current.split("?")[0] === href ? "page" : undefined}
            onClick={(e) => followLink(e, () => open(href))}
          >
            <Icon size={16} />
            <span>{name}</span>
            {name === "Inbox" && !!board.needs_you_count && (
              <span
                className="nav-count needs-attention ml-auto border"
                aria-hidden="true"
              >
                {Math.min(99, board.needs_you_count)}
                {board.needs_you_count > 99 ? "+" : ""}
              </span>
            )}
          </a>
        ))}
        <a
          href={base + "/edit"}
          onClick={(e) => followLink(e, () => open(base + "/edit"))}
        >
          <Settings size={16} /> Settings
        </a>
      </nav>
      {(!!attention.data?.items.length ||
        !!delivered.data?.items.length ||
        !!progress.length) && (
        <div className="work-navigation-heading">
          <span>Recent Work</span>
        </div>
      )}
      {(!!attention.data?.items.length ||
        !!delivered.data?.items.length ||
        !!progress.length ||
        attention.loading ||
        attention.error ||
        delivered.loading ||
        delivered.error) && (
        <nav className="work-navigation-scroll" aria-label="Recent Work">
          <div className="work-navigation-list">
            {(!!attention.data?.items.length || attention.error) && (
              <section aria-label="Needs attention">
                <h3 className="work-group-label">Needs attention</h3>
                <ResourceRetry resources={[attention]} />
                {attention.data?.items.map(
                  (item) =>
                    item.request_href && (
                      <RecentLink
                        key={item.kind + ":" + item.id}
                        href={item.request_href}
                        current={current}
                        open={open}
                        itemKey={item.task_key}
                        title={item.title}
                        state={item.state ?? null}
                      />
                    ),
                )}
              </section>
            )}
            {(!!delivered.data?.items.length || delivered.error) && (
              <section aria-label="Delivered">
                <h3 className="work-group-label">Delivered</h3>
                <ResourceRetry resources={[delivered]} />
                {delivered.data?.items.map((item) => (
                  <RecentLink
                    key={item.id}
                    href={resultHref(board.workspace.id, item.id)}
                    current={current}
                    open={open}
                    itemKey={item.task_key}
                    title={item.title}
                    state={{ label: "Delivered", tone: "complete" }}
                  />
                ))}
              </section>
            )}
            {!!progress.length && (
              <section aria-label="In progress">
                <h3 className="work-group-label">In progress</h3>
                {progress.map((task) => (
                  <RecentLink
                    key={task.id}
                    href={taskHref(task.workspace_id, task)}
                    current={current}
                    open={open}
                    itemKey={task.key}
                    title={task.title}
                    state={task.state ?? null}
                  />
                ))}
              </section>
            )}
          </div>
        </nav>
      )}
    </>
  );
}
