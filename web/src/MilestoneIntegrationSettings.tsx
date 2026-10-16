import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { ContentStack } from "./DetailLayout";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { Button } from "@/components/ui/button";
import { ResourceRetry } from "./ResourceRetry";
import type { DirtyCallback } from "./retainedState";

type Policy = components["schemas"]["TaskIntegrationPolicyView"];
const fields = (record?: Policy) => ({ enabled: record?.enabled ?? false });

export function MilestoneIntegrationSettings({
  path,
  refresh,
  onDirty,
}: {
  path: string;
  refresh: unknown;
  onDirty: DirtyCallback;
}) {
  const endpoint = `${path}/delivery/integration-policy`;
  const resource = useResource<Policy>(endpoint, refresh);
  const state = useRecordEditor({
    incoming: resource.data ?? undefined,
    recordName: "integration policy",
    fields,
    path: () => endpoint,
    onDirty,
    saved: (record: Policy) => resource.setData(record),
  });
  const { loaded, values, change, run, busy, dirty, newer, cancel, element } =
    state;
  return (
    <section ref={element} aria-label="Task integration settings">
      <ContentStack space="section">
        <EditorFeedback state={state} />
        <ResourceRetry resources={[resource]} />
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run("PUT", {
              expected_revision: loaded!.revision,
              delivery_id: loaded!.delivery_id,
              branch: loaded!.branch,
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
              checked={values.enabled}
              onChange={(event) => change("enabled", event.target.checked)}
            >
              Automatically integrate reviewed tasks into{" "}
              {loaded?.branch ?? "the milestone branch"}
            </SettingsCheckbox>
            <p>Final delivery still requires your approval.</p>
            <div className="actions">
              <Button size="sm" disabled={!dirty || newer}>
                {busy ? "Saving…" : "Save integration policy"}
              </Button>
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
