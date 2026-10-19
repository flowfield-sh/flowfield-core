import refractor from "refractor/core";
import python from "refractor/lang/python";
import typescript from "refractor/lang/typescript";
import jsx from "refractor/lang/jsx";
import tsx from "refractor/lang/tsx";
import json from "refractor/lang/json";
import bash from "refractor/lang/bash";

for (const language of [python, typescript, jsx, tsx, json, bash])
  refractor.register(language);

const languages: Record<string, string> = {
  py: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  ts: "typescript",
  jsx: "jsx",
  tsx: "tsx",
  json: "json",
  sh: "bash",
  bash: "bash",
  html: "markup",
  css: "css",
};

export { refractor };
export const codeLanguage = (path: string) =>
  languages[path.split(".").at(-1)?.toLowerCase() ?? ""];
