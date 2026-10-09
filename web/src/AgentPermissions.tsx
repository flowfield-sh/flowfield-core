import { useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request } from "./workspace";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ContentStack, DetailSection } from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { taskHref } from "./navigation";
import { Card } from "@/components/ui/card";

type Permission = components["schemas"]["PermissionRecord"];

export function PermissionControl({
  record,
  answered,
}: {
  record: Permission;
  answered: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <DetailSection title={record.title}>
      <p className="detail-metadata">
        {record.role === "worker" ? "Worker" : "Coordinator"} tool permission ·{" "}
        <Timestamp date={record.created_at} />
      </p>
      {record.details && (
        <pre className="evidence-output">{record.details}</pre>
      )}
      {record.status === "pending" ? (
        <>
          <p>
            The agent needs permission to continue. Code approval is separate.
          </p>
          <div className="actions">
            {record.options.map((option) => (
              <Button
                key={option.id}
                size="sm"
                variant="outline"
                className="h-auto min-h-8 max-w-full whitespace-normal wrap-anywhere py-1.5 text-left"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    await request(
                      `projects/${record.project_id}/permissions/${record.id}/answer`,
                      "POST",
                      {
                        expected_revision: record.revision,
                        option_id: option.id,
                      },
                    );
                    answered();
                  } catch (error) {
                    setError((error as Error).message);
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
        </>
      ) : (
        <p>
          {record.status === "answered"
            ? `${record.options.find((option) => option.id === record.answer)?.label ?? "Answered"}${record.released_at ? " · Answer recorded" : " · Awaiting delivery"}`
            : record.status === "expired"
              ? "Expired. No permission was delivered."
              : `Cancelled.${record.answer ? " The saved answer was not delivered." : " No permission was delivered."}`}
        </p>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </DetailSection>
  );
}

export function AgentPermissions({
  projectId,
  taskId,
  role,
  refresh,
}: {
  projectId: string;
  taskId?: string;
  role?: "worker" | "coordinator";
  refresh: unknown;
}) {
  const [retry, setRetry] = useState(0);
  const path = `projects/${projectId}/permissions?retry=${retry}${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ""}${role ? `&role=${role}` : ""}`;
  const page = useResource<components["schemas"]["PermissionPage"]>(
    path,
    refresh,
  );
  const pending = page.data?.pending ?? [];
  return (
    <>
      {page.error && (
        <Alert variant="destructive">
          <AlertDescription>
            {page.error}{" "}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setRetry(retry + 1)}
            >
              Reload permissions
            </Button>
          </AlertDescription>
        </Alert>
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
  projectId,
  id,
  taskKey,
  refresh,
}: {
  projectId: string;
  id: string;
  taskKey: string | null;
  refresh: unknown;
}) {
  const [retry, setRetry] = useState(0);
  const resource = useResource<Permission>(
    `projects/${projectId}/permissions/${id}?retry=${retry}`,
    refresh,
  );
  return (
    <Card className="p-3 gap-0 shadow-none">
      <ContentStack>
        {taskKey && (
          <WorkspaceLink to={taskHref(projectId, { key: taskKey })}>
            {taskKey}
          </WorkspaceLink>
        )}
        {resource.error && (
          <p role="alert">
            {resource.error}{" "}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setRetry(retry + 1)}
            >
              Retry
            </Button>
          </p>
        )}
        {resource.data && (
          <PermissionControl
            record={resource.data}
            answered={() => setRetry(retry + 1)}
          />
        )}
      </ContentStack>
    </Card>
  );
}
