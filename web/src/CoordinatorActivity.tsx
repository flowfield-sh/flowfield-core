import type { components } from "./api-schema";
import { ContentStack, Disclosure } from "./DetailLayout";
import { Markdown } from "./Markdown";
import { ActivityEntries } from "./RunActivity";

type Items = components["schemas"]["CoordinatorTurn"]["activity"]["items"];

function Reply({ item }: { item: Items[number] }) {
  return (
    <div data-kind="agent">
      <Markdown>{item.text}</Markdown>
      {item.omitted && <p className="muted">Reply preview shortened.</p>}
    </div>
  );
}

function Timeline({ items }: { items: Items }) {
  const groups: Items[] = [];
  for (const item of items) {
    const previous = groups.at(-1);
    if (previous && item.kind !== "agent" && previous[0].kind !== "agent")
      previous.push(item);
    else groups.push([item]);
  }
  return (
    <ContentStack>
      {groups.map(([entry, ...rest]) =>
        entry.kind === "agent" ? (
          <Reply key={entry.key} item={entry} />
        ) : (
          <ContentStack
            key={entry.key}
            space="flush"
            className="activity-events"
          >
            <ActivityEntries items={[entry, ...rest]} />
          </ContentStack>
        ),
      )}
    </ContentStack>
  );
}

/** Live chronology folds once the turn settles; the last reply stays readable. */
export function CoordinatorActivity({
  items,
  finished,
}: {
  items: Items;
  finished: boolean;
}) {
  if (!finished) return <Timeline items={items} />;
  const last = items.at(-1);
  const reply = last?.kind === "agent" ? last : null;
  const history = reply ? items.slice(0, -1) : items;
  return (
    <>
      {!!history.length && (
        <Disclosure
          className="coordinator-activity-summary"
          summary={`Activity · ${history.length} ${history.length === 1 ? "event" : "events"}`}
        >
          <Timeline items={history} />
        </Disclosure>
      )}
      {reply && <Reply item={reply} />}
    </>
  );
}
