export function requestHref(
  workspace: string,
  kind: string,
  id: string,
  project?: string | null,
) {
  if (kind === "result")
    return `/workspaces/${encodeURIComponent(workspace)}/results/${encodeURIComponent(id)}`;
  const family = ["review", "intervention"].includes(kind) ? "attempt" : kind;
  return `/workspaces/${encodeURIComponent(workspace)}/requests/${family}/${encodeURIComponent(id)}${project ? `?project_id=${encodeURIComponent(project)}` : ""}`;
}
