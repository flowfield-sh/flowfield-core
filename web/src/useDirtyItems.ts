import { useCallback, useEffect, useState } from "react";
import type { DirtyCallback } from "./retainedState";

/** Independently bound drafts never clear another request's navigation guard. */
export function useDirtyItems(onDirty: DirtyCallback) {
  const [items, setItems] = useState<
    Record<string, { dirty: boolean; retained: boolean }>
  >({});
  const dirty = Object.values(items).some((item) => item.dirty);
  const retained = !Object.values(items).some(
    (item) => item.dirty && !item.retained,
  );
  useEffect(() => {
    onDirty(dirty, retained);
    return () => onDirty(false);
  }, [dirty, retained, onDirty]);
  return useCallback((id: string, value: boolean, retained = false) => {
    setItems((previous) =>
      previous[id]?.dirty === value && previous[id]?.retained === retained
        ? previous
        : { ...previous, [id]: { dirty: value, retained } },
    );
  }, []);
}
