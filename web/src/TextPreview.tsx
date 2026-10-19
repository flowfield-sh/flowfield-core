import { useMemo, type ReactNode } from "react";
import { refractor, codeLanguage } from "./codeHighlight";
import { Markdown } from "./Markdown";
import { useResource } from "./useResource";
import { ResourceRetry } from "./ResourceRetry";
import type { components } from "./api-schema";
import { CsvPreview } from "./CsvPreview";

type Node = ReturnType<typeof refractor.highlight>[number];
function tokens(nodes: Node[]): ReactNode {
  return nodes.map((node, index) =>
    node.type === "text" ? (
      node.value
    ) : (
      <span
        key={index}
        className={
          Array.isArray(node.properties.className)
            ? node.properties.className.join(" ")
            : undefined
        }
      >
        {tokens(node.children as Node[])}
      </span>
    ),
  );
}
export default function TextPreview({
  file,
  compact,
}: {
  file: components["schemas"]["OutputFile"];
  compact: boolean;
}) {
  const resource = useResource<components["schemas"]["OutputText"]>(
    `workspaces/${file.workspace_id}/files/${file.id}/text`,
    file.id,
  );
  const text = resource.data?.text ?? "";
  const language = codeLanguage(file.name);
  const highlighted = useMemo(() => {
    if (!language || text.length > 16000) return text;
    try {
      return tokens(refractor.highlight(text, language));
    } catch {
      return text;
    }
  }, [language, text]);
  const markdown = /\.(md|markdown)$/i.test(file.name);
  return (
    <div className="text-preview content-stack min-h-0">
      <ResourceRetry resources={[resource]}>Retry preview</ResourceRetry>
      {resource.loading && !resource.data && (
        <p role="status">Loading preview…</p>
      )}
      {resource.data &&
        (resource.data.supported ? (
          <>
            <div
              aria-label={`Preview of ${file.name}`}
              tabIndex={0}
              className={`output-code min-h-0 min-w-0 overflow-auto rounded-md border p-3 ${compact ? "max-h-48" : ""}`}
            >
              {/\.csv$/i.test(file.name) || file.media_type === "text/csv" ? (
                <CsvPreview text={text} truncated={resource.data.truncated} />
              ) : markdown ? (
                <Markdown>{text}</Markdown>
              ) : (
                <pre
                  className={`m-0 text-xs ${language ? "" : "whitespace-pre-wrap break-words"}`}
                >
                  <code>{highlighted}</code>
                </pre>
              )}
            </div>
            {resource.data.truncated && (
              <p className="detail-metadata">
                Preview shows the first 64 KiB. Open or download the complete
                file.
              </p>
            )}
          </>
        ) : (
          <p className="detail-metadata">
            No text preview for this file. Open or download it.
          </p>
        ))}
    </div>
  );
}
