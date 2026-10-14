import { RequestError, requestRecovered } from "./requestFeedback";
export { RequestError } from "./requestFeedback";
import type { components } from "./api-schema";
import { actorLabels, actorRoles } from "./api-schema";
export const columns = [
  "backlog",
  "up_next",
  "in_progress",
  "in_review",
  "done",
] as const;
export type WorkStatus = (typeof columns)[number];
export const label = (value: string) =>
  value.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());

export const authorLabel = (author: string | null) => {
  if (!author) return author;
  const role = author.startsWith("worker:")
    ? "worker"
    : author.startsWith("coordinator:")
      ? "coordinator"
      : actorRoles[author as keyof typeof actorRoles];
  return actorLabels[role as keyof typeof actorLabels] ?? author;
};
export type WorkspaceRecord = components["schemas"]["WorkspaceRecord"];
export type Project = components["schemas"]["Project"];
export type Milestone = components["schemas"]["Milestone"];
export type TaskRevision = components["schemas"]["TaskRevision"];
export type TaskReference = components["schemas"]["TaskReference"];
export type Task = components["schemas"]["TaskDetail"];
export type TaskCard = components["schemas"]["TaskCard"];
export type WorkspaceBoard = components["schemas"]["BrowserWorkspaceBoard"];
export type Board = components["schemas"]["BrowserBoard"];
export type RecordMeta = Pick<
  Project,
  "id" | "revision" | "updated_at" | "updated_by"
>;
export async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
  timeoutMs = 10000,
): Promise<T> {
  const startedAt = performance.now();
  const timeout = AbortSignal.timeout(timeoutMs);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  const payload = body === undefined ? undefined : JSON.stringify(body);
  const uncertain =
    method !== "GET"
      ? " Reload to check whether the action completed before trying again."
      : " Try again once the service is available.";
  const failed = (message: string, code: string) =>
    new RequestError(message, code, path, method);
  const saved = () => {
    requestRecovered(path, startedAt);
    if (method !== "GET")
      window.dispatchEvent(
        new CustomEvent("flowfield:mutation", { detail: { path } }),
      );
  };
  let response: Response;
  try {
    response = await fetch("/api/" + path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: payload,
      signal: combined,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    if (timeout.aborted)
      throw failed(
        "Flowfield took too long to respond." + uncertain,
        "request_timeout",
      );
    throw failed(
      "Cannot reach Flowfield. Check that the service is running." + uncertain,
      "service_unavailable",
    );
  }
  if (response.status === 204) {
    saved();
    return undefined as T;
  }
  let data;
  try {
    data = await response.json();
  } catch (error) {
    if (signal?.aborted) throw error;
    if (timeout.aborted)
      throw failed(
        "Flowfield took too long to respond." + uncertain,
        "request_timeout",
      );
    throw failed(
      response.ok
        ? "Flowfield returned an unreadable response." + uncertain
        : "Flowfield returned an error (" + response.status + ")." + uncertain,
      "invalid_response",
    );
  }
  if (!response.ok)
    throw new RequestError(
      typeof data?.error?.message === "string"
        ? data.error.message
        : response.status >= 500
          ? "Flowfield returned an error (" + response.status + ")." + uncertain
          : "Flowfield rejected the request (" +
            response.status +
            "). Check your input and try again.",
      data?.error?.code,
      path,
      method,
    );
  saved();
  return data as T;
}

export const isUpcoming = (status: WorkStatus) =>
  status === "backlog" || status === "up_next";
export function compareTasks(a: TaskCard, b: TaskCard) {
  const order = isUpcoming(a.status)
    ? a.position - b.position
    : (a.status_changed_at === b.status_changed_at
        ? 0
        : a.status_changed_at < b.status_changed_at
          ? -1
          : 1) * (a.status === "done" ? -1 : 1);
  return order || (a.key === b.key ? 0 : a.key < b.key ? -1 : 1);
}

export type ActivityEntry = components["schemas"]["ActivityEntry"];
export type ActivityPage = components["schemas"]["ActivityPage"];
