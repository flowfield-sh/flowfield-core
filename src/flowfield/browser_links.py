"""Human-facing project resource locations, independent of HTTP API routes."""

from urllib.parse import quote


def project_href(identity: str) -> str:
    return f"/projects/{quote(identity, safe='')}"


def workspace_href(identity: str) -> str:
    return f"/workspaces/{quote(identity, safe='')}"


def request_href(workspace_id: str, kind: str, identity: str) -> str:
    if kind == "result":
        return f"{workspace_href(workspace_id)}/results/{quote(identity, safe='')}"
    family = "attempt" if kind in {"review", "intervention"} else kind
    path = (
        f"/workspaces/{quote(workspace_id, safe='')}/requests/{family}/{quote(identity, safe='')}"
    )
    return path
