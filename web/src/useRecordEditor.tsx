import {
  useRetainedState,
  retainDirty,
  type DirtyCallback,
} from "./retainedState";
import { reportError } from "./requestFeedback";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useEffect, useRef, useState } from "react";
import { request, RequestError } from "./workspace";

export function useRecordEditor<R extends { revision: number }, V>({
  incoming,
  recordName = "changes",
  fields,
  path,
  saved,
  onDirty,
  otherDirty = false,
  read,
  retainedKey,
  version,
}: {
  incoming?: R;
  recordName?: string;
  fields: (record?: R) => V;
  path: (record?: R) => string;
  saved: (record: R, method: string) => void | Promise<void>;
  onDirty: DirtyCallback;
  retainedKey?: string;
  version?: (record: R) => string;
  otherDirty?: boolean;
  read?: () => Promise<R>;
}) {
  const element = useRef<HTMLElement>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const [loaded, setLoaded] = useRetainedState(
    retainedKey ? `${retainedKey}:loaded` : undefined,
    incoming,
  );
  const [values, setValues] = useRetainedState(
    retainedKey ? `${retainedKey}:values` : undefined,
    () => fields(incoming),
  );
  const [editing, setEditing] = useRetainedState(
    retainedKey ? `${retainedKey}:editing` : undefined,
    !incoming,
  );
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const dirty = JSON.stringify(values) !== JSON.stringify(fields(loaded));
  useEffect(() => {
    if (retainedKey) retainDirty(retainedKey, dirty);
    onDirty(dirty || otherDirty || busy, !!retainedKey && !otherDirty && !busy);
  }, [dirty, otherDirty, onDirty, retainedKey, busy]);
  const newer = !!(
    incoming &&
    loaded &&
    (incoming.revision > loaded.revision ||
      (version && version(incoming) !== version(loaded)))
  );
  function adopt(record: R) {
    setLoaded(record);
    setValues(fields(record));
    setConflict(false);
  }
  if (incoming && (!loaded || newer) && !dirty && !busy) adopt(incoming);
  function change<K extends keyof V>(key: K, value: V[K]) {
    const next = { ...values, [key]: value };
    setValues(next);
    onDirty(
      JSON.stringify(next) !== JSON.stringify(fields(loaded)) || otherDirty,
      !!retainedKey && !otherDirty,
    );
  }
  async function run(method: string, payload?: unknown, suffix = "") {
    const action =
      suffix === "/restore"
        ? "restore"
        : method === "GET"
          ? "load"
          : method === "DELETE"
            ? "delete"
            : method === "POST"
              ? "create"
              : "save";
    setBusy(true);
    try {
      const result =
        method === "GET" && read
          ? await read()
          : await request<R>(path(loaded) + suffix, method, payload);
      if (mounted.current) {
        adopt(result);
        if (method !== "GET") setEditing(false);
        await saved(result, method);
        onDirty(otherDirty);
        if (method === "GET") toast.info("Latest version loaded.");
        else
          toast.success(
            action === "delete"
              ? "Deleted."
              : action === "restore"
                ? "Restored."
                : action === "create"
                  ? "Created."
                  : "Changes saved.",
          );
      }
    } catch (error) {
      if (mounted.current) {
        reportError(error, `Could not ${action} ${recordName}`);
        if (error instanceof RequestError && error.code === "revision_conflict")
          setConflict(true);
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  function cancel() {
    if (!dirty || window.confirm("Discard your unsaved edits?")) {
      setValues(fields(loaded));
      setEditing(false);
      return true;
    }
    return false;
  }
  function reload() {
    if (
      !dirty ||
      window.confirm("Discard unsaved edits and load the latest revision?")
    )
      void run("GET");
  }
  return {
    element,
    loaded,
    values,
    editing,
    setEditing,
    busy,
    conflict,
    dirty,
    newer,
    change,
    run,
    cancel,
    reload,
  };
}

export function EditorFeedback({
  state,
}: {
  state: {
    newer: boolean;
    conflict: boolean;
    busy: boolean;
    reload: () => void;
  };
}) {
  return (
    <>
      {(state.newer || state.conflict) && (
        <div className="notice content-stack">
          <p role="status">
            A newer revision is available. Your unsaved edits are preserved.
            Copy what you need before loading the latest.
          </p>
          <Button
            size="sm"
            variant="outline"
            type="button"
            className="quiet"
            disabled={state.busy}
            onClick={state.reload}
          >
            Load latest
          </Button>
        </div>
      )}
    </>
  );
}
