import { reportError } from "./requestFeedback";
import { useCallback, useEffect, useRef, useState } from "react";
import { RequestError, request } from "./workspace";
import { nextPage, pageCursor, pageWindow, type Page } from "./pagination";

const loadedWindows = new Map<string, number>();
function rememberWindow(path: string, count: number) {
  loadedWindows.delete(path);
  loadedWindows.set(path, count);
  while (loadedWindows.size > 100)
    loadedWindows.delete(loadedWindows.keys().next().value!);
}

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
  useEffect(() => {
    window.addEventListener("flowfield:service-recovered", retry);
    return () =>
      window.removeEventListener("flowfield:service-recovered", retry);
  }, [retry]);
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
          workspaces?: string[] | null;
          activity?: [string, string][];
          coordinator_activity?: [string, string][];
        }>
      ).detail;
      if (
        update.workspaces === null ||
        update.workspaces?.includes(workspaceId ?? projectId!) ||
        update.coordinator_activity?.some(
          ([workspace, turn]) =>
            workspace === workspaceId && turn === attemptId,
        ) ||
        update.activity?.some(
          ([project, attempt]) =>
            project === (workspaceId ?? projectId) && attempt === attemptId,
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

function readPage<P>(url: string, signal: AbortSignal) {
  return request<P>(url, "GET", undefined, signal);
}

export function usePage<P extends Page>(
  path: string,
  refresh: unknown,
  refreshLoaded = false,
  read: (url: string, signal: AbortSignal) => Promise<P> = readPage,
) {
  const loaded = useRef({
    path,
    count: refreshLoaded ? (loadedWindows.get(path) ?? 0) : 0,
  });
  const more = useRef<AbortController | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const readLoaded = useCallback(
    async (url: string, signal: AbortSignal) => {
      // Every base read supersedes pagination, including service recovery and
      // hook-local retry when the caller's refresh value has not changed.
      more.current?.abort();
      more.current = null;
      setLoadingMore(false);
      if (loaded.current.path !== url)
        loaded.current = {
          path: url,
          count: refreshLoaded ? (loadedWindows.get(url) ?? 0) : 0,
        };
      // Publish the refreshed window together so later-page editors stay mounted.
      return pageWindow<P>(
        url,
        refreshLoaded ? loaded.current.count : 0,
        (next) => read(next, signal),
      );
    },
    [refreshLoaded, read],
  );
  const resource = useResource<P>(path, refresh, 10000, undefined, readLoaded);
  const { data, setData, setError } = resource;
  const [key, setKey] = useState({ path, refresh });
  if (key.path !== path || key.refresh !== refresh) {
    setKey({ path, refresh });
    setLoadingMore(false);
  }
  useEffect(() => {
    more.current?.abort();
    more.current = null;
    return () => more.current?.abort();
  }, [path, refresh]);
  async function older() {
    if (!data || pageCursor(data) == null || more.current || resource.loading)
      return;
    const pending = new AbortController();
    more.current = pending;
    setLoadingMore(true);
    // A live refresh can cancel this read. Keep the requested window so that
    // refresh completes the user's pagination instead of discarding the click.
    if (refreshLoaded) {
      loaded.current = { path, count: (data?.items.length ?? 0) + 1 };
      // A refresh may finish this window after cancelling older(). Retain the
      // requested depth now, so leaving and reopening does not lose that page.
      rememberWindow(path, loaded.current.count);
    }
    try {
      const page = await nextPage(path, data, (url) =>
        read(url, pending.signal),
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
          rememberWindow(path, loaded.current.count);
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
