import { toast } from "sonner";

export class RequestError extends Error {
  constructor(
    message: string,
    readonly code?: string,
    readonly path = "",
    readonly method = "GET",
  ) {
    super(message);
  }
}

// Deduplicate failed background reads until that resource recovers. An outage is
// shared across views; deliberate user actions always get their own feedback.
const failures = new Map<string, { message: string; at: number }>();
export function requestRecovered(path: string, startedAt: number) {
  // A delayed successful response from before an outage does not prove recovery.
  for (const key of [path, "connection"])
    if ((failures.get(key)?.at ?? Infinity) <= startedAt) failures.delete(key);
}
export function reportError(error: unknown, title: string, background = false) {
  if (error instanceof DOMException && error.name === "AbortError") return;
  const failure = error instanceof RequestError ? error : null;
  const connection = failure?.code === "service_unavailable";
  const key = connection ? "connection" : failure?.path || title;
  const message = error instanceof Error ? error.message : "Please try again.";
  if (
    background &&
    failures.has(key) &&
    (connection || failures.get(key)?.message === message)
  )
    return;
  failures.delete(key);
  failures.set(key, { message, at: performance.now() });
  if (failures.size > 128) failures.delete(failures.keys().next().value!);
  toast.error(connection && background ? "Flowfield is unavailable" : title, {
    id: `request:${key}`,
    description: message,
    duration: 8000,
  });
}
