import type { WorkspaceLabel } from "./labelContext";

export const labelColors = {
  green: {
    name: "Green",
    className:
      "border-green-300 bg-green-200 text-green-950 dark:border-green-600 dark:bg-green-800 dark:text-green-50",
  },
  "green-light": {
    name: "Light green",
    className:
      "border-green-200 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-200",
  },
  yellow: {
    name: "Yellow",
    className:
      "border-yellow-300 bg-yellow-200 text-yellow-950 dark:border-yellow-600 dark:bg-yellow-800 dark:text-yellow-50",
  },
  "yellow-light": {
    name: "Light yellow",
    className:
      "border-yellow-200 bg-yellow-50 text-yellow-800 dark:border-yellow-800 dark:bg-yellow-950 dark:text-yellow-200",
  },
  orange: {
    name: "Orange",
    className:
      "border-orange-300 bg-orange-200 text-orange-950 dark:border-orange-600 dark:bg-orange-800 dark:text-orange-50",
  },
  "orange-light": {
    name: "Light orange",
    className:
      "border-orange-200 bg-orange-50 text-orange-800 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-200",
  },
  red: {
    name: "Red",
    className:
      "border-red-300 bg-red-200 text-red-950 dark:border-red-600 dark:bg-red-800 dark:text-red-50",
  },
  "red-light": {
    name: "Light red",
    className:
      "border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200",
  },
  pink: {
    name: "Pink",
    className:
      "border-pink-300 bg-pink-200 text-pink-950 dark:border-pink-600 dark:bg-pink-800 dark:text-pink-50",
  },
  "pink-light": {
    name: "Light pink",
    className:
      "border-pink-200 bg-pink-50 text-pink-800 dark:border-pink-800 dark:bg-pink-950 dark:text-pink-200",
  },
  violet: {
    name: "Violet",
    className:
      "border-violet-300 bg-violet-200 text-violet-950 dark:border-violet-600 dark:bg-violet-800 dark:text-violet-50",
  },
  "violet-light": {
    name: "Light violet",
    className:
      "border-violet-200 bg-violet-50 text-violet-800 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-200",
  },
  blue: {
    name: "Blue",
    className:
      "border-blue-300 bg-blue-200 text-blue-950 dark:border-blue-600 dark:bg-blue-800 dark:text-blue-50",
  },
  "blue-light": {
    name: "Light blue",
    className:
      "border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200",
  },
  cyan: {
    name: "Cyan",
    className:
      "border-cyan-300 bg-cyan-200 text-cyan-950 dark:border-cyan-600 dark:bg-cyan-800 dark:text-cyan-50",
  },
  "cyan-light": {
    name: "Light cyan",
    className:
      "border-cyan-200 bg-cyan-50 text-cyan-800 dark:border-cyan-800 dark:bg-cyan-950 dark:text-cyan-200",
  },
  teal: {
    name: "Teal",
    className:
      "border-teal-300 bg-teal-200 text-teal-950 dark:border-teal-600 dark:bg-teal-800 dark:text-teal-50",
  },
  "teal-light": {
    name: "Light teal",
    className:
      "border-teal-200 bg-teal-50 text-teal-800 dark:border-teal-800 dark:bg-teal-950 dark:text-teal-200",
  },
  slate: {
    name: "Slate",
    className:
      "border-slate-300 bg-slate-200 text-slate-950 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-50",
  },
  "slate-light": {
    name: "Light slate",
    className:
      "border-slate-200 bg-slate-50 text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-200",
  },
} satisfies Record<
  WorkspaceLabel["color"],
  { name: string; className: string }
>;
