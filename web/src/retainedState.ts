import { useCallback, useState, useSyncExternalStore } from "react";
import type { Dispatch, SetStateAction } from "react";

// Session memory only. Immutable record bindings travel with the values; no
// response text, attachments or document bodies enter browser storage.
const entries = new Map<string, unknown>();
const listeners = new Set<() => void>();
const dirtyEntries = new Set<string>();
const readers = new Map<string, number>();
const scope = (key: string) =>
  key.replace(
    /:(loaded|values|editing|draft|drafts|chosen|correcting|note|revision|items)$/,
    "",
  );
function trim() {
  if (entries.size <= 512) return;
  const protectedScopes = new Set(
    [...dirtyEntries, ...readers.keys()].map(scope),
  );
  for (const key of entries.keys()) {
    if (entries.size <= 512) break;
    // Retained Inbox rows are the navigation route back to unsent responses.
    if (
      protectedScopes.has(scope(key)) ||
      (key.startsWith("inbox:") && dirtyEntries.size)
    )
      continue;
    entries.delete(key);
  }
}
function subscribe(key: string | undefined, listener: () => void) {
  if (key) readers.set(key, (readers.get(key) ?? 0) + 1);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (key) {
      const count = (readers.get(key) ?? 1) - 1;
      if (count) readers.set(key, count);
      else readers.delete(key);
      queueMicrotask(trim);
    }
  };
}
export function useRetainedState<T>(
  key: string | undefined,
  initial: T | (() => T),
): [T, Dispatch<SetStateAction<T>>] {
  const [local, setLocal] = useState(initial);
  if (key && !entries.has(key))
    entries.set(
      key,
      typeof initial === "function" ? (initial as () => T)() : initial,
    );
  const watch = useCallback(
    (listener: () => void) => subscribe(key, listener),
    [key],
  );
  const value = useSyncExternalStore(watch, () =>
    key ? (entries.get(key) as T) : local,
  );
  const set = useCallback<Dispatch<SetStateAction<T>>>(
    (next) => {
      if (!key) {
        setLocal(next);
        return;
      }
      const previous = entries.get(key) as T;
      const value =
        typeof next === "function" ? (next as (old: T) => T)(previous) : next;
      if (Object.is(value, previous)) return;
      entries.delete(key);
      entries.set(key, value);
      queueMicrotask(() => {
        for (const listener of listeners) listener();
      });
    },
    [key],
  );
  return [value, set];
}
export function retainDirty(key: string, dirty: boolean) {
  if (dirty) dirtyEntries.add(key);
  else dirtyEntries.delete(key);
}
export function hasRetainedDrafts() {
  return dirtyEntries.size > 0;
}
export type DirtyCallback = (dirty: boolean, retained?: boolean) => void;
