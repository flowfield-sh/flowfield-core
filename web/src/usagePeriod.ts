/** One owner for local calendar presets, exact URL ranges and datetime inputs. */
const pad = (n: number) => String(n).padStart(2, "0");
export function localDateTime(value: string | number) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function parseLocalDateTime(value: string, previous?: string) {
  // Preserve the exact offset of a selected repeated DST hour when left unchanged.
  if (previous && localDateTime(previous) === value) return previous;
  const date = new Date(value);
  if (
    !value ||
    !Number.isFinite(date.getTime()) ||
    localDateTime(date.getTime()) !== value
  )
    throw new Error("Choose a valid local date and time.");
  return date.toISOString();
}

export const periods = [
  ["today", "Today"],
  ["week", "Last 7 days"],
  ["month", "Last 30 days"],
  ["quarter", "Last 3 months"],
  ["year", "Year to date"],
  ["custom", "Custom"],
] as const;
export type Period = (typeof periods)[number][0];
export function rangeGroup(start: string, end: string) {
  const seconds = (Date.parse(end) - Date.parse(start)) / 1000;
  return seconds <= 7200 ? "minute" : seconds <= 2 * 86400 ? "hour" : "day";
}
export function presetRange(period: Period, now: number) {
  const end = new Date(now),
    start = new Date(now);
  if (period === "quarter") {
    // Calendar months, clamped to the target month's last day (May 31 -> Feb 28).
    const day = start.getDate();
    start.setDate(1);
    start.setMonth(start.getMonth() - 3);
    const last = new Date(
      start.getFullYear(),
      start.getMonth() + 1,
      0,
    ).getDate();
    start.setDate(Math.min(day, last));
  } else {
    start.setHours(0, 0, 0, 0);
    if (period === "year") start.setMonth(0, 1);
    if (period === "week") start.setDate(start.getDate() - 6);
    if (period === "month") start.setDate(start.getDate() - 29);
  }
  // Midnight links still have a positive, inspectable interval.
  if (start.getTime() === end.getTime()) end.setMinutes(end.getMinutes() + 1);
  return { start: start.toISOString(), end: end.toISOString() };
}
export function metricsQuery(search: string, now: number) {
  const query = new URLSearchParams(search);
  const raw = query.get("period");
  const preset: Period = periods.some(([key]) => key === raw)
    ? (raw as Period)
    : "today";
  const hasRange = query.has("start") || query.has("end");
  query.set("period", hasRange && (!raw || raw === "zoom") ? "custom" : preset);
  if (!hasRange) {
    const range = presetRange(preset, now);
    query.set("start", range.start);
    query.set("end", range.end);
  }
  // Inputs are always the viewer's local datetime, regardless of a shared link's zone.
  query.set(
    "timezone",
    Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  query.set(
    "group",
    rangeGroup(query.get("start") ?? "", query.get("end") ?? ""),
  );
  if (!["role", "harness", "model"].includes(query.get("dimension") ?? ""))
    query.set("dimension", "role");
  query.delete("metric");
  query.delete("zoom");
  return query;
}
export function bucketLabel(
  value: string,
  timezone: string,
  group: string,
  compact = false,
) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone,
    ...(!compact || group === "day"
      ? { month: "short" as const, day: "numeric" as const }
      : {}),
    ...(group !== "day"
      ? { hour: "2-digit" as const, minute: "2-digit" as const }
      : {}),
    ...(!compact ? { year: "numeric" as const } : {}),
  }).format(new Date(value));
}
