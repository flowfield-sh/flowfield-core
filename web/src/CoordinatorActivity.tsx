import type { components } from "./api-schema";
import { ContentStack, Disclosure } from "./DetailLayout";
import { Markdown } from "./Markdown";
import { ActivityEntries } from "./RunActivity";

type Items = components["schemas"]["CoordinatorTurn"]["activity"]["items"];

function Reply({
  item,
  intermediate = false,
}: {
  item: Items[number];
  intermediate?: boolean;
}) {
  return (
    <div
      data-kind="agent"
      className={intermediate ? "activity-prose" : undefined}
    >
      <Markdown>{item.text}</Markdown>
      {item.omitted && (
        <p className="muted">
          {intermediate
            ? "Reply preview shortened."
            : "Some reply output could not be retained."}
        </p>
      )}
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
          <Reply key={entry.key} item={entry} intermediate />
        ) : (
          <ContentStack
            key={entry.key}

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
  const reply = [...items].reverse().find((item) => item.kind === "agent");
  const history = items.filter((item) => item !== reply);
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
