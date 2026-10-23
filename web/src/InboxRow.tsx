import { useContext } from "react";
import type { components } from "./api-schema";
import { ResourceNavigation } from "./WorkspaceTabs";
import { followLink } from "./navigation";
import { WorkspaceLink } from "./WorkspaceLink";
import { WorkIdentity } from "./WorkIdentity";
import { Timestamp } from "./Timestamp";

/** All Inbox collections navigate to exact resources without mounting responses. */
export function InboxRow({
  href,
  state,
  itemKey,
  title,
  date,
}: {
  href: string;
  state: components["schemas"]["WorkState"];
  itemKey?: string | null;
  title: string;
  date: string;
}) {
  const open = useContext(ResourceNavigation);
  return (
    <li>
      <WorkspaceLink
        className="work-navigation-row inbox-history-row"
        to={href}
        onClick={
          open
            ? (event) => followLink(event, () => open(href, "open"))
            : undefined
        }
      >
        <WorkIdentity state={state} itemKey={itemKey} title={title} />
        <span className="inbox-history-metadata">
          <span className="detail-metadata">
            <Timestamp date={date} />
          </span>
        </span>
      </WorkspaceLink>
    </li>
  );
}
