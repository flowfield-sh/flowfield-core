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
    revision: number;
  } | null>(null);
  return {
    command: draft?.command ?? resource.data?.run_command ?? "",
    dirty: draft !== null,
    loaded: !!resource.data,
    error: resource.error,
    retry: resource.retry,
    stale:
      !!draft && !!resource.data && draft.revision !== resource.data.revision,
    change(command: string) {
      if (resource.data)
        setDraft({
          command,
          revision: draft?.revision ?? resource.data.revision,
        });
    },
    loadCurrent() {
      setDraft(null);
    },
    async save() {
      if (!draft) return;
      try {
        const value = await request<Settings>(path, "PUT", {
          expected_revision: draft.revision,
          run_command: draft.command,
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
