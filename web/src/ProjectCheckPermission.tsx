import { useState } from "react";
import { DetailSection } from "./DetailLayout";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { ResourceRetry } from "./ResourceRetry";
import { useResource } from "./useResource";
import { request } from "./workspace";
import { reportError } from "./requestFeedback";
import type { components } from "./api-schema";
type Permission = components["schemas"]["CheckPermission"];
export function ProjectCheckPermission({
  projectId,
  revision,
  disabled,
  refresh,
}: {
  projectId: string;
  revision: number;
  disabled: boolean;
  refresh: unknown;
}) {
  const path = `projects/${projectId}/check-permission`;
  const resource = useResource<Permission>(path, `${revision}:${refresh}`);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<boolean | null>(null);
  return (
    <DetailSection title="Check execution">
      <SettingsCheckbox
        checked={pending ?? resource.data?.enabled ?? false}
        disabled={disabled || busy || resource.loading || !resource.data}
        hint="Lets a worker ask Flowfield to run the saved validation commands when its sandbox cannot run them. Commands use this machine’s access on worker-edited code. Ordinary sandboxed tests need no permission here. Saves immediately; changing integration settings revokes permission."
        onChange={async (event) => {
          if (!resource.data) return;
          setBusy(true);
          const enabled = event.target.checked;
          setPending(enabled);
          try {
            const value = await request<Permission>(path, "PUT", {
              expected_revision: resource.data.revision,
              settings_revision: revision,
              enabled,
            });
            resource.invalidate();
            resource.setData(value);
          } catch (error) {
            reportError(error, "Could not update check permission");
          } finally {
            setBusy(false);
            setPending(null);
          }
        }}
      >
        Allow saved checks outside the worker sandbox
      </SettingsCheckbox>
      <ResourceRetry resources={[resource]} />
    </DetailSection>
  );
}
