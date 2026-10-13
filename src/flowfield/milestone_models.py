"""Small immutable milestone bindings shared by preparation and source freshness."""

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, Field, StringConstraints


def source_link(value: str) -> str:
    if value:
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            raise ValueError("Use an HTTP or HTTPS source URL, or leave it empty.")
    return value


SourceURL = Annotated[str, StringConstraints(max_length=2000), AfterValidator(source_link)]


class MilestoneBinding(BaseModel):
    workspace_id: str
    id: str
    key: str
    title: str
    revision: int = Field(ge=1)
    origin: Literal["preparation", "upgrade"] = "preparation"


class MilestoneUpdate(BaseModel):
    workspace_id: str
    id: str
    key: str
    title: str
    prepared_revision: int
    latest_revision: int
