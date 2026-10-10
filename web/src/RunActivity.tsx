import { ResourceRetry } from "./ResourceRetry";
import { useLayoutEffect, useRef } from "react";
import { useResource } from "./useResource";
import { label } from "./workspace";
import type { components } from "./api-schema";
import { Disclosure } from "./DetailLayout";
import { UsageSummary } from "./UsageSummary";
import { ContextRing } from "./ContextRing";
import { MessageSquare, Wrench, Terminal, AlignLeft, Info } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type Page = components["schemas"]["RunActivityPage"];
const isActive = (page: Page) => page.active;
const activityIcons = {
  agent: MessageSquare,
  tool: Wrench,
  command: Terminal,
  output: AlignLeft,
  status: Info,
};
export function RunActivity({
  projectId,
  runId,
}: {
  projectId: string;
  runId: string;
}) {
  const resource = useResource<Page>(
    `projects/${projectId}/runs/${runId}/activity`,
    runId,
    10000,
    { projectId, attemptId: runId, isActive },
  );
  const { data: page, error } = resource;
  const pane = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useLayoutEffect(() => {
    if (follow.current && pane.current)
      pane.current.scrollTop = pane.current.scrollHeight;
  }, [page]);
  return (
    <div className="run-activity content-stack" data-space="tight">
      <ResourceRetry resources={[resource]}>Retry activity</ResourceRetry>
      {!page && !error && <p className="muted">Loading activity…</p>}
      {page && !page.supported && (
        <p className="muted">
          {page.active
            ? "Waiting for public worker activity…"
            : "This attempt has no recorded activity feed."}
        </p>
      )}
      {page?.supported && (
        <div
          ref={pane}
          className="run-activity-output"
          role="region"
          aria-label="Worker activity"
          tabIndex={0}
          onScroll={() => {
            const element = pane.current!;
            follow.current =
              element.scrollHeight - element.scrollTop - element.clientHeight <
              24;
          }}
        >
          {page.omitted && (
            <p className="muted">Earlier activity was omitted.</p>
          )}
          <ActivityEntries items={page.items} />{" "}
        </div>
      )}
      {page?.context && (
        <div className="activity-context">
          <ContextRing context={page.context} active={page.active} />
          <span className="detail-metadata">Current tokens</span>
        </div>
      )}
      {page?.usage?.total_tokens != null && (
        <UsageSummary usage={page.usage} active={page.active} />
      )}
    </div>
  );
}

export function ActivityEntries({ items }: { items: Page["items"] }) {
  return (
    <>
      {items.map((item) => {
        const Icon = activityIcons[item.kind];
        return (
          <div
            key={item.key}
            className="run-activity-entry"
            data-kind={item.kind}
          >
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  tabIndex={0}
                  className="run-activity-icon"
                  aria-label={label(item.kind)}
                >
                  <Icon size={14} aria-hidden="true" />
                </span>
              </TooltipTrigger>
              <TooltipContent>{label(item.kind)}</TooltipContent>
            </Tooltip>
            <div>
              {item.omitted && <p className="muted">Some output omitted.</p>}
              <pre>{item.preview || item.text}</pre>
              {item.abridged && (
                <Disclosure summary={<>Retained output</>}>
                  <pre>{item.text}</pre>
                </Disclosure>
              )}
            </div>
          </div>
        );
      })}
    </>
  );
}
