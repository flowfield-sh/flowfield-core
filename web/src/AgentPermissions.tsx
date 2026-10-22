import { PaneFooter } from "./EntityPane";
import { reportError } from "./requestFeedback";
import { useEffect, useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request } from "./workspace";
import { Button } from "@/components/ui/button";
import { OverflowButton } from "./OverflowButton";
import { ContentStack, DetailSection } from "./DetailLayout";
import { Timestamp } from "./Timestamp";

type Permission = components["schemas"]["PermissionRecord"];

export function PermissionControl({
  record,
  answered,
  inRequest = false,
}: {
  inRequest?: boolean;
  record: Permission;
  answered: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <DetailSection
      className={
        record.status === "pending" && !inRequest
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
      {record.details && (
        <pre className="evidence-output">{record.details}</pre>
      )}
      {record.status === "pending" ? (
        <PaneFooter inline={!inRequest}>
          <div className={inRequest ? "task-input actions" : "actions"}>
            {record.options.map((option) => (
              <OverflowButton
                key={option.id}
                label={option.label}
                size="sm"
                variant="outline"
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
              />
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
  role,
  refresh,
  onPendingTurns,
}: {
  workspaceId: string;
  role?: "worker" | "coordinator";
  refresh: unknown;
  onPendingTurns?: (ids: string[]) => void;
}) {
  const [retry, setRetry] = useState(0);
  const path = `workspaces/${workspaceId}/permissions?retry=${retry}${role ? `&role=${role}` : ""}`;
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
        <ContentStack space="section" aria-label="Tool permissions">
          {pending.map((record) => (
            <PermissionControl
              key={record.id}
              record={record}
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
  refresh,
}: {
  workspaceId: string;
  id: string;
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
  return content;
}
