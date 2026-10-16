import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { DetailSection } from "./DetailLayout";
import { Markdown } from "./Markdown";

/** Bound the rendered preview, so wrapped paragraphs count as lines too. */
export function ExpandableMarkdown({
  children,
  title,
}: {
  children: string;
  title: string;
}) {
  const id = useId();
  const container = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [preview, setPreview] = useState({ height: 0, long: false });
  useEffect(() => {
    const markdown = content.current?.firstElementChild;
    if (!markdown) return;
    const observer = new ResizeObserver(() => {
      const height = parseFloat(getComputedStyle(markdown).lineHeight) * 10;
      const long = markdown.getBoundingClientRect().height > height + 1;
      setPreview((previous) =>
        previous.height === height && previous.long === long
          ? previous
          : { height, long },
      );
    });
    observer.observe(markdown);
    return () => observer.disconnect();
  }, []);
  const collapsed = preview.long && !expanded;
  return (
    <div ref={container} className="content-stack" data-space="tight">
      <DetailSection title={title}>
        <div
          id={id}
          ref={content}
          className="definition-preview"
          data-collapsed={collapsed}
          style={{ maxHeight: collapsed ? preview.height : undefined }}
          onFocusCapture={(event) => {
            // Keyboard navigation must never leave focus in clipped content.
            if (
              collapsed &&
              event.target.getBoundingClientRect().bottom >
                event.currentTarget.getBoundingClientRect().top +
                  preview.height * 0.65
            )
              setExpanded(true);
          }}
        >
          <Markdown>{children}</Markdown>
        </div>
        {preview.long && (
          <button
            type="button"
            className="disclosure-control definition-toggle"
            aria-expanded={expanded}
            aria-controls={id}
            onClick={() => {
              setExpanded(!expanded);
              if (expanded)
                requestAnimationFrame(() =>
                  container.current?.scrollIntoView({ block: "start" }),
                );
            }}
          >
            {expanded ? (
              <ChevronUp aria-hidden="true" />
            ) : (
              <ChevronDown aria-hidden="true" />
            )}
            {expanded ? "Collapse definition" : "Expand definition"}
          </button>
        )}
      </DetailSection>
    </div>
  );
}
