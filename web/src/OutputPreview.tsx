import { useState } from "react";
import type { components } from "./api-schema";

type OutputFile = components["schemas"]["OutputFile"];
export const outputHref = (file: OutputFile) =>
  `/api/workspaces/${file.workspace_id}/files/${file.id}/content`;

const imageTypes = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);

/** Shared preview boundary. Only explicitly supported passive formats render inline. */
export function OutputPreview({
  file,
  compact = false,
}: {
  file: OutputFile;
  compact?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  if (!imageTypes.has(file.media_type)) return null;
  if (failed)
    return (
      <p className="detail-metadata">
        Preview unavailable. Open or download the file.
      </p>
    );
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
        className={
          compact
            ? "mx-auto max-h-48 max-w-full object-contain"
            : "mx-auto max-h-[65vh] max-w-full object-contain"
        }
      />
    </a>
  );
}
