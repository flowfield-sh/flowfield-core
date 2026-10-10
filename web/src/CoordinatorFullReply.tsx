import { reportError } from "./requestFeedback";
import { useState } from "react";
import { Disclosure } from "./DetailLayout";
import { Markdown } from "./Markdown";
import { request } from "./workspace";
import { Button } from "@/components/ui/button";

// Long public replies remain saved even when the activity preview is abridged.
// Reuse the revision-checked full-text endpoint; page only on explicit demand.
export function CoordinatorFullReply({
  project,
  turn,
  revision,
}: {
  project: string;
  turn: string;
  revision: number;
}) {
  const [text, setText] = useState("");
  const [offset, setOffset] = useState<number | null>(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [omitted, setOmitted] = useState(false);
  async function load() {
    if (busy || offset === null) return;
    setBusy(true);
    setError("");
    try {
      const query = new URLSearchParams({
        resource: "coordinator",
        identity: turn,
        field: "coordinator",
        revision: String(revision),
        offset: String(offset),
      });
      const value = await request<{
        text: string;
        next_offset: number | null;
        output_omitted: boolean;
      }>(`context/projects/${encodeURIComponent(project)}/text?${query}`);
      setText((current) => current + value.text);
      setOffset(value.next_offset);
      setOmitted(value.output_omitted);
    } catch (error) {
      setError((error as Error).message);
      reportError(error, "Could not load full reply");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Disclosure
      summary="Read full reply"
      onToggle={(event) => {
        if (event.currentTarget.open && !text) void load();
      }}
    >
      <Markdown>{text}</Markdown>
      {omitted && (
        <p className="detail-metadata">Some original output was not saved.</p>
      )}
      {offset !== null && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => void load()}
        >
          {busy ? "Loading…" : error ? "Retry" : "Load more"}
        </Button>
      )}
    </Disclosure>
  );
}
