import {
  checkServiceConnection,
  serviceIsUnavailable,
} from "./serviceConnection";
import { toast } from "sonner";

export class RequestError extends Error {
  constructor(
    message: string,
    readonly code?: string,
    readonly path = "",
    readonly method = "GET",
    readonly status?: number,
  ) {
    super(message);
  }
}

// Deduplicate failed background reads until that resource recovers. Service-wide
// outages belong to the health owner; actions get their own contextual feedback.
const failures = new Map<string, { message: string; at: number }>();
export function requestRecovered(path: string, startedAt: number) {
  // A delayed successful response from before an outage does not prove recovery.
  if ((failures.get(path)?.at ?? Infinity) <= startedAt) failures.delete(path);
}
export function reportError(error: unknown, title: string, background = false) {
  if (
    error instanceof RequestError &&
    (["service_unavailable", "request_timeout", "invalid_response"].includes(
      error.code ?? "",
    ) ||
      (error.status ?? 0) >= 500)
  ) {
    return checkServiceConnection().then((available) => {
      if (available) showError(error, title, background);
    });
  }
  showError(error, title, background);
}
function showError(error: unknown, title: string, background: boolean) {
  if (serviceIsUnavailable()) return;
  if (error instanceof DOMException && error.name === "AbortError") return;
  const failure = error instanceof RequestError ? error : null;
  const key = failure?.path || title;
  const message = error instanceof Error ? error.message : "Please try again.";
  if (background && failures.get(key)?.message === message) return;
  failures.delete(key);
  failures.set(key, { message, at: performance.now() });
  if (failures.size > 128) failures.delete(failures.keys().next().value!);
  toast.error(title, {
    id: `request:${key}`,
    description: message,
    duration: 8000,
  });
}
