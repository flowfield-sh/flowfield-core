import { lazy, Suspense, useState } from "react";
import type { components } from "./api-schema";
import { PreviewUnavailable } from "./PreviewUnavailable";

type OutputFile = components["schemas"]["OutputFile"];
const TextPreview = lazy(() => import("./TextPreview"));
export const outputHref = (file: OutputFile) =>
  `/api/workspaces/${file.workspace_id}/files/${file.id}/content`;

const imageTypes = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);

/** Shared preview boundary. Only explicitly supported passive formats render inline. */
export function OutputPreview({ file }: { file: OutputFile }) {
  const [failed, setFailed] = useState(false);
  if (
    file.media_type.startsWith("text/") ||
    /\.(md|markdown|txt|log|json|jsonl|yaml|yml|toml|csv|tsv|ini|cfg|xml|html|svg|css|scss|js|mjs|cjs|ts|jsx|tsx|py|sh|bash|zsh|sql|rs|go|java|c|h|cpp|hpp|rb|php|swift|kt|r|diff|patch)$/i.test(
      file.name,
    ) ||
    /(^|\/)(readme|license|makefile|dockerfile)$/i.test(file.name)
  )
    return (
      <Suspense fallback={<p>Loading preview…</p>}>
        <TextPreview file={file} />
      </Suspense>
    );
  if (!imageTypes.has(file.media_type)) return <PreviewUnavailable />;
  if (failed) return <PreviewUnavailable />;
  return (
    <a
      href={outputHref(file)}
      target="_blank"
      rel="noreferrer"
      aria-label={`Open ${file.name}`}
      className="block rounded-md border bg-muted p-2"
    >
      <img
        src={outputHref(file)}
        alt={file.name}
        loading="lazy"
        onError={() => setFailed(true)}
        className="mx-auto max-h-[65vh] max-w-full object-contain"
      />
    </a>
  );
}
