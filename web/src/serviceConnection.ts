import { useSyncExternalStore } from "react";

// One health probe owns connection recovery. HTTP failures and SSE disconnects
// are signals to check it, not evidence that every endpoint is unavailable.
let unavailable = false;
let probe: Promise<boolean> | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
let controller: AbortController | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());

export function checkServiceConnection(): Promise<boolean> {
  if (probe) return probe;
  clearTimeout(timer);
  const pending = new AbortController();
  controller = pending;
  probe = (async () => {
    let available = false;
    try {
      const response = await fetch("/api/health", {
        cache: "no-store",
        signal: AbortSignal.any([pending.signal, AbortSignal.timeout(2500)]),
      });
      const data = await response.json();
      available = response.ok && data.status === "ok";
    } catch {
      // Retain the UI and keep trying; this probe never repeats an action.
    }
    if (pending.signal.aborted) return !unavailable;
    const recovered = unavailable && available;
    unavailable = !available;
    emit();
    if (recovered)
      window.dispatchEvent(new Event("flowfield:service-recovered"));
    return available;
  })().finally(() => {
    if (controller !== pending) return;
    probe = null;
    controller = null;
    if (unavailable && listeners.size)
      timer = setTimeout(() => void checkServiceConnection(), 2000);
  });
  return probe;
}

export function serviceIsUnavailable() {
  return unavailable;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      clearTimeout(timer);
      controller?.abort();
      controller = null;
      probe = null;
    }
  };
}

export function useServiceUnavailable() {
  return useSyncExternalStore(subscribe, serviceIsUnavailable, () => false);
}
