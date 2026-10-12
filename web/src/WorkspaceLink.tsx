import { Link, useLocation, type LinkProps } from "react-router";
import { createContext, useContext, type ComponentProps } from "react";
import { canonicalHref, entityNavigationState } from "./navigation";

export const ProjectOwners = createContext<Record<string, string>>({});
export function ScopedAnchor({ href, ...props }: ComponentProps<"a">) {
  const owners = useContext(ProjectOwners);
  return <a {...props} href={href ? canonicalHref(href, owners) : href} />;
}

/** Native link semantics with the same overlay return path as button navigation. */
export function WorkspaceLink({
  to,
  ...props
}: Omit<LinkProps, "to" | "state"> & { to: string }) {
  const location = useLocation();
  to = canonicalHref(to, useContext(ProjectOwners));
  return (
    <Link
      {...props}
      to={to}
      state={entityNavigationState(location.pathname, location.state, to)}
    />
  );
}
