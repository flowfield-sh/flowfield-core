"""Validate workspace ownership for coordinator records."""

import json
import sqlite3
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from flowfield.application import Workspace


def validate(db: sqlite3.Connection, store: "Workspace", identity: str) -> None:
    from flowfield.workspaces import Workspaces

    Workspaces.read(db, identity)


def serialized(db: sqlite3.Connection, record: BaseModel) -> str:
    value = record.model_dump(mode="json")

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
