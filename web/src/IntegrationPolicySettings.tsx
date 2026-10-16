import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { ContentStack } from "./DetailLayout";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { Button } from "@/components/ui/button";
import { ResourceRetry } from "./ResourceRetry";
import type { DirtyCallback } from "./retainedState";

type Policy =
  | components["schemas"]["TaskIntegrationPolicyView"]
  | components["schemas"]["WorkspaceIntegrationPolicy"];
const fields = (record?: Policy) => ({ enabled: record?.enabled ?? null });

export function IntegrationPolicySettings({
  path,
  refresh,
  onDirty,
  milestone = false,
}: {
  path: string;
  milestone?: boolean;
  refresh: unknown;
  onDirty: DirtyCallback;
}) {
  const endpoint = `${path}${milestone ? "/delivery" : ""}/integration-policy`;
  const resource = useResource<Policy>(endpoint, refresh);
  const state = useRecordEditor({
    incoming: resource.data ?? undefined,
    recordName: "integration policy",
    version: (record) =>
      `${record.revision}:${"workspace_revision" in record ? record.workspace_revision : ""}`,
    fields,
    path: () => endpoint,
    onDirty,
    saved: (record: Policy) => resource.setData(record),
  });
  const { loaded, values, change, run, busy, dirty, newer, cancel, element } =
    state;
  const local = loaded && "branch" in loaded ? loaded : null;
  const enabled = values.enabled ?? local?.effective_enabled ?? false;
  const binding = () => ({
    expected_revision: loaded!.revision,
    ...(local
      ? {
          delivery_id: local.delivery_id,
          branch: local.branch,
          expected_workspace_revision: local.workspace_revision,
        }
      : {}),
  });
  return (
    <section ref={element} aria-label="Task integration settings">
      <ContentStack space="section">
        <EditorFeedback state={state} />
        <ResourceRetry resources={[resource]} />
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run("PUT", {
              ...binding(),
              ...values,
            });
          }}
        >
          <fieldset
            disabled={busy || !loaded}
            className="content-stack"
            data-space="section"
          >
            <SettingsCheckbox
              checked={enabled}
              onChange={(event) => change("enabled", event.target.checked)}
            >
              Automatically integrate reviewed tasks
            </SettingsCheckbox>
            <p>
              {local ? (
                <>
                  Into <code>{local.branch}</code>.{" "}
                  {values.enabled === null
                    ? "Uses workspace default. "
                    : "Milestone override. "}
                </>
              ) : (
                "Into each milestone’s branch. "
              )}
              Final delivery requires your approval.
            </p>
            <div className="actions">
              <Button size="sm" disabled={!dirty || newer}>
                {busy ? "Saving…" : "Save integration policy"}
              </Button>
              {milestone && (
                <Button
                  size="sm"
                  type="button"
                  variant="outline"
                  disabled={
                    !loaded ||
                    busy ||
                    newer ||
                    (loaded.enabled === null && !dirty)
                  }
                  onClick={() =>
                    void run("PUT", { ...binding(), enabled: null })
                  }
                >
                  Reset to workspace
                </Button>
              )}
              <Button
                size="sm"
                type="button"
                variant="outline"
                disabled={!dirty}
                onClick={cancel}
              >
                Cancel
              </Button>
            </div>
          </fieldset>
        </form>
      </ContentStack>
    </section>
  );
}
