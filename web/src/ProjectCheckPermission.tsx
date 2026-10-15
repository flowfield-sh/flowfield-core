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
    <DetailSection title="Worker check permission">
      <SettingsCheckbox
        checked={pending ?? resource.data?.enabled ?? false}
        disabled={disabled || busy || !resource.data}
        hint="Saves immediately. Workers can run the saved check commands on their edited code with Flowfield’s host access. Changing integration settings requires fresh permission. Revocation blocks future calls; use Stop worker to stop running checks."
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
        Allow workers to run saved project checks
      </SettingsCheckbox>
      <ResourceRetry resources={[resource]} />
    </DetailSection>
  );
}
