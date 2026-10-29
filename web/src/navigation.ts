import { useCallback, useEffect, useRef, type MouseEvent } from "react";
import { useBlocker, useLocation, useMatches, useNavigate } from "react-router";

const pagePaths = [
  "settings/harnesses",
  "settings/appearance",
  "settings/notifications",
  "settings/updates",
  "new-workspace",
  "workspaces/:workspaceId",
  "workspaces/:workspaceId/tasks",
  "workspaces/:workspaceId/tasks/:taskKey",
  "workspaces/:workspaceId/tasks/:taskKey/settings",
  "workspaces/:workspaceId/tasks/:taskKey/history/:runId?",
  "workspaces/:workspaceId/documents",
  "workspaces/:workspaceId/files",
  "workspaces/:workspaceId/metrics",
  "workspaces/:workspaceId/documents/:documentId",
  "workspaces/:workspaceId/documents/:documentId/edit",
  "workspaces/:workspaceId/documents/:documentId/revisions/:documentRevision",
  "workspaces/:workspaceId/projects",
  "workspaces/:workspaceId/projects/new",
  "workspaces/:workspaceId/archive",
  "workspaces/:workspaceId/inbox",
  "workspaces/:workspaceId/requests/:requestKind/:requestId",
  "workspaces/:workspaceId/results/:resultId",
  "workspaces/:workspaceId/edit/:workspaceTab?",
  "projects/:projectId",
  "projects/:projectId/overview",
  "projects/:projectId/edit/:projectTab?",
];
const nestedProjectPaths = pagePaths
  .filter((path) => path.startsWith("projects/"))
  .map((path) => `workspaces/:workspaceId/${path}`);
export const workspacePaths = [...pagePaths, ...nestedProjectPaths];
export type TaskTab = "task" | "history";

export function canonicalHref(path: string, _owners: Record<string, string>) {
  if (!path.startsWith("/") || path.startsWith("//")) return path;
  const url = new URL(path, window.location.origin);
  const nested = url.pathname.match(
    /^\/workspaces\/([^/]+)(\/projects\/([^/]+)(?:\/.*)?)$/,
  );
  if (nested && nested[3] !== "new") url.pathname = nested[2];
  const project = /^\/projects\/([^/]+)\/?$/.exec(url.pathname);
  if (project && _owners[decodeURIComponent(project[1])])
    url.pathname += "/overview";
  if (/^\/workspaces\/[^/]+\/?$/.test(url.pathname)) {
    const filtered = ["project", "label"].some((key) =>
      url.searchParams.has(key),
    );
    url.pathname =
      url.pathname.replace(/\/$/, "") + (filtered ? "/tasks" : "/inbox");
  }
  return url.pathname + url.search + url.hash;
}
export function boardHref(
  workspaceId: string,
  filters: { project?: string; label?: string } = {},
  archive = false,
) {
  const query = new URLSearchParams(
    Object.entries(filters).filter(([, value]) => !!value) as [
      string,
      string,
    ][],
  );
  return (
    workspaceHref(workspaceId) +
    (archive ? "/archive" : "/tasks") +
    (query.size ? "?" + query : "")
  );
}
export function projectHref(id: string) {
  return `/projects/${encodeURIComponent(id)}`;
}
export function workspaceHref(id: string) {
  return `/workspaces/${encodeURIComponent(id)}`;
}
export function resultHref(workspaceId: string, resultId: string) {
  return `${workspaceHref(workspaceId)}/results/${encodeURIComponent(resultId)}`;
}
export function taskHref(
  projectId: string,
  task: { key: string },
  tab: TaskTab = "task",
) {
  const owner = workspaceHref(projectId);
  return `${owner}/tasks/${encodeURIComponent(task.key)}${tab === "task" ? "" : `/${tab}`}`;
}
function editorIdentity(path: string) {
  return path
    .replace(
      /^\/settings\/(harnesses|appearance|notifications|updates)\/?$/,
      "/settings",
    )
    .replace(
      /^(\/(?:(?:workspaces\/[^/]+\/)?projects\/[^/]+|workspaces\/[^/]+)\/edit)\/(general|info|coordinator|workers|integration)\/?$/,
      "$1",
    )
    .replace(
      /\/(task|changes(?:\/[^/]+)?|history(?:\/[^/]+)?(?:\/integrations\/[^/]+)?|tasks)\/?$/,
      "",
    );
}
export function useWorkspaceNavigation(
  unsaved: boolean,
  setUnsaved: (value: boolean) => void,
  chatSettingsDirty = false,
  projectOwners: Record<string, string> = {},
) {
  const location = useLocation();
  const navigate = useNavigate();
  const matches = useMatches();
  const params = matches.at(-1)?.params ?? {};
  const bypass = useRef(false);
  function workspaceOwner(path: string) {
    const [, kind, identity] = path.split("/");
    return kind === "workspaces"
      ? identity
      : kind === "projects"
        ? (projectOwners[identity] ?? `project:${identity}`)
        : null;
  }
  function responseIdentity(location: { pathname: string; search: string }) {
    const path = canonicalHref(location.pathname, projectOwners);
    const params = new URLSearchParams(location.search);
    const pane = /\/inbox$/.test(path)
      ? [params.get("inbox") ?? "needs_me", params.get("project") ?? ""]
      : [];
    return JSON.stringify([editorIdentity(path), ...pane]);
  }
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      !bypass.current &&
      ((chatSettingsDirty &&
        workspaceOwner(currentLocation.pathname) !==
          workspaceOwner(nextLocation.pathname)) ||
        (unsaved &&
          responseIdentity(currentLocation) !==
            responseIdentity(nextLocation))),
  );
  useEffect(() => {
    const target = canonicalHref(
      location.pathname + location.search + location.hash,
      projectOwners,
    );
    if (target !== location.pathname + location.search + location.hash)
      void navigate(target, {
        replace: true,
        state: location.state,
      });
  }, [location, navigate, projectOwners]);
  const changeLocation = useCallback(
    (url: string, replace = false, extraState?: Record<string, unknown>) => {
      url = canonicalHref(url, projectOwners);
      if (
        !replace &&
        !extraState &&
        url === location.pathname + location.search + location.hash
      )
        return;
      bypass.current = replace;
      void navigate(url, {
        flushSync: !replace,
        replace,
        state: {
          ...extraState,
        },
      });
      bypass.current = false;
    },
    [navigate, location, projectOwners],
  );
  return {
    discardChanges: {
      open: blocker.state === "blocked",
      cancel: () => {
        if (blocker.state === "blocked") blocker.reset();
      },
      discard: () => {
        if (blocker.state !== "blocked") return;
        if (responseIdentity(location) !== responseIdentity(blocker.location))
          setUnsaved(false);
        blocker.proceed();
      },
    },
    params,
    pathname: location.pathname,
    changeLocation,
    notFound:
      matches.at(-1)?.id === "not-found" ||
      (!!params.requestKind &&
        !["question", "attempt", "integration", "permission"].includes(
          params.requestKind,
        )),
  };
}
export function followLink(
  event: MouseEvent<HTMLAnchorElement>,
  action: () => void,
) {
  if (
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  )
    return;
  event.preventDefault();
  action();
}
