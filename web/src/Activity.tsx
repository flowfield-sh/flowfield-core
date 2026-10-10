import { authorLabel } from "./workspace";
import { Disclosure } from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import { ResourceRetry } from "./ResourceRetry";
import { useResource } from "./useResource";
import { useState } from "react";
import { Markdown } from "./Markdown";
import { type ActivityEntry } from "./workspace";

export function RelatedActivity({
  path,
  id,
  label,
}: {
  path: string;
  id: string;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const resource = useResource<ActivityEntry>(
    open ? `${path}/${encodeURIComponent(id)}` : null,
    null,
  );
  const entry = resource.data;
  return (
    <Disclosure
      summary={<>{label}</>}
      className="history-disclosure"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <ResourceRetry resources={[resource]} />
      {entry ? (
        <>
          <p className="detail-metadata">
            {authorLabel(entry.author)} · <Timestamp date={entry.created_at} />
          </p>
          <Markdown>{entry.body}</Markdown>
        </>
      ) : (
        resource.loading && <p>Loading entry…</p>
      )}
    </Disclosure>
  );
}
