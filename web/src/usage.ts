import { useEffect, useMemo, useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";

export type UsageView = components["schemas"]["UsageView"];
export type UsageExecution = components["schemas"]["UsageExecution"];
export type Tokens = components["schemas"]["Tokens"];
export const usageNumber = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString();
export function usageDuration(seconds: number | null) {
  if (seconds == null) return "—";
  const whole = Math.max(0, Math.floor(seconds));
  if (whole < 60) return `${whole}s`;
  const minutes = Math.floor(whole / 60);
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}h ${minutes % 60}m`
    : `${minutes}m ${whole % 60}s`;
}

// Totals are service-owned, independent of pagination. Refresh a visible live
// surface even when its harness has not emitted another workspace event.
export function useUsagePage(path: string, refresh: unknown) {
  const [poll, setPoll] = useState(0);
  const tick = useMemo(() => ({ refresh, poll }), [refresh, poll]);
  const resource = useResource<UsageView>(path, tick);
  const live = !!resource.data?.breakdowns.some(
    (row) => row.dimension === "role" && row.live,
  );
  useEffect(() => {
    if (!live) return;
    const refreshVisible = () => {
      if (document.visibilityState === "visible") setPoll((n) => n + 1);
    };
    const timer = window.setInterval(refreshVisible, 5000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [live, path]);
  return resource;
}

export function useUsageClock(view: UsageView) {
  const live = view.breakdowns.some(
    (row) => row.dimension === "role" && row.live,
  );
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") setNow(Date.now());
    }, 1000);
    return () => clearInterval(timer);
  }, [live]);
  // Stop interpolation after a missed refresh, rather than inventing indefinite
  // running time during an outage. The next authoritative read reconciles it.
  const delta = Math.max(
    0,
    Math.min(10, (now - Date.parse(view.measured_at)) / 1000),
  );
  return (seconds: number | null, running = live) =>
    usageDuration(seconds == null ? null : seconds + (running ? delta : 0));
}
