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
  "workspaces/:workspaceId/milestones/new",
  "workspaces/:workspaceId/milestones/:milestoneId/:milestoneTab?",
  "workspaces/:workspaceId/tasks/new",
  "workspaces/:workspaceId/edit/:workspaceTab?",
  "projects/:projectId",
  "projects/:projectId/archive",
  "projects/:projectId/inbox",
  "projects/:projectId/inbox/:questionId",
  "projects/:projectId/edit/:projectTab?",
  "projects/:projectId/tasks/new/:tab?",
  "projects/:projectId/tasks/:taskKey/:tab?/:runId?",
  "projects/:projectId/tasks/:taskKey/:tab/:runId/integrations/:integrationId",
  "projects/:projectId/milestones",
  "projects/:projectId/milestones/new",
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
  return path.replace(/^\/projects\/([^/?#]+)/, (prefix, id: string) =>
    owners[decodeURIComponent(id)]
      ? `${workspaceHref(owners[decodeURIComponent(id)])}${prefix}`
      : prefix,
  );
}
export function projectHref(id: string, workspaceId?: string) {
  return `${workspaceId ? workspaceHref(workspaceId) : ""}/projects/${encodeURIComponent(id)}`;
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
      /^(\/(?:(?:workspaces\/[^/]+\/)?projects\/[^/]+|workspaces\/[^/]+)\/edit)\/(general|info|coordinator|workers|integration|labels)\/?$/,
      "$1",
    )
    .replace(
      /\/(conversation(?:\/[^/]+)?|info|dependencies|task|changes(?:\/[^/]+)?|history(?:\/[^/]+)?(?:\/integrations\/[^/]+)?|result(?:\/[^/]+)?|activity|tasks|runs(?:\/[^/]+)?(?:\/integrations\/[^/]+)?)\/?$/,
      "",
    );
}
export function entityPage(path: string) {
  return (
    /\/(?:projects|documents)\/new$/.test(withoutQuestionOverlay(path)) ||
    /^\/(?:(?:workspaces\/[^/]+\/)?projects\/[^/]+|workspaces\/[^/]+)\/(?:tasks\/[^/]+|milestones\/[^/]+|inbox\/[^/]+|edit)(?:\/.*)?$/.test(
      withoutQuestionOverlay(path),
    )
  );
}
export function collectionPath(path: string) {
  const base = withoutQuestionOverlay(path);
  return base
    .replace(/\/(projects|documents)\/new$/, "/$1")
    .replace(
      /^(\/(?:(?:workspaces\/[^/]+\/)?projects\/[^/]+|workspaces\/[^/]+))\/(tasks\/.*|edit(?:\/.*)?)$/,
      "$1",
    )
    .replace(
      /^(\/(?:(?:workspaces\/[^/]+\/)?projects\/[^/]+|workspaces\/[^/]+))\/(milestones|inbox)\/.*$/,
      "$1/$2",
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
) {
  if (!entityPage(target)) return null;
  const current = withoutQuestionOverlay(pathname);
  const sameEditor =
    entityPage(current) && editorIdentity(current) === editorIdentity(target);
  const sameProject = current.split("/")[2] === target.split("/")[2];
  const workspaceOrigin =
    current.startsWith("/workspaces/") ||
    state?.backgroundPath?.startsWith("/workspaces/");
  const retainOrigin =
    sameProject || (workspaceOrigin && target.startsWith("/projects/"));
  const betweenTasks =
    retainOrigin &&
    /^\/(?:workspaces\/[^/]+\/)?projects\/[^/]+\/tasks\/[^/]+/.test(current) &&
    /^\/(?:workspaces\/[^/]+\/)?projects\/[^/]+\/tasks\/[^/]+/.test(target);
  const index = window.history.state?.idx;
  return {
    backgroundPath: retainOrigin
      ? (state?.backgroundPath ?? collectionPath(current))
      : collectionPath(target),
    returnTo: !retainOrigin
      ? undefined
      : replace || sameEditor || betweenTasks || current.endsWith("/new")
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
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      !bypass.current &&
      ((chatSettingsDirty &&
        workspaceOwner(currentLocation.pathname) !==
          workspaceOwner(nextLocation.pathname)) ||
        (overlayDirty && currentLocation.pathname !== nextLocation.pathname) ||
        (unsaved &&
          editorIdentity(
            canonicalHref(currentLocation.pathname, projectOwners),
          ) !==
            editorIdentity(
              canonicalHref(nextLocation.pathname, projectOwners),
            ))),
  );
  useEffect(() => {
    const target = canonicalHref(location.pathname, projectOwners);
    if (target !== location.pathname)
      void navigate(target + location.search + location.hash, {
        replace: true,
        state: location.state,
      });
  }, [location, navigate, projectOwners]);
  const changeLocation = useCallback(
    (url: string, replace = false) => {
      url = canonicalHref(url, projectOwners);
      bypass.current = replace;
      void navigate(url, {
        replace,
        state: entityNavigationState(
          location.pathname,
          location.state,
          url,
          replace,
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
        if (
          editorIdentity(location.pathname) !==
          editorIdentity(blocker.location.pathname)
        )
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
