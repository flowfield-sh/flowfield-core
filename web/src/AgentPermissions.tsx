import { PaneFooter } from "./EntityPane";
import { RequestFrame } from "./RequestFrame";
import { reportError } from "./requestFeedback";
import { useEffect, useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request } from "./workspace";
import { Button } from "@/components/ui/button";
import { ContentStack, DetailSection } from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { taskHref } from "./navigation";

type Permission = components["schemas"]["PermissionRecord"];

export function PermissionControl({
  record,
  answered,
  inRequest = false,
  flat = false,
}: {
  inRequest?: boolean;
  flat?: boolean;
  record: Permission;
  answered: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <DetailSection
      className={
        record.status === "pending" && !inRequest && !flat
          ? "rounded-md border border-border bg-muted p-4"
          : undefined
      }
      title={record.title}
      data-announcement-source={`${record.workspace_id}:permission:${record.id}`}
    >
      <p className="detail-metadata">
        {record.role === "worker" ? "Worker" : "Coordinator"} tool permission ·{" "}
        <Timestamp date={record.created_at} />
      </p>
      {record.status === "pending" && record.task_id && !inRequest && (
        <p className="detail-metadata">
          <WorkspaceLink
            to={
              taskHref(record.workspace_id, { key: record.task_id }) +
              "/settings"
            }
          >
            Task access settings
          </WorkspaceLink>{" "}
          · Changes apply to the next attempt.
        </p>
      )}
      {record.details && (
        <pre className="evidence-output">{record.details}</pre>
      )}
      {record.status === "pending" ? (
        <PaneFooter inline={!inRequest}>
          <div className={inRequest ? "task-input actions" : "actions"}>
            {record.options.map((option) => (
              <Button
                key={option.id}
                size="sm"
                variant="outline"
                className="h-auto min-h-8 max-w-full whitespace-normal wrap-anywhere py-1.5 text-left"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await request(
                      `workspaces/${record.workspace_id}/permissions/${record.id}/answer`,
                      "POST",
                      {
                        expected_revision: record.revision,
                        option_id: option.id,
                      },
                    );
                    answered();
                  } catch (error) {
                    reportError(error, "Could not send permission decision");
                    answered();
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {option.label}
              </Button>
            ))}
          </div>
        </PaneFooter>
      ) : (
        <p>
          {record.status === "answered"
            ? `${record.options.find((option) => option.id === record.answer)?.label ?? "Answered"}${record.released_at ? " · Answer recorded" : " · Awaiting delivery"}`
            : record.status === "expired"
              ? "Expired. No permission was delivered."
              : `Cancelled.${record.answer ? " The saved answer was not delivered." : " No permission was delivered."}`}
        </p>
      )}
    </DetailSection>
  );
}

export function AgentPermissions({
  workspaceId,
  taskId,
  role,
  refresh,
  onPendingTurns,
  inTask = false,
}: {
  workspaceId: string;
  taskId?: string;
  role?: "worker" | "coordinator";
  refresh: unknown;
  onPendingTurns?: (ids: string[]) => void;
  inTask?: boolean;
}) {
  const [retry, setRetry] = useState(0);
  const path = `workspaces/${workspaceId}/permissions?retry=${retry}${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ""}${role ? `&role=${role}` : ""}`;
  const page = useResource<components["schemas"]["PermissionPage"]>(
    path,
    refresh,
  );
  const pending = page.data?.pending ?? [];
  useEffect(() => {
    onPendingTurns?.(page.data?.pending.map((item) => item.turn_id) ?? []);
  }, [page.data, onPendingTurns]);
  return (
    <>
      {page.error && (
        <Button variant="outline" size="sm" onClick={() => setRetry(retry + 1)}>
          Reload permissions
        </Button>
      )}
      {!!pending.length && (
        <ContentStack
          space="section"
          aria-label="Tool permissions"
          className={inTask ? "task-input" : undefined}
        >
          {pending.map((record) => (
            <PermissionControl
              key={record.id}
              record={record}
              flat={inTask}
              answered={() => setRetry((n) => n + 1)}
            />
          ))}
        </ContentStack>
      )}
    </>
  );
}

export function PermissionCard({
  workspaceId,
  id,
  taskKey,
  refresh,
  inline = false,
}: {
  inline?: boolean;
  workspaceId: string;
  id: string;
  taskKey: string | null;
  refresh: unknown;
}) {
  const [retry, setRetry] = useState(0);
  const resource = useResource<Permission>(
    `workspaces/${workspaceId}/permissions/${id}?retry=${retry}`,
    refresh,
  );
  const content = (
    <>
      {resource.error && (
        <Button variant="outline" size="sm" onClick={() => setRetry(retry + 1)}>
          Retry
        </Button>
      )}
      {resource.data && (
        <PermissionControl
          record={resource.data}
          inRequest
          answered={() => setRetry(retry + 1)}
        />
      )}
    </>
  );
  if (inline) return content;
  return (
    <RequestFrame
      kind="Tool permission"
      state={{
        label:
          resource.data?.status === "pending"
            ? "Needs permission"
            : (resource.data?.status ?? "Loading permission"),
        tone: resource.data?.status === "pending" ? "waiting" : "idle",
      }}
      label="Tool permission"
      source={
        taskKey &&
        workspaceId && (
          <WorkspaceLink to={taskHref(workspaceId, { key: taskKey })}>
            {taskKey}
          </WorkspaceLink>
        )
      }
    >
      {content}
    </RequestFrame>
  );
}
