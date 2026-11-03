import { useMemo, useState } from "react";
import { useResource } from "./useResource";
import { request } from "./workspace";
import type { components } from "./api-schema";

type Settings = components["schemas"]["InspectionSettings"];

// Run instructions retain their own revision: editing them never revokes code approval.
export function useInspectionSettings(projectId: string, refresh: unknown) {
  const path = `projects/${projectId}/inspection/settings`;
  const [retry, setRetry] = useState(0);
  const key = useMemo(() => ({ refresh, retry }), [refresh, retry]);
  const resource = useResource<Settings>(path, key);
  const [draft, setDraft] = useState<{
    command: string;
    reason: string;
    noPreview: boolean;
    revision: number;
  } | null>(null);
  const fields = draft ?? {
    command: resource.data?.run_command ?? "",
    reason: resource.data?.no_preview_reason ?? "",
    noPreview: !!resource.data?.no_preview_reason,
    revision: resource.data?.revision ?? 1,
  };
  return {
    binding: JSON.stringify([path, resource.data?.revision, draft]),
    command: fields.command,
    reason: fields.reason,
    noPreview: fields.noPreview,
    valid: !!(fields.noPreview ? fields.reason.trim() : fields.command.trim()),
    dirty: draft !== null,
    loaded: !!resource.data,
    error: resource.error,
    retry: resource.retry,
    stale:
      !!draft && !!resource.data && draft.revision !== resource.data.revision,
    change(command: string) {
      if (resource.data)
        setDraft({
          ...fields,
          command,
          revision: draft?.revision ?? resource.data.revision,
        });
    },
    changeReason(reason: string) {
      if (resource.data) setDraft({ ...fields, reason });
    },
    chooseNoPreview(noPreview: boolean) {
      if (resource.data) setDraft({ ...fields, noPreview });
    },
    loadCurrent() {
      setDraft(null);
    },
    async save() {
      if (!draft) return;
      try {
        const value = await request<Settings>(path, "PUT", {
          expected_revision: draft.revision,
          run_command: draft.noPreview ? "" : draft.command,
          no_preview_reason: draft.noPreview ? draft.reason : "",
        });
        resource.invalidate();
        resource.setData(value);
        setDraft(null);
      } catch (error) {
        setRetry((value) => value + 1);
        throw error;
      }
    },
  };
}
