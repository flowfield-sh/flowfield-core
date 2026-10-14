import { useCallback, useEffect, useState } from "react";

/** Aggregate independently bound response drafts without one clearing another. */
export function useDirtyItems(onDirty: (dirty: boolean) => void) {
  const [items, setItems] = useState<Record<string, boolean>>({});
  const dirty = Object.values(items).some(Boolean);
  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  return useCallback((id: string, value: boolean) => {
    setItems((previous) =>
      previous[id] === value ? previous : { ...previous, [id]: value },
    );
  }, []);
}
