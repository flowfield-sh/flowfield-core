import { reportError } from "./requestFeedback";
import { DetailSection } from "./DetailLayout";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useEffect, useMemo, useRef, useState } from "react";
import { Diff, Hunk, Decoration, parseDiff, tokenize } from "react-diff-view";
import refractor from "refractor/core";
import python from "refractor/lang/python";
import typescript from "refractor/lang/typescript";
import jsx from "refractor/lang/jsx";
import tsx from "refractor/lang/tsx";
import json from "refractor/lang/json";
import bash from "refractor/lang/bash";
import "react-diff-view/style/index.css";
import { useResource } from "./useResource";
import { request, label } from "./workspace";
import type { components } from "./api-schema";

for (const language of [python, typescript, jsx, tsx, json, bash])
  refractor.register(language);
type Files = components["schemas"]["ChangedFiles"];
type Patch = components["schemas"]["FilePatch"];
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

export type DiffViewState = { selected: number; mode: "unified" | "split" };

export default function CodeDiff({
  path,
  result,
  view,
  changeView,
}: {
  path: string;
  result: string;
  view: DiffViewState;
  changeView: (view: DiffViewState) => void;
}) {
  const [retry, setRetry] = useState(0);
  const files = useResource<Files>(`${path}/diff`, `${result}:${retry}`);
  const moreRequest = useRef<AbortController | null>(null);
  const { selected, mode } = view;
  const setSelected = (selected: number) => changeView({ ...view, selected });
  const setMode = (mode: DiffViewState["mode"]) =>
    changeView({ ...view, mode });
  const [loadingMore, setLoadingMore] = useState(false);
  const [key, setKey] = useState({ path, result, retry });
  if (key.path !== path || key.result !== result || key.retry !== retry) {
    setKey({ path, result, retry });
    setLoadingMore(false);
  }
  const patch = useResource<Patch>(
    files.data?.files.length ? `${path}/diff/${selected}` : null,
    `${result}:${retry}`,
  );
  const preview = useMemo(() => {
    if (!patch.data?.text) return { files: [], error: "" };
    try {
      const parsed = parseDiff(patch.data.text);
      if (!parsed.length) throw new Error("No complete file found");
      return { files: parsed, error: "" };
    } catch {
      return {
        files: [],
        error:
          "This patch could not be displayed. Inspect the captured commits in Git.",
      };
    }
  }, [patch.data]);
  useEffect(() => {
    moreRequest.current?.abort();
    moreRequest.current = null;
    return () => moreRequest.current?.abort();
  }, [path, result, retry]);
  async function more() {
    if (files.data?.next_offset == null || moreRequest.current || files.loading)
      return;
    const pending = new AbortController();
    moreRequest.current = pending;
    setLoadingMore(true);
    try {
      const next = await request<Files>(
        `${path}/diff?offset=${files.data.next_offset}`,
        "GET",
        undefined,
        pending.signal,
      );
      if (pending.signal.aborted) return;
      files.setData((previous) => ({
        ...next,
        files: [...(previous?.files ?? []), ...next.files],
      }));
    } catch (e) {
      if (!pending.signal.aborted)
        reportError(e, "Could not load more changed files");
    } finally {
      if (!pending.signal.aborted) {
        moreRequest.current = null;
        setLoadingMore(false);
      }
    }
  }
  return (
    <DetailSection
      className="code-review"
      aria-label="Code changes"
      title={
        <>
          Changes{" "}
          {files.data && (
            <span className="muted">
              {files.data.total_files}{" "}
              {files.data.total_files === 1 ? "file" : "files"}
            </span>
          )}
        </>
      }
      actions={
        <div
          className="segmented-control"
          role="group"
          aria-label="Diff layout"
        >
          <Button
            size="xs"
            type="button"
            variant={mode === "unified" ? "secondary" : "ghost"}
            aria-pressed={mode === "unified"}
            onClick={() => setMode("unified")}
          >
            Unified
          </Button>
          <Button
            size="xs"
            type="button"
            variant={mode === "split" ? "secondary" : "ghost"}
            aria-pressed={mode === "split"}
            onClick={() => setMode("split")}
          >
            Split
          </Button>
        </div>
      }
    >
      {files.error && (
        <Button size="sm" onClick={() => setRetry(retry + 1)}>
          Retry
        </Button>
      )}
      {files.loading && <p className="muted">Loading changed files…</p>}
      {files.data?.total_files === 0 && (
        <p className="muted">No code changes between these commits.</p>
      )}
      {!!files.data?.files.length && (
        <div className="diff-browser">
          <nav className="changed-files" aria-label="Changed files">
            {files.data.files.map((file) => (
              <Button
                size="sm"
                variant="ghost"
                key={file.id}
                className={`file justify-start items-start text-left whitespace-normal h-auto ${selected === file.id ? "selected" : ""}`}
                aria-pressed={selected === file.id}
                onClick={() => setSelected(file.id)}
              >
                <span
                  className={`file-status ${file.change}`}
                  aria-label={label(file.change)}
                >
                  {file.change[0].toUpperCase()}
                </span>
                <span>{file.new_path ?? file.old_path}</span>
              </Button>
            ))}
            {files.data.next_offset !== null && (
              <Button
                size="sm"
                variant="outline"
                className="quiet"
                disabled={loadingMore || files.loading}
                onClick={() => void more()}
              >
                More files
              </Button>
            )}
          </nav>
          <div key={selected} className="file-preview" aria-live="polite">
            {patch.loading && <p className="muted">Loading file…</p>}
            {patch.error && (
              <Button size="sm" onClick={() => setRetry((v) => v + 1)}>
                Retry file
              </Button>
            )}
            {patch.data && (
              <>
                <div className="file-heading">
                  <strong>
                    {patch.data.file.new_path ?? patch.data.file.old_path}
                  </strong>
                  <span className="muted">
                    {label(patch.data.file.change)}
                    {patch.data.binary ? " · Binary" : ""}
                  </span>
                </div>
                {patch.data.file.change === "renamed" && (
                  <p className="muted">Previously {patch.data.file.old_path}</p>
                )}
                {patch.data.omitted_reason && (
                  <p className="notice">{patch.data.omitted_reason}</p>
                )}
                {preview.error && (
                  <Alert variant="destructive">
                    <AlertDescription>{preview.error}</AlertDescription>
                  </Alert>
                )}
                {preview.files.map((file, index) =>
                  file.hunks.length ? (
                    <HighlightedDiff
                      key={index}
                      file={file}
                      path={
                        patch.data!.file.new_path ??
                        patch.data!.file.old_path ??
                        ""
                      }
                      mode={mode}
                    />
                  ) : (
                    <p className="muted" key={index}>
                      No text changes.{" "}
                      {patch.data!.file.old_mode !== patch.data!.file.new_mode
                        ? `Mode ${patch.data!.file.old_mode} → ${patch.data!.file.new_mode}.`
                        : "Only file metadata changed."}
                    </p>
                  ),
                )}
              </>
            )}
          </div>
        </div>
      )}
    </DetailSection>
  );
}

function HighlightedDiff({
  file,
  path,
  mode,
}: {
  file: ReturnType<typeof parseDiff>[number];
  path: string;
  mode: "unified" | "split";
}) {
  const tokens = useMemo(() => {
    const language = languages[path.split(".").at(-1) ?? ""];
    if (!language) return undefined;
    try {
      return tokenize(file.hunks, { highlight: true, refractor, language });
    } catch {
      return undefined;
    }
  }, [file, path]);
  return (
    <Diff
      viewType={mode}
      diffType={file.type}
      hunks={file.hunks}
      tokens={tokens}
    >
      {(hunks) =>
        hunks.map((hunk) => <HunkBlock key={hunk.content} hunk={hunk} />)
      }
    </Diff>
  );
}
function HunkBlock({
  hunk,
}: {
  hunk: ReturnType<typeof parseDiff>[number]["hunks"][number];
}) {
  return (
    <>
      <Decoration>{hunk.content}</Decoration>
      <Hunk hunk={hunk} />
    </>
  );
}
