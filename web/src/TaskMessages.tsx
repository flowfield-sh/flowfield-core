import { useState } from "react";
import { useResource } from "./useResource";
import { ResourceRetry } from "./ResourceRetry";
import { Markdown } from "./Markdown";
import { Button } from "@/components/ui/button";
import type { components } from "./api-schema";
export type Message = components["schemas"]["ThreadMessage"];

export function merge(old: Message[], next: Message[]) {
  return [
    ...new Map([...old, ...next].map((item) => [item.id, item])).values(),
  ].sort(
    (a, b) =>
      a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );
}
export function MessageBody({
  message,
  path,
}: {
  message: Message;
  path: string;
}) {
  const [full, setFull] = useState(false);
  const source = useResource<Message>(
    full ? `${path}/thread/${encodeURIComponent(message.id)}?full=true` : null,
    message.revision,
  );
  return (
    <>
      {message.question && (
        <p>
          <strong>{message.question}</strong>
        </p>
      )}
      {message.body && <Markdown>{source.data?.body ?? message.body}</Markdown>}
      <ResourceRetry resources={[source]} />
      {message.truncated && !source.data && (
        <Button
          variant="outline"
          size="sm"
          disabled={source.loading}
          onClick={() => setFull(true)}
        >
          Read full entry
        </Button>
      )}
    </>
  );
}

export function hasBody(message: Message) {
  if (message.kind === "review") return true;
  return !!(
    message.body ||
    message.question_id ||
    message.earlier_id ||
    message.successor_id ||
    ["attempt", "result", "question", "definition"].includes(message.kind)
  );
}
