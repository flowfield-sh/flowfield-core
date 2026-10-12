"""Human-facing project resource locations, independent of HTTP API routes."""

from urllib.parse import quote


def project_href(identity: str) -> str:
    return f"/projects/{quote(identity, safe='')}"
