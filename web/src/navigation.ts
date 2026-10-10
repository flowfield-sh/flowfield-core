import { useCallback, useRef, type MouseEvent } from "react";
import { useBlocker, useLocation, useMatches, useNavigate } from "react-router";

const pagePaths = [
  "settings/harnesses",
  "settings/appearance",
  "new-project",
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
export const workspacePaths = pagePaths.flatMap((path) => [
  path,
  path + "/related/questions/:overlayQuestionId",
]);
export function withoutQuestionOverlay(path: string) {
  return path.replace(
    /^(\/projects\/[^/]+(?:\/.*)?)\/related\/questions\/[^/]+\/?$/,
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
export function projectHref(id: string) {
  return `/projects/${encodeURIComponent(id)}`;
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
    .replace(/^\/settings\/(harnesses|appearance)\/?$/, "/settings")
    .replace(
      /^(\/projects\/[^/]+\/edit)\/(general|info|coordinator|workers|integration)\/?$/,
      "$1",
    )
    .replace(
      /\/(conversation(?:\/[^/]+)?|info|dependencies|task|changes(?:\/[^/]+)?|history(?:\/[^/]+)?(?:\/integrations\/[^/]+)?|result(?:\/[^/]+)?|activity|tasks|runs(?:\/[^/]+)?(?:\/integrations\/[^/]+)?)\/?$/,
      "",
    );
}
export function entityPage(path: string) {
  return /^\/projects\/[^/]+\/(?:tasks\/[^/]+|milestones\/[^/]+|inbox\/[^/]+|edit)(?:\/.*)?$/.test(
    withoutQuestionOverlay(path),
  );
}
export function collectionPath(path: string) {
  const base = withoutQuestionOverlay(path);
  return base
    .replace(/^(\/projects\/[^/]+)\/(tasks\/.*|edit(?:\/.*)?)$/, "$1")
    .replace(/^(\/projects\/[^/]+)\/(milestones|inbox)\/.*$/, "$1/$2");
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
  const betweenTasks =
    sameProject &&
    /^\/projects\/[^/]+\/tasks\/[^/]+/.test(current) &&
    /^\/projects\/[^/]+\/tasks\/[^/]+/.test(target);
  const index = window.history.state?.idx;
  return {
    backgroundPath: sameProject
      ? (state?.backgroundPath ?? collectionPath(current))
      : collectionPath(target),
    returnTo: !sameProject
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
) {
  const location = useLocation();
  const navigate = useNavigate();
  const matches = useMatches();
  const params = matches.at(-1)?.params ?? {};
  const bypass = useRef(false);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      !bypass.current &&
      ((chatSettingsDirty &&
        currentLocation.pathname.split("/")[2] !==
          nextLocation.pathname.split("/")[2]) ||
        (overlayDirty && currentLocation.pathname !== nextLocation.pathname) ||
        (unsaved &&
          editorIdentity(currentLocation.pathname) !==
            editorIdentity(nextLocation.pathname))),
  );
  const changeLocation = useCallback(
    (url: string, replace = false) => {
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
    [navigate, location],
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
      const target = location.state?.backgroundPath ?? fallback;
      void navigate(target === "/new-project" ? "/" : target, {
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
