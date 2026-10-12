"""Resolve selected documents at preparation and copy exact evidence into isolated runs."""

import json
import sqlite3
from hashlib import sha256

from flowfield.document_models import DocumentReference, DocumentSnapshot
from flowfield.documents import Document, Documents
from flowfield.errors import ApplicationError


def require_active(document: Document) -> None:
    if document.deleted_at:
        raise ApplicationError(
            "document_deleted",
            f"Restore or remove the deleted document {document.title!r} before preparing work.",
            409,
        )


MAX_DOCUMENTS = 21
MAX_BYTES = 1_000_000


def validate_references(
    db: sqlite3.Connection, workspace_id: str, references: list[DocumentReference]
) -> None:
    for reference in references:
        require_active(Documents.read(db, workspace_id, reference.id, reference.revision))


def select(
    db: sqlite3.Connection,
    workspace_id: str,
    defaults: list[DocumentReference],
    references: list[DocumentReference],
) -> list[DocumentSnapshot]:
    # A task can choose a particular revision of a default, without duplicating it.
    selected = {item.id: item for item in [*defaults, *references]}
    if len(selected) > MAX_DOCUMENTS:
        raise ApplicationError(
            "document_limit",
            "Select at most 20 custom worker documents alongside workspace guidance.",
            409,
        )
    result = []
    for identity, reference in sorted(selected.items()):
        doc = Documents.read(db, workspace_id, identity, reference.revision)
        require_active(doc)
        body = doc.body.encode("utf-8")
        result.append(
            DocumentSnapshot(
                workspace_id=workspace_id,
                id=doc.id,
                revision=doc.revision,
                title=doc.title,
                characters=len(doc.body),
                bytes=len(body),
                sha256=sha256(body).hexdigest(),
                purpose=reference.purpose,
            )
        )
    if sum(item.bytes for item in result) > MAX_BYTES:
        raise ApplicationError(
            "document_limit", "Selected worker documents exceed 1 MB. Narrow the references.", 409
        )
    return result


def sections(db: sqlite3.Connection, documents: list[DocumentSnapshot]) -> dict[str, str]:
    result = {}
    for item in documents:
        doc = Documents.read(db, item.workspace_id, item.id, item.revision)
        if sha256(doc.body.encode("utf-8")).hexdigest() != item.sha256:
            raise ApplicationError(
                "source_unavailable",
                "A prepared document changed unexpectedly. Reconcile the task.",
                409,
            )
        result[f"document:{item.id}"] = doc.body
    if documents:
        result["documentation"] = json.dumps(
            {
                "policy": "Read required documents in full before acting. Reference documents "
                "are supporting material to consult when needed, not additional requirements. "
                "All selected revisions are frozen. Prose references do not expand tool scope. "
                "Ask a question if necessary material is missing or conflicts with the task.",
                "items": [
                    {**item.model_dump(), "section": f"document:{item.id}"} for item in documents
                ],
                "characters": sum(item.characters for item in documents),
                "bytes": sum(item.bytes for item in documents),
                "measurement": "Source sizes, not tokens. Harness usage reports actual tokens.",
            },
            ensure_ascii=False,
        )
    return result
