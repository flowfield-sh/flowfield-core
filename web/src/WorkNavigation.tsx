import { useState } from "react";
import {
  Archive,
  Columns3,
  FileText,
  Flag,
  Folder,
  Settings,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { WorkState } from "./WorkState";
import { milestoneCards, milestoneState } from "./Milestones";
import { followLink, taskHref, workspaceHref } from "./navigation";
import { WorkspaceLink } from "./WorkspaceLink";
import { canonicalResourceRoot } from "./WorkspaceTabs";
import type { WorkspaceBoard } from "./workspace";

export function workItems(board: WorkspaceBoard) {
  const attention = new Set(board.attention_work_keys ?? []);
  return [
    ...milestoneCards(board).map(
      ({ milestone, column, verification, tasks, done, archived }) => ({
        key: milestone.key,
        title: milestone.title,
        href: `${workspaceHref(board.workspace.id)}/milestones/${milestone.key}`,
        complete: column === "Complete",
        archived,
        planned: column === "Planned",
        attention:
          attention.has(milestone.key) ||
          tasks.some((t) => attention.has(t.key)),
        state: tasks.find((t) => attention.has(t.key))?.state ??
          milestoneState(
            verification,
            tasks.length > 0 && done === tasks.length,
          ) ?? {
            label: column,
            tone:
              column === "Complete" ? ("complete" as const) : ("idle" as const),
          },
      }),
    ),
    ...board.tasks
      .filter((t) => !t.milestone_id)
      .map((t) => ({
        key: t.key,
        title: t.title,
        href: taskHref(t.project_id, t),
        complete: t.status === "done",
        archived: t.archived,
        planned: t.status === "backlog" || t.status === "up_next",
        attention: attention.has(t.key),
        state: t.state ?? { label: t.status, tone: "idle" as const },
      })),
  ].sort(
    (a, b) =>
      Number(b.attention) - Number(a.attention) ||
      Number(a.planned) - Number(b.planned) ||
      a.key.localeCompare(b.key, undefined, { numeric: true }),
  );
}
export function WorkList({
  board,
  all = false,
  current,
  open,
}: {
  board: WorkspaceBoard;
  all?: boolean;
  current?: string;
  open?: (url: string) => void;
}) {
  const [order, setOrder] = useState<string[]>([]);
  const [count, setCount] = useState(40);
  const items = workItems(board).filter(
    (item) => all || ((!item.complete || item.attention) && !item.archived),
  );
  const added = items
    .filter((item) => !order.includes(item.key))
    .map((item) => item.key);
  const ordering = added.length ? [...order, ...added] : order;
  if (added.length) setOrder(ordering);
  const group = (item: (typeof items)[number]) =>
    item.attention && !item.archived
      ? 0
      : item.complete || item.archived
        ? 3
        : item.planned
          ? 2
          : 1;
  const groups = [
    "Needs attention",
    "In progress",
    "Planned",
    "Completed and archived",
  ];
  items.sort(
    (a, b) =>
      group(a) - group(b) || ordering.indexOf(a.key) - ordering.indexOf(b.key),
  );
  return (
    <>
      <div className={all ? "work-resource-groups" : "work-navigation-list"}>
        {groups.map((name, index) => {
          const members = items
            .slice(0, count)
            .filter((item) => group(item) === index);
          if (!members.length) return null;
          return (
            <section key={name} aria-label={all ? name : undefined}>
              <h3 className="work-group-label">{name}</h3>
              <div className={all ? "work-resource-list" : undefined}>
                {members.map((item) => (
                  <div key={item.key}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <WorkspaceLink
                          to={item.href}
                          className="work-navigation-row"
                          aria-current={
                            canonicalResourceRoot(current ?? "", board) ===
                            item.href
                              ? "page"
                              : undefined
                          }
                          aria-label={`${item.key} ${item.title}`}
                          onClick={
                            open
                              ? (event) =>
                                  followLink(event, () => open(item.href))
                              : undefined
                          }
                        >
                          <WorkState
                            state={item.state}
                            indicatorOnly
                            decorative
                          />
                          <span className="work-navigation-key">
                            {item.key}
                          </span>
                          <span className="work-navigation-title">
                            {item.title}
                          </span>
                        </WorkspaceLink>
                      </TooltipTrigger>
                      <TooltipContent side="right">
                        {item.key} {item.title} · {item.state.label}
                        {item.archived ? " · Archived" : ""}
                      </TooltipContent>
                    </Tooltip>
                  </div>
                ))}
              </div>
            </section>
          );
        })}
        {!items.length && (
          <p className="empty-column">
            {all ? "No work yet" : "No active work"}
          </p>
        )}
      </div>
      {count < items.length && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setCount((n) => n + 40)}
        >
          Load more work
        </Button>
      )}
    </>
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
  const resources = [
    { name: "Projects", href: base + "/projects", Icon: Folder },
    { name: "Documents", href: base + "/documents", Icon: FileText },
    { name: "Board", href: base, Icon: Columns3 },
    { name: "Milestones", href: base + "/milestones", Icon: Flag },
    { name: "Archive", href: base + "/archive", Icon: Archive },
  ];
  return (
    <>
      <nav className="workspace-resources" aria-label="Workspace resources">
        {resources.map(({ name, href, Icon }) => (
          <a
            key={name}
            href={href}
            aria-current={current === href ? "page" : undefined}
            onClick={(event) => followLink(event, () => open(href))}
          >
            <Icon size={16} />
            <span>{name}</span>
          </a>
        ))}
        <a
          href={base + "/edit"}
          onClick={(event) => followLink(event, () => open(base + "/edit"))}
        >
          <Settings size={16} /> Workspace settings
        </a>
      </nav>
      <div className="work-navigation-heading">
        <span>Work</span>
        <a
          href={base + "/work"}
          onClick={(event) => followLink(event, () => open(base + "/work"))}
        >
          All work
        </a>
      </div>
      <nav className="work-navigation-scroll" aria-label="Work">
        <WorkList board={board} current={current} open={open} />
      </nav>
    </>
  );
}
