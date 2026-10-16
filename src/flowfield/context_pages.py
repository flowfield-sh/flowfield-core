"""Bounded source retrieval shared by scoped workers and both reviewer roles."""

import json

from pydantic import Field

from flowfield.errors import ApplicationError
from flowfield.execution_models import Record


class ReadContext(Record):
    section: str
    offset: int = Field(default=0, ge=0)

    def page(self, text: str) -> str:
        if self.offset > len(text):
            raise ApplicationError("invalid_offset", "Offset exceeds source length.")
        end = min(len(text), self.offset + 6000)
        return json.dumps(
            {
                "text": text[self.offset : end],
                "total_chars": len(text),
                "next_offset": end if end < len(text) else None,
            }
        )
