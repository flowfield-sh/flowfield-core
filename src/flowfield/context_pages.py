"""Bounded source retrieval shared by scoped workers and both reviewer roles."""

import json

from pydantic import Field

from flowfield.execution_models import Record
from flowfield.source_navigation import Mode, navigate


class ReadContext(Record):
    section: str = Field(
        description="Exact section key from the brief or a returned section value."
    )
    offset: int = Field(default=0, ge=0)
    limit: int = Field(
        default=6000,
        ge=1,
        le=6000,
        description="Maximum characters per page, not source length. Omit for the default.",
    )
    end: int | None = Field(default=None, ge=0)
    mode: Mode = "read"
    query: str | None = Field(default=None, max_length=500)

    def page(self, text: str) -> str:
        page = navigate(
            text,
            mode=self.mode,
            offset=self.offset,
            limit=self.limit,
            end=self.end,
            query=self.query,
        )
        if page["next_request"] is not None:
            page["next_request"]["section"] = self.section
        return json.dumps(page)
