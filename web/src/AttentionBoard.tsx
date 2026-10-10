import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { components } from "./api-schema";
import { usePage } from "./useResource";
import { AttentionCard } from "./AttentionCard";
import { taskHref } from "./navigation";
import { questionHref } from "./Inbox";
import { label } from "./workspace";
import { WorkState } from "./WorkState";
import { PermissionCard } from "./AgentPermissions";
import { ContentStack } from "./DetailLayout";

type Item = components["schemas"]["AttentionItem"];
type Page = components["schemas"]["AttentionPage"];
const columns = {
  action: "Needs your action",
  waiting: "Waiting",
  history: "History",
} as const;
function action(item: Item) {
  if (item.next_action) return item.next_action;
  if (item.kind === "result")
    return (
      (
        {
          preparing: "Checking changes",
          ready: "Review changes",
          delivering: "Delivering changes",
          delivered:
            item.code_available === false
              ? "Check code on the branch"
              : "Completed",
          cancelled: "Recheck changes",
          blocked: "Inspect problem",
          stale: "Delivery needs attention",
          changes_requested: "Changes requested",
        } as Record<string, string>
      )[item.status] ?? label(item.status)
    );
  if (item.kind === "integration")
    return (
      (
        {
          preparing: "Validating integration",
          ready: "Ready to integrate",
          applying: "Applying integration",
          integrated: "Integrated",
          failed: "Inspect integration failure",
          stale: "Revalidate integration",
        } as Record<string, string>
      )[item.status] ?? label(item.status)
    );
  const questionActions: Record<string, string> = {
    open: "Answer question",
    answered: "Resume coordinator",
    assigned: "Answer sent",
    applied: "Answered",
    withdrawn: "Withdrawn",
  };
  if (item.kind === "question")
    return questionActions[item.status] ?? label(item.status);
  const runActions: Record<string, string> = {
    in_review: "Review result",
    failed: "Inspect failure",
    uncertain: "Reconcile worker",
    changes_requested: "Changes requested",
    preparing: "Preparing follow-up",
    running: "Working on follow-up",
    stopping: "Stopping worker",
    stopped: "Stopped",
    waiting_for_input: "Question raised",
    accepted: item.code_available
      ? "Accepted · code available"
      : "Accepted · awaiting integration",
  };
  return runActions[item.status] ?? label(item.status);
}

function Column({
  projectId,
  identity,
  refresh,
  column,
}: {
  projectId: string;
  identity?: string;
  refresh: unknown;
  column: keyof typeof columns;
}) {
  const [retry, setRetry] = useState(0);
  const page = usePage<Page>(
    `projects/${projectId}/view/attention?column=${column}&retry=${retry}`,
    refresh,
  );
  return (
    <section className="column attention-column" aria-label={columns[column]}>
      <h2>
        {columns[column]}{" "}
        {page.data && <span className="column-count">{page.data.total}</span>}
      </h2>
      <div
        className="column-cards"
        role="region"
        aria-label={`${columns[column]} items`}
        tabIndex={0}
      >
        <ContentStack>
          {page.error && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setRetry((value) => value + 1)}
            >
              Retry
            </Button>
          )}
          {page.loading && !page.data && <p>Loading…</p>}
          {page.data && !page.data.items.length && (
            <p className="muted">
              {column === "action"
                ? "Nothing needs your attention."
                : column === "waiting"
                  ? "Nothing waiting on a worker, coordinator or service."
                  : "No past items yet."}
            </p>
          )}
          <ul>
            {page.data?.items.map((item) => (
              <li key={`${item.kind}:${item.id}`}>
                {item.kind === "permission" ? (
                  <PermissionCard
                    projectId={projectId}
                    id={item.id}
                    taskKey={item.task_key}
                    refresh={refresh}
                  />
                ) : (
                  <AttentionCard
                    href={
                      item.state?.href ??
                      (item.kind === "question"
                        ? questionHref(projectId, item.id)
                        : item.kind === "result"
                          ? `${taskHref(projectId, { key: item.task_key! }, "result")}/${item.id}`
                          : `${taskHref(projectId, { key: item.task_key! }, "runs")}/${item.run_id ?? item.id}${item.kind === "integration" ? `/integrations/${item.id}` : ""}`)
                    }
                    kind={item.kind === "result" ? "Changes" : label(item.kind)}
                    reference={item.task_key ?? "Project"}
                    title={item.title}
                    selected={identity === item.id}
                    action={
                      item.state ? (
                        <WorkState state={item.state} />
                      ) : column === "history" ? (
                        label(item.status)
                      ) : (
                        action(item)
                      )
                    }
                  />
                )}
              </li>
            ))}
          </ul>
          {page.data?.next_offset != null && (
            <Button
              size="sm"
              variant="outline"
              className="quiet"
              disabled={page.loadingMore || page.loading}
              onClick={() => void page.older()}
            >
              Load more
            </Button>
          )}
        </ContentStack>
      </div>
    </section>
  );
}
export function AttentionBoard(props: {
  projectId: string;
  identity?: string;
  refresh: unknown;
}) {
  return (
    <div className="board attention-board">
      <Column {...props} column="action" />
      <Column {...props} column="waiting" />
      <Column {...props} column="history" />
    </div>
  );
}
