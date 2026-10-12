"""Stable workspace document references and append-only Markdown revisions."""

import sqlite3
from typing import Any
from uuid import uuid4

from pydantic import Field, computed_field

from flowfield.actors import Actor, actor
from flowfield.application import Edit, Input, Markdown, Workspace, now
from flowfield.errors import ApplicationError
from flowfield.project_config import Identifier, Title
from flowfield.reads import page, validate_page
from flowfield.workspaces import Workspaces


class DocumentCreate(Input):
    title: Title
    body: Markdown = ""
    author: Title = "human"


class DocumentEdit(Edit):
    title: Title | None = None
    body: Markdown | None = None


class DocumentRestore(Edit):
    revision: int = Field(ge=1)


class DocumentHeader(Input):
    workspace_id: Identifier
    id: Identifier
    revision: int
    title: Title
    created_at: str
    author: str
    restored_from: int | None = None
    characters: int
    deleted_at: str | None = None
    deleted_by: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def actor(self) -> Actor:
        return actor(self.author)


class Document(DocumentHeader):
    body: str

    def header(self) -> DocumentHeader:
        return DocumentHeader(**self.model_dump(exclude={"body", "actor"}))


class DocumentPage(Input):
    items: list[DocumentHeader]
    next_cursor: str | int | None


class Documents:
    def __init__(self, store: Workspace):
        self.store = store

    @staticmethod
    def seed(db: sqlite3.Connection, workspace_id: str, author: str, timestamp: str) -> None:
        db.execute(
            "INSERT INTO workspace_documents (workspace_id,id,revision) VALUES (?,'guidance',1)",
            (workspace_id,),
        )
        db.execute(
            "INSERT INTO workspace_document_revisions VALUES "
            "(?,'guidance',1,'Workspace guidance','',?,?,NULL)",
            (workspace_id, timestamp, author),
        )

    @staticmethod
    def read(
        db: sqlite3.Connection, workspace_id: str, identity: str, revision: int | None = None
    ) -> Document:
        row = db.execute(
            "SELECT r.*,d.deleted_at,d.deleted_by,length(r.body) characters "
            "FROM workspace_document_revisions r "
            "JOIN workspace_documents d USING(workspace_id,id) "
            "WHERE r.workspace_id=? AND r.id=? AND r.revision=coalesce(?,d.revision)",
            (workspace_id, identity, revision),
        ).fetchone()
        if row is None:
            raise ApplicationError(
                "not_found", "Document revision not found in this workspace.", 404
            )
        return Document(**dict(row))

    def get(self, workspace_id: str, identity: str, revision: int | None = None) -> Document:
        with self.store.connection() as db:
            return self.read(db, workspace_id, identity, revision)

    def page(self, workspace_id: str, *, after: str | None = None, limit: int = 20) -> DocumentPage:
        validate_page(limit, after)
        with self.store.connection() as db:
            Workspaces.read(db, workspace_id)
            rows = db.execute(
                "SELECT r.workspace_id,r.id,r.revision,r.title,r.created_at,r.author,"
                "r.restored_from,length(r.body) characters FROM workspace_documents d "
                "JOIN workspace_document_revisions r USING(workspace_id,id,revision) "
                "WHERE d.workspace_id=? AND d.deleted_at IS NULL AND "
                "(? IS NULL OR (d.id!='guidance' AND "
                "(?='guidance' OR d.id>?))) ORDER BY d.id!='guidance',d.id LIMIT ?",
                (workspace_id, after, after, after, limit + 1),
            )
            return DocumentPage(**page([dict(row) for row in rows], limit, "id"))

    def history(
        self, workspace_id: str, identity: str, *, before: int | None = None, limit: int = 20
    ) -> DocumentPage:
        validate_page(limit, before)
        with self.store.connection() as db:
            current = self.read(db, workspace_id, identity)
            rows = db.execute(
                "SELECT workspace_id,id,revision,title,created_at,author,restored_from,"
                "length(body) characters FROM workspace_document_revisions "
                "WHERE workspace_id=? AND id=? AND revision<? ORDER BY revision DESC LIMIT ?",
                (workspace_id, identity, before or current.revision + 1, limit + 1),
            )
            return DocumentPage(**page([dict(row) for row in rows], limit, "revision"))

    def text(
        self, workspace_id: str, identity: str, revision: int, *, offset: int = 0, limit: int = 4000
    ) -> dict[str, Any]:
        if revision < 1 or offset < 0 or not 1 <= limit <= 4000:
            raise ApplicationError(
                "invalid_request", "Use a positive revision and a 1–4000 character page."
            )
        value = self.get(workspace_id, identity, revision)
        if offset > value.characters:
            raise ApplicationError("invalid_offset", "Choose an offset within the document.")
        end = offset + limit
        return {
            **value.header().model_dump(),
            "text": value.body[offset:end],
            "next_offset": end if end < value.characters else None,
        }

    @staticmethod
    def save(db: sqlite3.Connection, value: Document) -> Document:
        db.execute(
            "INSERT INTO workspace_document_revisions VALUES (?,?,?,?,?,?,?,?)",
            (
                value.workspace_id,
                value.id,
                value.revision,
                value.title,
                value.body,
                value.created_at,
                value.author,
                value.restored_from,
            ),
        )
        db.execute(
            "UPDATE workspace_documents SET revision=? WHERE workspace_id=? AND id=?",
            (value.revision, value.workspace_id, value.id),
        )
        return value

    def create(self, workspace_id: str, request: DocumentCreate) -> Document:
        with self.store.connection(write=True) as db:
            Workspaces.read(db, workspace_id)
            if (
                db.execute(
                    "SELECT count(*) FROM workspace_documents WHERE workspace_id=? "
                    "AND deleted_at IS NULL",
                    (workspace_id,),
                ).fetchone()[0]
                >= 100
            ):
                raise ApplicationError(
                    "document_limit", "A workspace can have up to 100 documents.", 409
                )
            value = Document(
                **request.model_dump(),
                workspace_id=workspace_id,
                id=uuid4().hex,
                revision=1,
                created_at=now(),
                characters=len(request.body),
            )
            db.execute(
                "INSERT INTO workspace_documents (workspace_id,id,revision) VALUES (?,?,1)",
                (workspace_id, value.id),
            )
            return self.save(db, value)

    def edit(self, workspace_id: str, identity: str, request: DocumentEdit) -> Document:
        with self.store.connection(write=True) as db:
            current = self.read(db, workspace_id, identity)
            self.store._current(current.revision, request.expected_revision)
            if current.deleted_at:
                raise ApplicationError(
                    "document_deleted", "Restore this document before editing it.", 409
                )
            title = request.title if request.title is not None else current.title
            body = request.body if request.body is not None else current.body
            if title == current.title and body == current.body:
                return current
            return self.save(
                db,
                Document(
                    workspace_id=workspace_id,
                    id=identity,
                    revision=current.revision + 1,
                    title=title,
                    body=body,
                    characters=len(body),
                    created_at=now(),
                    author=request.author,
                ),
            )

    def restore(self, workspace_id: str, identity: str, request: DocumentRestore) -> Document:
        with self.store.connection(write=True) as db:
            current = self.read(db, workspace_id, identity)
            self.store._current(current.revision, request.expected_revision)
            source = self.read(db, workspace_id, identity, request.revision)
            db.execute(
                "UPDATE workspace_documents SET deleted_at=NULL,deleted_by=NULL "
                "WHERE workspace_id=? AND id=?",
                (workspace_id, identity),
            )
            return self.save(
                db,
                source.model_copy(
                    update={
                        "revision": current.revision + 1,
                        "created_at": now(),
                        "author": request.author,
                        "restored_from": source.revision,
                        "deleted_at": None,
                        "deleted_by": None,
                    }
                ),
            )

    def delete(self, workspace_id: str, identity: str, request: Edit) -> Document:
        with self.store.connection(write=True) as db:
            current = self.read(db, workspace_id, identity)
            self.store._current(current.revision, request.expected_revision)
            if identity == "guidance":
                raise ApplicationError(
                    "main_guidance",
                    "Workspace guidance can be edited or cleared, but not deleted.",
                    409,
                )
            if current.deleted_at:
                return current
            defaults = db.execute(
                "SELECT p.id FROM projects p JOIN workspace_projects w ON w.project_id=p.id, "
                "json_each(p.worker_documents) ref WHERE w.workspace_id=? "
                "AND json_extract(ref.value,'$.id')=? LIMIT 1",
                (workspace_id, identity),
            ).fetchone()
            tasks = db.execute(
                "SELECT t.id FROM tasks t JOIN workspace_projects w ON w.project_id=t.project_id, "
                "json_each(t.data,'$.documents') ref WHERE w.workspace_id=? "
                "AND json_extract(ref.value,'$.id')=? AND json_extract(t.data,'$.archived')=0 "
                "AND json_extract(t.data,'$.status')!='done' LIMIT 1",
                (workspace_id, identity),
            ).fetchone()
            if defaults or tasks:
                raise ApplicationError(
                    "document_in_use",
                    "Remove this document from project defaults and unfinished task selections "
                    "before deleting it.",
                    409,
                )
            timestamp = now()
            self.save(
                db,
                current.model_copy(
                    update={
                        "revision": current.revision + 1,
                        "created_at": timestamp,
                        "author": request.author,
                        "restored_from": None,
                    }
                ),
            )
            db.execute(
                "UPDATE workspace_documents SET deleted_at=?,deleted_by=? "
                "WHERE workspace_id=? AND id=?",
                (timestamp, request.author, workspace_id, identity),
            )
            return self.read(db, workspace_id, identity)
