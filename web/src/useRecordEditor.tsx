import {
  useRetainedState,
  retainDirty,
  type DirtyCallback,
} from "./retainedState";
import { useDiscardChanges } from "./DiscardChangesDialog";
import { reportError } from "./requestFeedback";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router";
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
  read?: (signal: AbortSignal) => Promise<R>;
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
  const location = useLocation();
  const readController = useRef<AbortController | null>(null);
  const resourcePath = path(incoming);
  useEffect(
    () => () => readController.current?.abort(),
    [resourcePath, retainedKey, location.key],
  );
  const dirty = JSON.stringify(values) !== JSON.stringify(fields(loaded));
  const discard = useDiscardChanges(
    JSON.stringify([
      retainedKey,
      path(loaded),
      loaded?.revision,
      incoming && version ? version(incoming) : incoming?.revision,
      values,
    ]),
    busy,
  );
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
  // Retained values notify all mounted readers synchronously. Adopt new records
  // after render so that another reader is never updated during this render.
  const shouldAdopt = !!(incoming && (!loaded || newer) && !dirty && !busy);
  if (shouldAdopt && conflict) setConflict(false);
  useLayoutEffect(() => {
    if (incoming && shouldAdopt) {
      setLoaded(incoming);
      setValues(fields(incoming));
    }
  }, [incoming, shouldAdopt, fields, setLoaded, setValues]);
  function change<K extends keyof V>(key: K, value: V[K]) {
    const next = { ...values, [key]: value };
    setValues(next);
    onDirty(
      JSON.stringify(next) !== JSON.stringify(fields(loaded)) || otherDirty,
      !!retainedKey && !otherDirty,
    );
  }
  async function run(method: string, payload?: unknown, suffix = "") {
    if (busy) return;
    const pending = method === "GET" ? new AbortController() : null;
    if (pending) {
      readController.current?.abort();
      readController.current = pending;
    }
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
          ? await read(pending!.signal)
          : await request<R>(
              path(loaded) + suffix,
              method,
              payload,
              pending?.signal,
            );
      if (mounted.current && !pending?.signal.aborted) {
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
      if (mounted.current && !pending?.signal.aborted) {
        reportError(error, `Could not ${action} ${recordName}`);
        if (error instanceof RequestError && error.code === "revision_conflict")
          setConflict(true);
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  function cancel(after?: () => void) {
    if (busy) return;
    const apply = () => {
      setValues(fields(loaded));
      setEditing(false);
      if (retainedKey) retainDirty(retainedKey, false);
      onDirty(otherDirty);
      after?.();
    };
    if (dirty) discard.request(apply);
    else apply();
  }
  function reload() {
    if (busy) return;
    if (dirty) discard.request(() => void run("GET"), true);
    else void run("GET");
  }
  return {
    confirmation: discard.dialog,
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
    confirmation: ReactNode;
  };
}) {
  return (
    <>
      {state.confirmation}
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
