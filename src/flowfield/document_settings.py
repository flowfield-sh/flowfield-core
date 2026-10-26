"""One revision-checked transaction for the human document settings form."""

from flowfield.application import Workspace
from flowfield.document_usage import DocumentUsage, DocumentUsageEdit, DocumentUsageView
from flowfield.documents import Document, DocumentEdit, Documents


class DocumentSettings(Document):
    usage: DocumentUsageView


class DocumentSettingsEdit(DocumentEdit):
    usage: DocumentUsageEdit | None = None


class DocumentSettingsService:
    def __init__(self, store: Workspace):
        self.store = store

    def get(self, workspace_id: str, identity: str) -> DocumentSettings:
        with self.store.connection() as db:
            return DocumentSettings(
                **Documents.read(db, workspace_id, identity).model_dump(exclude={"actor"}),
                usage=DocumentUsage(self.store)._read(db, workspace_id, identity),
            )

    def edit(
        self, workspace_id: str, identity: str, request: DocumentSettingsEdit
    ) -> DocumentSettings:
        with self.store.connection(write=True, workspace_id=workspace_id) as db:
            document = Documents(self.store)._edit(db, workspace_id, identity, request)
            usage = DocumentUsage(self.store)
            if request.usage is not None:
                usage._edit(
                    db,
                    workspace_id,
                    identity,
                    request.usage.model_copy(update={"author": request.author}),
                )
            return DocumentSettings(
                **document.model_dump(exclude={"actor"}),
                usage=usage._read(db, workspace_id, identity),
            )
