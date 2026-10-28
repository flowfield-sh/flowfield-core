import type { UsageView } from "./usage";
/** UTC boundaries for a local calendar day, including short/long DST days. */
export function localDate(instant: number, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(instant);
  return ["year", "month", "day"]
    .map((key) => parts.find((p) => p.type === key)!.value)
    .join("-");
}
export function todayPeriod(timezone: string, now = Date.now()) {
  const date = localDate(now, timezone);
  // Find the first instant of this local date without assuming midnight exists
  // or a day is 24 hours. Search UTC instants; repeated hours remain distinct.
  function boundary(after: boolean) {
    let left = now - 36 * 3600000,
      right = now + 36 * 3600000;
    while (right - left > 1) {
      const mid = Math.floor((left + right) / 2);
      const value = localDate(mid, timezone);
      if (after ? value > date : value >= date) right = mid;
      else left = mid;
    }
    return new Date(right).toISOString();
  }
  return { start: boundary(false), end: boundary(true) };
}
export function defaultUsageQuery() {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  return new URLSearchParams({
    ...todayPeriod(timezone),
    timezone,
    group: "hour",
    period: "today",
  });
}
export function bucketLabel(
  value: string,
  timezone: string,
  group: string,
  compact = false,
) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone,
    ...(!compact || group !== "hour"
      ? { month: "short" as const, day: "numeric" as const }
      : {}),
    ...(group === "hour"
      ? { hour: "2-digit" as const, minute: "2-digit" as const }
      : {}),
    ...(!compact
      ? { year: "numeric" as const, timeZoneName: "shortOffset" as const }
      : {}),
  }).format(new Date(value));
}

/** Native date/time input in UTC; its visible label states the zone. */
export function utcInput(value: string | null) {
  const instant = Date.parse(value ?? "");
  return Number.isFinite(instant)
    ? new Date(instant).toISOString().slice(0, 19)
    : "";
}

/** Start exploration at known task activity; the visible period remains editable.
 * Workspace reads are bounded to a year; the task summary remains lifetime.
 */
export function taskUsageQuery(taskId: string, view: UsageView) {
  const params = defaultUsageQuery();
  const end = Date.parse(params.get("end")!);
  const earliest = Math.min(
    Date.now(),
    ...view.items.flatMap((item) =>
      [item.created_at, item.started_at]
        .filter((date): date is string => date != null)
        .map(Date.parse),
    ),
  );
  const start = Math.max(
    end - 366 * 86400000,
    Date.parse(todayPeriod(params.get("timezone")!, earliest).start),
  );
  params.set("start", new Date(start).toISOString());
  params.set("group", end - start > 31 * 86400000 ? "day" : "hour");
  params.set("period", "custom");
  params.set("task_id", taskId);
  return params;
}
