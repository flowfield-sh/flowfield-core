import { reportError } from "./requestFeedback";
import { useCallback, useEffect, useRef, useState } from "react";
import { RequestError, request } from "./workspace";

const loadedWindows = new Map<string, number>();

// Every read owns an abort signal. Navigation, refresh and a successful local
// mutation invalidate stale replies; writes are never silently cancelled.
export function useResource<T>(
  path: string | null,
  refresh: unknown,
  timeoutMs = 10000,
  activity?: {
    projectId?: string;
    workspaceId?: string;
    attemptId?: string;
    isActive?: (data: T) => boolean;
  },
  readData?: (path: string, signal: AbortSignal) => Promise<T>,
) {
  const [retryVersion, setRetryVersion] = useState(0);
  const retry = useCallback(() => setRetryVersion((value) => value + 1), []);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [errorCode, setErrorCode] = useState<string | undefined>();
  const [loading, setLoading] = useState(!!path);
  const [key, setKey] = useState({ path, refresh, retryVersion });
  if (
    key.path !== path ||
    key.refresh !== refresh ||
    key.retryVersion !== retryVersion
  ) {
    setKey({ path, refresh, retryVersion });
    setLoading(!!path);
    setError("");
    if (key.path !== path) setData(null);
  }
  const controller = useRef<AbortController | null>(null);
  const invalidate = useCallback(() => {
    controller.current?.abort();
    setLoading(false);
  }, []);
  const projectId = activity?.projectId;
  const workspaceId = activity?.workspaceId;
  const attemptId = activity?.attemptId;
  const isActive = activity?.isActive;
  useEffect(() => {
    const pending = new AbortController();
    controller.current = pending;
    if (!path) return () => pending.abort();
    let reading = false,
      again = false;
    async function read() {
      if (reading) {
        again = true;
        return;
      }
      reading = true;
      do {
        again = false;
        try {
          const result = await (readData
            ? readData(path!, pending.signal)
            : request<T>(path!, "GET", undefined, pending.signal, timeoutMs));
          if (!pending.signal.aborted) {
            setData(result);
            setError("");
            if (isActive && !isActive(result))
              window.removeEventListener("flowfield:activity", updated);
          }
        } catch (error) {
          if (!pending.signal.aborted) {
            setError((error as Error).message);
            setErrorCode(
              error instanceof RequestError ? error.code : undefined,
            );
            reportError(error, "Could not load data", true);
          }
        } finally {
          if (!pending.signal.aborted) setLoading(false);
        }
      } while (again && !pending.signal.aborted);
      reading = false;
    }
    function updated(event: Event) {
      const update = (
        event as CustomEvent<{
          projects?: string[] | null;
          activity?: [string, string][];
          coordinator_activity?: [string, string][];
        }>
      ).detail;
      if (
        update.projects === null ||
        update.projects?.includes(projectId!) ||
        update.coordinator_activity?.some(
          ([workspace, turn]) =>
            workspace === workspaceId && turn === attemptId,
        ) ||
        update.activity?.some(
          ([project, attempt]) =>
            project === projectId && attempt === attemptId,
        )
      )
        void read();
    }
    if (attemptId) window.addEventListener("flowfield:activity", updated);
    void read();
    return () => {
      pending.abort();
      window.removeEventListener("flowfield:activity", updated);
    };
  }, [
    path,
    refresh,
    retryVersion,
    timeoutMs,
    projectId,
    workspaceId,
    attemptId,
    isActive,
    readData,
  ]);
  return {
    data,
    setData,
    error,
    errorCode: error ? errorCode : undefined,
    setError,
    loading,
    invalidate,
    retry,
  };
}

export function usePage<
  P extends {
    items: unknown[];
    next_cursor?: number | string | null;
    next_before?: number | null;
    next_offset?: number | null;
  },
>(path: string, refresh: unknown, refreshLoaded = false) {
  const loaded = useRef({
    path,
    count: refreshLoaded ? (loadedWindows.get(path) ?? 0) : 0,
  });
  const readLoaded = useCallback(async (url: string, signal: AbortSignal) => {
    let page = await request<P>(url, "GET", undefined, signal);
    const count = loaded.current.path === url ? loaded.current.count : 0;
    // Publish the refreshed window together, so later-page editors never
    // unmount briefly while the first page is being revalidated.
    while (page.items.length < count) {
      const cursor = page.next_cursor ?? page.next_before ?? page.next_offset;
      if (cursor == null) break;
      const next = await request<P>(
        `${url}${url.includes("?") ? "&" : "?"}${page.next_offset != null ? "offset" : "before"}=${encodeURIComponent(cursor)}`,
        "GET",
        undefined,
        signal,
      );
      page = { ...next, items: [...page.items, ...next.items] };
    }
    return page;
  }, []);
  const resource = useResource<P>(
    path,
    refresh,
    10000,
    undefined,
    refreshLoaded ? readLoaded : undefined,
  );
  const { data, setData, setError } = resource;
  const [loadingMore, setLoadingMore] = useState(false);
  const [key, setKey] = useState({ path, refresh });
  if (key.path !== path || key.refresh !== refresh) {
    setKey({ path, refresh });
    setLoadingMore(false);
  }
  const more = useRef<AbortController | null>(null);
  useEffect(() => {
    more.current?.abort();
    more.current = null;
    return () => more.current?.abort();
  }, [path, refresh]);
  async function older() {
    const cursor = data?.next_cursor ?? data?.next_before ?? data?.next_offset;
    if (!cursor || more.current || resource.loading) return;
    const pending = new AbortController();
    more.current = pending;
    setLoadingMore(true);
    // A live refresh can cancel this read. Keep the requested window so that
    // refresh completes the user's pagination instead of discarding the click.
    if (refreshLoaded) {
      loaded.current = { path, count: (data?.items.length ?? 0) + 1 };
    }
    try {
      const page = await request<P>(
        `${path}${path.includes("?") ? "&" : "?"}${data?.next_offset != null ? "offset" : "before"}=${encodeURIComponent(cursor)}`,
        "GET",
        undefined,
        pending.signal,
      );
      if (!pending.signal.aborted) {
        setData((previous) => ({
          ...page,
          items: [...(previous?.items ?? []), ...page.items],
        }));
        loaded.current = {
          path,
          count: (data?.items.length ?? 0) + page.items.length,
        };
        if (refreshLoaded) {
          loadedWindows.delete(path);
          loadedWindows.set(path, loaded.current.count);
          while (loadedWindows.size > 100)
            loadedWindows.delete(loadedWindows.keys().next().value!);
        }
        setError("");
      }
    } catch (error) {
      if (!pending.signal.aborted) {
        setError((error as Error).message);
        reportError(error, "Could not load more items");
      }
    } finally {
      if (!pending.signal.aborted) {
        more.current = null;
        setLoadingMore(false);
      }
    }
  }
  return {
    ...resource,
    loadingMore: loadingMore || resource.loading,
    older,
    retry() {
      more.current?.abort();
      more.current = null;
      setLoadingMore(false);
      resource.retry();
    },
  };
}
