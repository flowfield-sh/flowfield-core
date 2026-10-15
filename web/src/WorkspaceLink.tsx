import { Link, useLocation, type LinkProps } from "react-router";
import { createContext, useContext, type ComponentProps } from "react";
import { ResourceNavigation } from "./WorkspaceTabs";
import { canonicalHref, followLink } from "./navigation";

export const ProjectOwners = createContext<Record<string, string>>({});
export function ScopedAnchor({ href, ...props }: ComponentProps<"a">) {
  const owners = useContext(ProjectOwners);
  return <a {...props} href={href ? canonicalHref(href, owners) : href} />;
}

/** Native link semantics with the same resource history as button navigation. */
export function WorkspaceLink({
  to,
  ...props
}: Omit<LinkProps, "to" | "state"> & { to: string }) {
  const location = useLocation();
  const owners = useContext(ProjectOwners);
  const navigate = useContext(ResourceNavigation);
  const resolved = new URL(
    to,
    window.location.origin + location.pathname + location.search,
  );
  const target = canonicalHref(
    resolved.pathname + resolved.search + resolved.hash,
    owners,
  );
  return (
    <Link
      {...props}
      to={target}
      onClick={(event) => {
        props.onClick?.(event);
        if (!event.defaultPrevented && navigate)
          followLink(event, () => navigate(target));
      }}
      state={location.state}
    />
  );
}
