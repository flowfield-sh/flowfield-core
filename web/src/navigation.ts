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
  "workspaces/:workspaceId/tasks/:taskKey/:tab?/:runId?",
  "workspaces/:workspaceId/documents",
  "workspaces/:workspaceId/files",
  "workspaces/:workspaceId/work",
  "workspaces/:workspaceId/documents/:documentId",
  "workspaces/:workspaceId/documents/:documentId/revisions/:documentRevision",
  "workspaces/:workspaceId/projects",
  "workspaces/:workspaceId/projects/new",
  "workspaces/:workspaceId/archive",
  "workspaces/:workspaceId/inbox",
  "workspaces/:workspaceId/requests/:requestKind/:requestId",
  "workspaces/:workspaceId/milestones",
  "workspaces/:workspaceId/milestones/:milestoneId/revisions/:milestoneRevision",
  "workspaces/:workspaceId/milestones/:milestoneId/:milestoneTab?",
  "workspaces/:workspaceId/edit/:workspaceTab?",
  "projects/:projectId",
  "projects/:projectId/overview",
  "projects/:projectId/archive",
  "projects/:projectId/inbox",
  "projects/:projectId/inbox/:questionId",
  "projects/:projectId/edit/:projectTab?",
  "projects/:projectId/tasks/:taskKey/:tab?/:runId?",
  "projects/:projectId/tasks/:taskKey/:tab/:runId/integrations/:integrationId",
  "projects/:projectId/milestones",
  "projects/:projectId/milestones/:milestoneId/:milestoneTab?",
];
const nestedProjectPaths = pagePaths
  .filter((path) => path.startsWith("projects/"))
  .map((path) => `workspaces/:workspaceId/${path}`);
export const workspacePaths = [...pagePaths, ...nestedProjectPaths].flatMap(
  (path) => [path, path + "/related/questions/:relatedQuestionId"],
);
export function withoutRelatedQuestion(path: string) {
  return path.replace(
    /^(\/(?:(?:workspaces\/[^/]+\/)?projects\/[^/]+|workspaces\/[^/]+)(?:\/.*)?)\/related\/questions\/[^/]+\/?$/,
    "$1",
  );
}
export function relatedQuestionHref(path: string, id: string) {
  const project = /(?:^|\/)projects\/([^/]+)/.exec(path)?.[1];
  return project
    ? `/projects/${project}/inbox/${encodeURIComponent(id)}`
    : withoutRelatedQuestion(path) +
        "/related/questions/" +
        encodeURIComponent(id);
}
export type TaskTab = "task" | "changes" | "history";
export function taskTab(tab?: string): TaskTab {
  if (tab === "result" || tab === "changes") return "changes";
  if (tab === "runs" || tab === "activity" || tab === "history")
    return "history";
  return "task";
}
export function canonicalHref(path: string, owners: Record<string, string>) {
  if (!path.startsWith("/") || path.startsWith("//")) return path;
  const url = new URL(path, window.location.origin);
  const nested = url.pathname.match(
    /^\/workspaces\/([^/]+)(\/projects\/([^/]+)(?:\/.*)?)$/,
  );
  if (nested && nested[3] !== "new") url.pathname = nested[2];
  const workspaceTask = url.pathname.match(
    /^\/projects\/(workspace_([^/]+))(\/tasks\/W-[1-9][0-9]*(?:\/.*)?)$/,
  );
  if (
    workspaceTask &&
    owners[decodeURIComponent(workspaceTask[1])] ===
      decodeURIComponent(workspaceTask[2])
  ) {
    url.pathname = `${workspaceHref(decodeURIComponent(workspaceTask[2]))}${workspaceTask[3]}`;
  }
  const project = url.pathname.match(
    /^\/projects\/([^/]+)(\/(?:archive|inbox|milestones))?\/?$/,
  );
  const owner =
    nested?.[1] ??
    (project ? owners[decodeURIComponent(project[1])] : undefined);
  if (project && owner) {
    url.pathname =
      workspaceHref(decodeURIComponent(owner)) + (project[2] ?? "/tasks");
    url.searchParams.set("project", decodeURIComponent(project[1]));
  }
  if (/^\/workspaces\/[^/]+\/?$/.test(url.pathname)) {
    const filtered = ["project", "milestone", "label"].some((key) =>
      url.searchParams.has(key),
    );
    url.pathname =
      url.pathname.replace(/\/$/, "") + (filtered ? "/tasks" : "/inbox");
  }
  return url.pathname + url.search + url.hash;
}
export function boardHref(
  workspaceId: string,
  filters: { project?: string; milestone?: string; label?: string } = {},
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
export function taskHref(
  projectId: string,
  task: { key: string },
  tab:
    TaskTab | "info" | "dependencies" | "result" | "activity" | "runs" = "task",
) {
  const owner =
    projectId.startsWith("workspace_") && task.key.startsWith("W-")
      ? workspaceHref(projectId.slice("workspace_".length))
      : projectHref(projectId);
  return `${owner}/tasks/${encodeURIComponent(task.key)}${taskTab(tab) === "task" ? "" : `/${taskTab(tab)}`}`;
}
function editorIdentity(path: string) {
  return withoutRelatedQuestion(path)
    .replace(
      /^\/settings\/(harnesses|appearance|notifications|updates)\/?$/,
      "/settings",
    )
    .replace(
      /^(\/(?:(?:workspaces\/[^/]+\/)?projects\/[^/]+|workspaces\/[^/]+)\/edit)\/(general|info|coordinator|workers|integration)\/?$/,
      "$1",
    )
    .replace(
      /\/(conversation(?:\/[^/]+)?|info|dependencies|task|changes(?:\/[^/]+)?|history(?:\/[^/]+)?(?:\/integrations\/[^/]+)?|result(?:\/[^/]+)?|activity|tasks|runs(?:\/[^/]+)?(?:\/integrations\/[^/]+)?)\/?$/,
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
      : /\/milestones\/[^/]+$/.test(path)
        ? [
            params.has("verification")
              ? "review"
              : (params.get("tab") ?? "details"),
            params.get("verification") ?? "",
          ]
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
    pathname: withoutRelatedQuestion(location.pathname),
    changeLocation,
    notFound:
      matches.at(-1)?.id === "not-found" ||
      (!!params.milestoneTab &&
        !["tasks", "settings"].includes(params.milestoneTab)) ||
      (!!params.tab &&
        ![
          "task",
          "conversation",
          "changes",
          "history",
          "info",
          "dependencies",
          "result",
          "activity",
          "runs",
          "settings",
        ].includes(params.tab)),
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
