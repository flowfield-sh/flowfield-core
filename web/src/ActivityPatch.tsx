import { useMemo } from "react";
import { Diff, Hunk, parseDiff } from "react-diff-view";
import "react-diff-view/style/index.css";
import { Disclosure } from "./DetailLayout";

/** Captured patches reuse the result diff renderer; partial evidence stays readable. */
export default function ActivityPatch({ text }: { text: string }) {
  const files = useMemo(() => {
    try {
      return parseDiff(text);
    } catch {
      return [];
    }
  }, [text]);
  if (!files.length || !files.some((file) => file.hunks.length))
    return (
      <pre className="activity-console" tabIndex={0}>
        {text}
      </pre>
    );
  const prefix = text.slice(0, text.indexOf("diff --git ")).trim();
  return (
    <div className="activity-console activity-patch" tabIndex={0}>
      {prefix && <pre>{prefix}</pre>}
      {files.map((file, index) => (
        <div key={index}>
          <p>{file.newPath === "/dev/null" ? file.oldPath : file.newPath}</p>
          <Diff viewType="unified" diffType={file.type} hunks={file.hunks}>
            {(hunks) =>
              hunks.map((hunk) => <Hunk key={hunk.content} hunk={hunk} />)
            }
          </Diff>
        </div>
      ))}
      <Disclosure summary="Saved patch text" chevron={false}>
        <pre>{text}</pre>
      </Disclosure>
    </div>
  );
}
