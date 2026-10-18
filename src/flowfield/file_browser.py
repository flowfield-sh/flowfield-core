"""Bounded directory views over retained files, using submitted folder paths."""

from typing import Literal

from pydantic import BaseModel

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.output_models import OutputFile, OutputSelection
from flowfield.outputs import Outputs
from flowfield.workspaces import Workspaces


class FileEntry(BaseModel):
    id: str
    name: str
    path: str
    kind: Literal["folder", "file"]
    file: OutputFile | None = None


class FileDirectory(BaseModel):
    items: list[FileEntry]
    next_offset: int | None = None


class FileWork(BaseModel):
    id: str
    title: str
    task_key: str | None = None


class FileWorkPage(BaseModel):
    items: list[FileWork]
    next_offset: int | None = None


class FileBrowser:
    def __init__(self, store: Workspace):
        self.store = store

    def work(self, workspace: str, offset: int = 0) -> FileWorkPage:
        with self.store.connection() as db:
            Workspaces.read(db, workspace)
            rows = db.execute(
                "WITH files AS (SELECT data FROM output_files WHERE workspace_id=?) "
                "SELECT 'task:'||json_extract(data,'$.task_key') id, "
                "json_extract(data,'$.task_key') task_key, "
                "json_extract(data,'$.task_title') title FROM files GROUP BY task_key "
                "ORDER BY max(json_extract(data,'$.created_at')) DESC,id LIMIT 51 OFFSET ?",
                (workspace, offset),
            ).fetchall()
            return FileWorkPage(
                items=[FileWork(**dict(row)) for row in rows[:50]],
                next_offset=offset + 50 if len(rows) > 50 else None,
            )

    def directory(
        self,
        workspace: str,
        path: str = "",
        *,
        task_key: str | None = None,
        sort: Literal["name", "newest", "type"] = "name",
        offset: int = 0,
    ) -> FileDirectory:
        if path:
            try:
                OutputSelection.relative_file(path)
            except ValueError as error:
                raise ApplicationError("invalid_path", "Choose a relative folder path.") from error
        prefix = path + "/" if path else ""
        ordering = {
            "name": "name COLLATE NOCASE,id",
            "newest": "created DESC,name COLLATE NOCASE,id",
            "type": "media,name COLLATE NOCASE,id",
        }[sort]
        with self.store.connection() as db:
            Workspaces.read(db, workspace)
            # SQL groups immediate children before limiting. A large folder never needs
            # loading all file records or paths into application/browser memory.
            rows = db.execute(
                "WITH paths AS (SELECT id,data,json_extract(data,'$.name') path FROM output_files "
                "WHERE workspace_id=? AND (? IS NULL OR json_extract(data,'$.task_key')=?)), "
                "children AS (SELECT *,substr(path,?) rest FROM paths WHERE substr(path,1,?)=?), "
                "folders AS (SELECT DISTINCT substr(rest,1,instr(rest,'/')-1) name FROM children "
                "WHERE instr(rest,'/')>0), "
                "entries AS (SELECT 'folder:'||name id,name,'folder' kind,NULL data, "
                "'' created,'' media FROM folders UNION ALL SELECT id,rest,'file', "
                "data,json_extract(data,'$.created_at'),json_extract(data,'$.media_type') "
                "FROM children WHERE instr(rest,'/')=0) "
                f"SELECT * FROM entries ORDER BY kind DESC,{ordering} LIMIT 51 OFFSET ?",
                (workspace, task_key, task_key, len(prefix) + 1, len(prefix), prefix, offset),
            ).fetchall()
            items: list[FileEntry] = []
            size = 0
            for row in rows[:50]:
                file = (
                    Outputs._present(db, OutputFile.model_validate_json(row["data"]))
                    if row["data"]
                    else None
                )
                entry = FileEntry(
                    id=row["id"],
                    name=row["name"],
                    path=prefix + row["name"],
                    kind=row["kind"],
                    file=file,
                )
                size += len(entry.model_dump_json().encode())
                if items and size > 30000:
                    break
                items.append(entry)
            return FileDirectory(
                items=items,
                next_offset=offset + len(items) if len(rows) > len(items) else None,
            )
