import type { components } from "./api-schema";

export type AdjacentContext = {
  view: components["schemas"]["CoordinatorViewSelection"] | null;
  task: components["schemas"]["CoordinatorTaskSelection"] | null;
};
export type AdjacentSnapshot = {
  workspaceId: string;
  pathname: string;
  context: AdjacentContext | null;
};
