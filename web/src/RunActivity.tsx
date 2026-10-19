import { ResourceRetry } from "./ResourceRetry";
import { lazy, Suspense, useState } from "react";
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
const ActivityPatch = lazy(() => import("./ActivityPatch"));
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
  review = false,
  reviewAttemptId,
}: {
  projectId: string;
  runId: string;
  review?: boolean;
  reviewAttemptId?: string;
}) {
  const resource = useResource<Page>(
    `workspaces/${projectId}/${review ? "reviews" : "runs"}/${runId}${reviewAttemptId ? `/attempts/${reviewAttemptId}` : ""}/activity`,
    runId,
    10000,
    { projectId, attemptId: reviewAttemptId ?? runId, isActive },
  );
  const { data: page, error } = resource;
  return (
    <div className="run-activity content-stack">
      <ResourceRetry resources={[resource]}>Retry activity</ResourceRetry>
      {!page && !error && <p className="muted">Loading activity…</p>}
      {page && !page.supported && (
        <p className="muted">
          {page.active
            ? "Waiting for public activity…"
            : "This attempt has no recorded activity feed."}
        </p>
      )}
      {page?.supported && (
        <div
          className="run-activity-output content-stack"
          role="region"
          aria-label={review ? "Review activity" : "Worker activity"}
          tabIndex={0}
        >
          {page.omitted && (
            <p className="muted">Earlier activity was omitted.</p>
          )}
          <ActivityEntries items={page.items} />{" "}
        </div>
      )}
      {(page?.context || page?.usage?.total_tokens != null) && (
        <div className="activity-context">
          {page.context && (
            <ContextRing context={page.context} active={page.active} inline />
          )}
          {page.usage?.total_tokens != null && (
            <UsageSummary usage={page.usage} active={page.active} />
          )}
        </div>
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
              {item.action ? (
                <ActionEntry action={item.action} kind={item.kind} />
              ) : (
                <ActivityText text={item.text} preview={item.preview} />
              )}
            </div>
          </div>
        );
      })}
    </>
  );
}

function ActionEntry({
  action,
  kind,
}: {
  action: NonNullable<Page["items"][number]["action"]>;
  kind: Page["items"][number]["kind"];
}) {
  return (
    <Disclosure
      summary={action.title}
      chevron={false}
      className="activity-action"
    >
      <ActionDetails text={action.details} />
      {action.output ? (
        <ActionResult
          text={action.output}
          flowfield={action.title.startsWith("Flowfield · ")}
        />
      ) : (
        (kind === "command" || !action.details) && (
          <p>No output was recorded for this event.</p>
        )
      )}
    </Disclosure>
  );
}

function formatEvidence(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

const resultFields: Record<string, string> = {
  title: "Title",
  name: "Name",
  body: "Description",
  description: "Description",
  summary: "Summary",
  question: "Question",
  answer: "Answer",
  status: "Status",
  problem: "Problem",
  message: "Message",
  acceptance_criteria: "Acceptance criteria",
  checks: "Checks",
  limitations: "Limitations",
  next_action: "Next action",
};

/** Flowfield records lead with authored work; protocol metadata remains inspectable. */
function ActionResult({
  text,
  flowfield,
}: {
  text: string;
  flowfield: boolean;
}) {
  let value: unknown;
  if (flowfield) {
    try {
      value = JSON.parse(text);
    } catch {
      /* Partial evidence uses the verbatim fallback. */
    }
  }
  const record =
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  const fields = record
    ? Object.entries(record).filter(
        ([key, value]) => resultFields[key] && value != null && value !== "",
      )
    : [];
  if (!fields.length) return <Evidence text={text} />;
  return (
    <div className="activity-console" tabIndex={0}>
      <dl className="activity-record">
        {fields.map(([key, value]) => (
          <div key={key}>
            <dt>{resultFields[key]}</dt>
            <dd>
              <pre>
                {typeof value === "string"
                  ? value
                  : JSON.stringify(value, null, 2)}
              </pre>
            </dd>
          </div>
        ))}
      </dl>
      <Disclosure summary="Saved response" chevron={false}>
        <pre>{formatEvidence(text)}</pre>
      </Disclosure>
    </div>
  );
}

function Evidence({ text }: { text: string }) {
  const plain = (
    <pre className="activity-console" tabIndex={0}>
      {formatEvidence(text)}
    </pre>
  );
  return text.includes("diff --git ") ? (
    <Suspense fallback={plain}>
      <ActivityPatch text={text} />
    </Suspense>
  ) : (
    plain
  );
}

/** Known parameter names express intent; unknown tools retain their actual fields. */
function ActionDetails({ text }: { text: string }) {
  if (!text) return null;
  if (text.startsWith("Arguments: ")) {
    let args: unknown;
    try {
      args = JSON.parse(text.slice("Arguments: ".length));
    } catch {
      /* Preserve truncated evidence below. */
    }
    if (args && typeof args === "object" && !Array.isArray(args)) {
      if (!Object.keys(args).length) return null;
      const names: Record<string, string> = {
        cmd: "Command",
        command: "Command",
        cwd: "Directory",
        workdir: "Directory",
        path: "Path",
        file_path: "File",
        query: "Search",
        pattern: "Pattern",
        project_id: "Project",
        task_id: "Task",
      };
      return (
        <dl className="activity-parameters">
          {Object.entries(args).map(([key, value]) => (
            <div key={key}>
              <dt>{names[key] ?? label(key)}</dt>
              <dd>
                <pre>
                  {typeof value === "string"
                    ? value
                    : JSON.stringify(value, null, 2)}
                </pre>
              </dd>
            </div>
          ))}
        </dl>
      );
    }
  }
  return <Evidence text={text} />;
}

function ActivityText({ text, preview }: { text: string; preview: string }) {
  const [open, setOpen] = useState(false);
  if (!preview || preview === text) return <pre>{text}</pre>;
  // Older unstructured entries carry semantic omission/exit facts in their preview.
  return (
    <Disclosure
      className="activity-evidence"
      chevron={false}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      summary={open ? preview.split("\n", 1)[0] : <pre>{preview}</pre>}
    >
      <pre>{text}</pre>
    </Disclosure>
  );
}
