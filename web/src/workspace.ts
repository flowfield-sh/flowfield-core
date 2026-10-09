import type { components } from "./api-schema";
export const columns = [
  "backlog",
  "up_next",
  "in_progress",
  "in_review",
  "done",
] as const;
export const taskTypes = [
  "feature",
  "bug",
  "maintenance",
  "investigation",
] as const;
export type WorkStatus = (typeof columns)[number];
export const label = (value: string) =>
  value.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());

export const authorLabel = (author: string | null) =>
  author?.startsWith("coordinator:") ? "Coordinator" : author;
export type Project = components["schemas"]["Project"];
export type Milestone = components["schemas"]["Milestone"];
export type TaskRevision = components["schemas"]["TaskRevision"];
export type TaskReference = components["schemas"]["TaskReference"];
export type Task = components["schemas"]["TaskDetail"];
export type TaskCard = components["schemas"]["TaskCard"];
export type Board = components["schemas"]["BrowserBoard"];
export type RecordMeta = Pick<
  Project,
  "id" | "revision" | "updated_at" | "updated_by"
>;
export class RequestError extends Error {
  constructor(
    message: string,
    readonly code?: string,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
  timeoutMs = 10000,
): Promise<T> {
  const response = await fetch(`/api/${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal
      ? AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)])
      : AbortSignal.timeout(timeoutMs),
  });
  if (response.status === 204) return undefined as T;
  const data = await response.json();
  if (!response.ok)
    throw new RequestError(
      data.error?.message ??
        (response.status === 422
          ? "Check your fields: a title is required, and text must fit the field limits."
          : "Request failed. Please try again."),
      data.error?.code,
    );
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
  return order || (a.id === b.id ? 0 : a.id < b.id ? -1 : 1);
}

export type ActivityEntry = components["schemas"]["ActivityEntry"];
export type ActivityPage = components["schemas"]["ActivityPage"];
