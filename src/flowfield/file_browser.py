"""Bounded directory views over retained files, with independent organization."""

from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.output_models import OutputFile, OutputSelection
from flowfield.outputs import Outputs
from flowfield.workspaces import Workspaces


class FileLocation(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return OutputSelection.relative_file(value)


class FileFolder(BaseModel):
    path: str = Field(min_length=1, max_length=500)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return OutputSelection.relative_file(value)


class FileEntry(BaseModel):
    id: str
    name: str
    path: str
    kind: Literal["folder", "file"]
    revision: int = 1
    file: OutputFile | None = None


class FileDirectory(BaseModel):
    items: list[FileEntry]
    next_offset: int | None = None


class FileWork(BaseModel):
    id: str
    title: str
    task_key: str | None = None
    milestone_id: str | None = None


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
                "json_extract(data,'$.task_key') task_key, NULL milestone_id, "
                "json_extract(data,'$.task_title') title FROM files GROUP BY task_key "
                "UNION ALL SELECT 'milestone:'||json_extract(data,'$.milestone_id'), NULL, "
                "json_extract(data,'$.milestone_id') milestone_id, "
                "json_extract(data,'$.milestone_title') FROM files "
                "WHERE milestone_id IS NOT NULL GROUP BY milestone_id "
                "ORDER BY id LIMIT 51 OFFSET ?",
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
        milestone_id: str | None = None,
        sort: Literal["name", "newest", "type"] = "name",
        offset: int = 0,
    ) -> FileDirectory:
        if path:
            try:
                FileFolder(path=path)
            except ValidationError as error:
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
                "WITH paths AS (SELECT f.id,f.data,COALESCE(l.path,json_extract(f.data,'$.name')) "
                "path,COALESCE(l.revision,1) revision FROM output_files f "
                "LEFT JOIN output_locations l ON l.file_id=f.id WHERE f.workspace_id=? "
                "AND (? IS NULL OR json_extract(f.data,'$.task_key')=?) "
                "AND (? IS NULL OR json_extract(f.data,'$.milestone_id')=?)), "
                "children AS (SELECT *,substr(path,?) rest FROM paths WHERE substr(path,1,?)=?), "
                "folders AS (SELECT substr(rest,1,instr(rest,'/')-1) name FROM children "
                "WHERE instr(rest,'/')>0 UNION SELECT "
                "CASE WHEN instr(substr(path,?),'/')>0 THEN "
                "substr(substr(path,?),1,instr(substr(path,?),'/')-1) ELSE substr(path,?) END "
                "FROM output_folders WHERE workspace_id=? AND substr(path,1,?)=?), "
                "entries AS (SELECT 'folder:'||name id,name,'folder' kind,NULL data,1 revision, "
                "'' created,'' media FROM folders WHERE name!='' UNION ALL SELECT id,rest,'file', "
                "data,revision,json_extract(data,'$.created_at'),json_extract(data,'$.media_type') "
                "FROM children WHERE instr(rest,'/')=0) "
                f"SELECT * FROM entries ORDER BY kind DESC,{ordering} LIMIT 51 OFFSET ?",
                (
                    workspace,
                    task_key,
                    task_key,
                    milestone_id,
                    milestone_id,
                    len(prefix) + 1,
                    len(prefix),
                    prefix,
                    len(prefix) + 1,
                    len(prefix) + 1,
                    len(prefix) + 1,
                    len(prefix) + 1,
                    workspace,
                    len(prefix),
                    prefix,
                    offset,
                ),
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
                    revision=row["revision"],
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

    def folder(self, workspace: str, value: FileFolder) -> FileFolder:
        with self.store.connection(write=True) as db:
            Workspaces.read(db, workspace)
            db.execute(
                "INSERT OR IGNORE INTO output_folders(workspace_id,path) VALUES (?,?)",
                (workspace, value.path),
            )
        return value

    def move(self, workspace: str, identity: str, value: FileLocation) -> FileEntry:
        with self.store.connection(write=True) as db:
            row = db.execute(
                "SELECT data FROM output_files WHERE workspace_id=? AND id=?", (workspace, identity)
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "File not found in this workspace.", 404)
            current = db.execute(
                "SELECT revision FROM output_locations WHERE file_id=?", (identity,)
            ).fetchone()
            revision = current[0] if current else 1
            self.store._current(revision, value.expected_revision)
            db.execute(
                "INSERT INTO output_locations(file_id,path,revision) VALUES (?,?,?) "
                "ON CONFLICT(file_id) DO UPDATE SET path=excluded.path,revision=excluded.revision",
                (identity, value.path, revision + 1),
            )
            return FileEntry(
                id=identity,
                name=value.path.rsplit("/", 1)[-1],
                path=value.path,
                kind="file",
                revision=revision + 1,
                file=Outputs._present(db, OutputFile.model_validate_json(row[0])),
            )
