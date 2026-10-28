import type { components } from "./api-schema";
import { request } from "./workspace";
import { usePage } from "./useResource";

export type UsageView = components["schemas"]["UsageView"];
export type UsageExecution = components["schemas"]["UsageExecution"];
export type UsageEvidencePage = components["schemas"]["UsageEvidencePage"];
export type Tokens = components["schemas"]["Tokens"];

export const usageNumber = (value: number | null | undefined) =>
  value == null ? "Not reported" : value.toLocaleString();
export const usageLabel = (value: string) => value.replaceAll(/[-_]/g, " ");
export const usageCoverage = (value: UsageExecution["coverage"]) =>
  value === "complete"
    ? "Final for reported scope"
    : value === "partial"
      ? "Partial coverage"
      : "Usage unavailable";
export function usageDuration(seconds: number | null) {
  if (seconds == null) return "Time unavailable";
  if (seconds < 60) return `${Math.floor(seconds)}s recorded execution time`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes >= 60 ? `${Math.floor(minutes / 60)}h ` : ""}${minutes % 60}m recorded execution time`;
}
export function usageEvidencePath(
  workspaceId: string,
  execution: Pick<UsageExecution, "source" | "source_id">,
) {
  return `workspaces/${encodeURIComponent(workspaceId)}/usage/${execution.source}/${encodeURIComponent(execution.source_id)}`;
}

// Adapt the accounting API's forward cursor to the shared retained-window owner.
// All totals remain server-owned; loaded execution pages are never re-summed.
type UsagePage = UsageView | UsageEvidencePage;
async function readUsagePage<P extends UsagePage>(
  path: string,
  signal: AbortSignal,
): Promise<P & { next_cursor: number | null }> {
  const [base, query] = path.split("?");
  const params = new URLSearchParams(query);
  if (params.has("before")) {
    params.set("after", params.get("before")!);
    params.delete("before");
  }
  const page = await request<P>(`${base}?${params}`, "GET", undefined, signal);
  return { ...page, next_cursor: page.next_after };
}
export function useUsagePage<P extends UsagePage>(
  path: string,
  refresh: unknown,
) {
  return usePage<P & { next_cursor: number | null }>(
    path,
    refresh,
    true,
    readUsagePage<P>,
  );
}
