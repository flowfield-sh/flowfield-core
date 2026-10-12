import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { useId, useState, type ReactNode } from "react";
import { WorkspaceLink as Link } from "./WorkspaceLink";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function MarkdownLink({
  href,
  children,
}: {
  href?: string;
  children: ReactNode;
}) {
  if (!href) return <span>{children}</span>;
  let target: URL | undefined;
  try {
    target = new URL(href, window.location.href);
  } catch {
    // Invalid authored links remain readable without breaking the surrounding view.
  }
  if (!target) return <span>{children}</span>;
  const internal = target.origin === window.location.origin;
  if (
    internal &&
    (target.pathname === "/" ||
      target.pathname.startsWith("/projects/") ||
      target.pathname.startsWith("/workspaces/"))
  ) {
    return (
      <Link to={target.pathname + target.search + target.hash}>{children}</Link>
    );
  }
  return (
    <a
      href={href}
      target={internal ? undefined : "_blank"}
      rel={internal ? undefined : "noopener noreferrer"}
    >
      {children}
    </a>
  );
}

export function Markdown({
  children,
  links = true,
}: {
  children: string;
  links?: boolean;
}) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ href, children }) =>
            links ? (
              <MarkdownLink href={href}>{children}</MarkdownLink>
            ) : (
              <span>{children}</span>
            ),
          img: ({ alt }) => <span>{alt || "Image"}</span>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

export function MarkdownField({
  label,
  value,
  onChange,
  rows = 5,
  previewEnabled = true,
  maxLength = 200000,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  previewEnabled?: boolean;
  maxLength?: number;
}) {
  const id = useId();
  const [preview, setPreview] = useState(false);
  return (
    <div className="markdown-field content-stack">
      <div className="markdown-toolbar">
        <Label className="field block" htmlFor={id}>
          {label}
        </Label>
        {previewEnabled && (
          <div role="group" className="actions" aria-label={`${label} format`}>
            <Button
              size="sm"
              variant={preview ? "ghost" : "secondary"}
              type="button"
              aria-pressed={!preview}
              onClick={() => setPreview(false)}
            >
              Write
            </Button>
            <Button
              size="sm"
              variant={preview ? "secondary" : "ghost"}
              type="button"
              aria-pressed={preview}
              onClick={() => setPreview(true)}
            >
              Preview
            </Button>
          </div>
        )}
      </div>
      <Textarea
        id={id}
        value={value}
        rows={rows}
        maxLength={maxLength}
        hidden={preview}
        onChange={(e) => onChange(e.target.value)}
      />
      {preview && (
        <div className="markdown-preview" aria-label={`${label} preview`}>
          <Markdown>{value || "Nothing to preview yet."}</Markdown>
        </div>
      )}
    </div>
  );
}
