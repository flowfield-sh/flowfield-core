import { createContext, useContext } from "react";
import type { components } from "./api-schema";
export type WorkspaceLabel = components["schemas"]["WorkspaceLabel"];
export const LabelCatalog = createContext<WorkspaceLabel[]>([]);
export const useLabels = () => useContext(LabelCatalog);
