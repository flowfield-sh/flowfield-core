"""Workspace ownership with historical-schema construction for migration verification."""

import json
import sqlite3
from typing import TYPE_CHECKING

from pydantic import BaseModel

from flowfield.storage import version

if TYPE_CHECKING:
    from flowfield.application import Workspace


def column(db: sqlite3.Connection) -> str:
    return "workspace_id" if version(db) >= 59 else "project_id"


def validate(db: sqlite3.Connection, store: "Workspace", identity: str) -> None:
    if version(db) >= 59:
        from flowfield.workspaces import Workspaces

        Workspaces.read(db, identity)
    else:
        store._project(db, identity)


def serialized(db: sqlite3.Connection, record: BaseModel) -> str:
    value = record.model_dump(mode="json")
    if version(db) < 59:
        value["project_id"] = value.pop("workspace_id")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
