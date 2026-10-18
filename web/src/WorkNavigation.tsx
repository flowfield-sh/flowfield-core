import { LoadMore } from "./LoadMore";
import { DeliveredNavigation } from "./Delivered";
import { useState } from "react";
import {
  Archive,
  Columns3,
  FileText,
  Files,
  Folder,
  Settings,
} from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { WorkIdentity } from "./WorkIdentity";
import { followLink, taskHref, workspaceHref } from "./navigation";
import { WorkspaceLink } from "./WorkspaceLink";
import { canonicalResourceRoot } from "./WorkspaceTabs";
import type { WorkspaceBoard } from "./workspace";

function workItems(board: WorkspaceBoard) {
  const attention = new Set(board.attention_work_keys ?? []);
  return [
    ...board.tasks.map((t) => ({
      key: t.key,
      title: t.title,
      updatedAt: t.activity_at,
      href: taskHref(t.workspace_id, t),
      complete: t.status === "done",
      archived: t.archived,
      planned: t.status === "backlog" || t.status === "up_next",
      attention: attention.has(t.key),
      state: t.state ?? { label: t.status, tone: "idle" as const },
    })),
  ];
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
  const [count, setCount] = useState(40);
  const items = workItems(board).filter(
    (item) =>
      !item.archived && (item.attention || (!item.complete && !item.planned)),
  );
  const group = (item: (typeof items)[number]) => (item.attention ? 0 : 2);
  const groups = ["Needs attention", "Delivered", "In progress"];
  items.sort(
    (a, b) =>
      group(a) - group(b) ||
      b.updatedAt.localeCompare(a.updatedAt) ||
      b.key.localeCompare(a.key, undefined, { numeric: true }),
  );
  return (
    <>
      <div className={all ? "work-resource-groups" : "work-navigation-list"}>
        {groups.map((name, index) => {
          if (index === 1)
            return (
              <DeliveredNavigation
                key={name}
                workspaceId={board.workspace.id}
                refresh={board}
                current={current}
                open={open}
                all={all}
              />
            );
          const members = items
            .filter((item) => group(item) === index)
            .slice(0, count);
          if (!all && !members.length) return null;
          return (
            <section key={name} aria-label={all ? name : undefined}>
              <h3 className="work-group-label">{name}</h3>
              <div
                className={
                  all && members.length ? "work-resource-list" : undefined
                }
              >
                {members.map((item) => (
                  <div key={item.key}>
                    <Tooltip open={all ? false : undefined}>
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
                          <WorkIdentity
                            state={item.state}
                            itemKey={item.key}
                            title={item.title}
                            decorative={!all}
                          />
                        </WorkspaceLink>
                      </TooltipTrigger>
                      <TooltipContent side="right">
                        {item.key} {item.title} · {item.state.label}
                      </TooltipContent>
                    </Tooltip>
                  </div>
                ))}
              </div>
              {!members.length && (
                <p className="collection-empty">
                  {index === 0
                    ? "Nothing needs attention"
                    : "No work in progress"}
                </p>
              )}
            </section>
          );
        })}
      </div>
      {[0, 2].some(
        (index) => items.filter((item) => group(item) === index).length > count,
      ) && (
        <LoadMore size="sm" onClick={() => setCount((n) => n + 40)}>
          Load more work
        </LoadMore>
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
    { name: "Tasks", href: base + "/tasks", Icon: Columns3 },
    { name: "Documents", href: base + "/documents", Icon: FileText },
    { name: "Files", href: base + "/files", Icon: Files },
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
          <Settings size={16} /> Settings
        </a>
      </nav>
      <div className="work-navigation-heading">
        <span>Work</span>
        <a
          href={base + "/work"}
          onClick={(event) => followLink(event, () => open(base + "/work"))}
        >
          Show all
        </a>
      </div>
      <nav className="work-navigation-scroll" aria-label="Work">
        <WorkList board={board} current={current} open={open} />
      </nav>
    </>
  );
}
