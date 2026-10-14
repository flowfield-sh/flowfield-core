import { useCallback, useEffect, useRef, type MouseEvent } from "react";
import { useBlocker, useLocation, useMatches, useNavigate } from "react-router";

const pagePaths = [
  "settings/harnesses",
  "settings/appearance",
  "settings/notifications",
  "settings/updates",
  "new-workspace",
  "workspaces/:workspaceId",
  "workspaces/:workspaceId/documents",
  "workspaces/:workspaceId/documents/:documentId",
  "workspaces/:workspaceId/documents/:documentId/revisions/:documentRevision",
  "workspaces/:workspaceId/projects",
  "workspaces/:workspaceId/projects/new",
  "workspaces/:workspaceId/archive",
  "workspaces/:workspaceId/inbox",
  "workspaces/:workspaceId/milestones",
  "workspaces/:workspaceId/milestones/:milestoneId/revisions/:milestoneRevision",
  "workspaces/:workspaceId/milestones/:milestoneId/:milestoneTab?",
  "workspaces/:workspaceId/edit/:workspaceTab?",
  "projects/:projectId",
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
  (path) => [path, path + "/related/questions/:overlayQuestionId"],
);
export function withoutQuestionOverlay(path: string) {
  return path.replace(
    /^(\/(?:(?:workspaces\/[^/]+\/)?projects\/[^/]+|workspaces\/[^/]+)(?:\/.*)?)\/related\/questions\/[^/]+\/?$/,
    "$1",
  );
}
export function questionOverlayHref(path: string, id: string) {
  return (
    withoutQuestionOverlay(path) +
    "/related/questions/" +
    encodeURIComponent(id)
  );
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
  const project = url.pathname.match(
    /^\/projects\/([^/]+)(\/(?:archive|inbox|milestones))?\/?$/,
  );
  const owner =
    nested?.[1] ??
    (project ? owners[decodeURIComponent(project[1])] : undefined);
  if (project && owner) {
    url.pathname =
      workspaceHref(decodeURIComponent(owner)) + (project[2] ?? "");
    url.searchParams.set("project", decodeURIComponent(project[1]));
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
    (archive ? "/archive" : "") +
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
  return `${projectHref(projectId)}/tasks/${encodeURIComponent(task.key)}${taskTab(tab) === "task" ? "" : `/${taskTab(tab)}`}`;
}
function editorIdentity(path: string) {
  return withoutQuestionOverlay(path)
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
export function entityPage(path: string) {
  return (
    /\/projects\/new$/.test(withoutQuestionOverlay(path)) ||
    /^\/(?:(?:workspaces\/[^/]+\/)?projects\/[^/]+|workspaces\/[^/]+)\/(?:tasks\/[^/]+|milestones\/[^/]+|documents\/[^/]+|inbox\/[^/]+|edit)(?:\/.*)?$/.test(
      withoutQuestionOverlay(path.split("?")[0]),
    )
  );
}
export function collectionPath(path: string) {
  const [pathname, query] = path.split("?");
  const base = withoutQuestionOverlay(pathname);
  return (
    base
      .replace(/\/(projects)\/new$/, "/$1")
      .replace(
        /^(\/(?:(?:workspaces\/[^/]+\/)?projects\/[^/]+|workspaces\/[^/]+))\/(tasks\/.*|edit(?:\/.*)?)$/,
        "$1",
      )
      .replace(
        /^(\/(?:(?:workspaces\/[^/]+\/)?projects\/[^/]+|workspaces\/[^/]+))\/(milestones|documents|inbox)\/.*$/,
        "$1/$2",
      ) + (query ? "?" + query : "")
  );
}
type EntityNavigationState = {
  backgroundPath?: string;
  returnTo?: { path: string; index: number };
};
export function entityNavigationState(
  pathname: string,
  state: EntityNavigationState | null,
  target: string,
  replace = false,
  projectOwners: Record<string, string> = {},
) {
  if (!entityPage(target)) return null;
  const current = withoutQuestionOverlay(pathname.split("?")[0]);
  const sameEditor =
    entityPage(current) && editorIdentity(current) === editorIdentity(target);
  const sameProject = current.split("/")[2] === target.split("/")[2];
  const workspaceOrigin =
    current.startsWith("/workspaces/") ||
    state?.backgroundPath?.startsWith("/workspaces/");
  const owner = (path: string) => {
    const [, kind, id] = path.split("/");
    return kind === "workspaces"
      ? id
      : kind === "projects"
        ? projectOwners[id]
        : undefined;
  };
  const sameWorkspace = !!owner(current) && owner(current) === owner(target);
  const retainOrigin =
    sameProject ||
    sameWorkspace ||
    (workspaceOrigin && target.startsWith("/projects/"));
  const milestonePath = (path: string) =>
    path.match(
      /^(\/workspaces\/[^/]+\/milestones\/[^/]+)(?:\/revisions\/[^/]+)?$/,
    )?.[1];
  const sameMilestone =
    !!milestonePath(current) &&
    milestonePath(current) === milestonePath(target);
  const betweenTasks =
    retainOrigin &&
    /^\/(?:workspaces\/[^/]+\/)?projects\/[^/]+\/tasks\/[^/]+/.test(current) &&
    /^\/(?:workspaces\/[^/]+\/)?projects\/[^/]+\/tasks\/[^/]+/.test(target);
  const index = window.history.state?.idx;
  return {
    backgroundPath: retainOrigin
      ? (state?.backgroundPath ?? collectionPath(pathname))
      : collectionPath(target),
    returnTo: !retainOrigin
      ? undefined
      : replace ||
          sameEditor ||
          sameMilestone ||
          betweenTasks ||
          current.endsWith("/new")
        ? state?.returnTo
        : typeof index === "number"
          ? { path: pathname, index }
          : undefined,
  };
}
export function useWorkspaceNavigation(
  unsaved: boolean,
  setUnsaved: (value: boolean) => void,
  overlayDirty: boolean,
  setOverlayDirty: (value: boolean) => void,
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
        (overlayDirty && currentLocation.pathname !== nextLocation.pathname) ||
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
    (url: string, replace = false) => {
      url = canonicalHref(url, projectOwners);
      if (
        !replace &&
        url === location.pathname + location.search + location.hash
      )
        return;
      bypass.current = replace;
      void navigate(url, {
        flushSync: !replace,
        replace,
        state: entityNavigationState(
          location.pathname + location.search,
          location.state,
          url,
          replace,
          projectOwners,
        ),
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
        setOverlayDirty(false);
        blocker.proceed();
      },
    },
    params,
    pathname: withoutQuestionOverlay(location.pathname),
    backgroundPath: location.state?.backgroundPath as string | undefined,
    closeEntity: (fallback: string) => {
      const origin = location.state?.returnTo;
      const index = window.history.state?.idx;
      if (origin && typeof index === "number" && origin.index < index) {
        void navigate(origin.index - index);
        return;
      }
      const target = canonicalHref(
        location.state?.backgroundPath ?? fallback,
        projectOwners,
      );
      void navigate(target === "/new-workspace" ? "/" : target, {
        replace: true,
        state: null,
      });
    },
    closeQuestion: () => {
      if (
        location.state?.overlayFrom ===
        withoutQuestionOverlay(location.pathname)
      )
        void navigate(-1);
      else
        void navigate(withoutQuestionOverlay(location.pathname), {
          replace: true,
          state: location.state,
        });
    },
    changeLocation,
    notFound:
      matches.at(-1)?.id === "not-found" ||
      (!!params.milestoneTab && params.milestoneTab !== "tasks") ||
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
