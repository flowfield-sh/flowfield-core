"""The human document form saves content and applicability as one transaction."""

import pytest
from execution_fixtures import fixture

from flowfield.application import ProjectEdit
from flowfield.document_settings import DocumentSettingsEdit, DocumentSettingsService
from flowfield.document_usage import DocumentUsage, DocumentUsageEdit
from flowfield.documents import DocumentCreate, DocumentEdit, Documents
from flowfield.errors import ApplicationError


def test_document_settings_save_content_and_usage_together(tmp_path):
    store = fixture(tmp_path).workspace
    docs = Documents(store)
    doc = docs.create("harbor", DocumentCreate(title="Original", body="Original content"))
    settings = DocumentSettingsService(store)
    before = settings.get("harbor", doc.id)
    saved = settings.edit(
        "harbor",
        doc.id,
        DocumentSettingsEdit(
            expected_revision=before.revision,
            title="Revised",
            body="Revised content",
            usage={
                "expected_revision": before.usage.revision,
                "projectless": "required",
                "projects": [
                    {
                        "id": "harbor",
                        "expected_revision": store.project("harbor").revision,
                        "purpose": "reference",
                    }
                ],
            },
        ),
    )
    assert saved == settings.get("harbor", doc.id)
    assert (saved.title, saved.body, saved.revision) == ("Revised", "Revised content", 2)
    assert saved.usage.projectless == "required"
    assert next(p for p in saved.usage.projects if p.id == "harbor").purpose == "reference"
    assert (
        next(ref for ref in store.project("harbor").worker_documents if ref.id == doc.id).purpose
        == "reference"
    )
    usage_only = settings.edit(
        "harbor",
        doc.id,
        DocumentSettingsEdit(
            expected_revision=2,
            usage={"expected_revision": saved.usage.revision, "projectless": "reference"},
        ),
    )
    assert usage_only.revision == 2
    assert usage_only.usage.projectless == "reference"


@pytest.mark.parametrize("changed", ["document", "usage", "project"])
def test_document_settings_conflict_rolls_back_the_entire_form(tmp_path, changed):
    store = fixture(tmp_path).workspace
    docs = Documents(store)
    doc = docs.create("harbor", DocumentCreate(title="Original", body="Original content"))
    settings = DocumentSettingsService(store)
    initial = settings.get("harbor", doc.id)
    project = store.project("harbor")
    if changed == "document":
        docs.edit("harbor", doc.id, DocumentEdit(expected_revision=1, body="External content"))
    elif changed == "usage":
        DocumentUsage(store).edit(
            "harbor", doc.id, DocumentUsageEdit(expected_revision=1, projectless="reference")
        )
    else:
        store.edit_project(
            "harbor",
            ProjectEdit(expected_revision=project.revision, description="External settings"),
        )
    before = settings.get("harbor", doc.id)
    project_before = store.project("harbor")
    with pytest.raises(ApplicationError, match="revision"):
        settings.edit(
            "harbor",
            doc.id,
            DocumentSettingsEdit(
                expected_revision=initial.revision,
                title="Unsaved title",
                body="Unsaved content",
                usage={
                    "expected_revision": initial.usage.revision,
                    "projectless": "required",
                    "projects": [
                        {
                            "id": "harbor",
                            "expected_revision": project.revision,
                            "purpose": "required",
                        }
                    ],
                },
            ),
        )
    assert settings.get("harbor", doc.id) == before
    assert store.project("harbor") == project_before
    assert len(docs.history("harbor", doc.id).items) == before.revision


def test_main_guidance_cannot_be_excluded_by_the_combined_form(tmp_path):
    store = fixture(tmp_path).workspace
    settings = DocumentSettingsService(store)
    before = settings.get("harbor", "guidance")
    with pytest.raises(ApplicationError, match="all tasks"):
        settings.edit(
            "harbor",
            "guidance",
            DocumentSettingsEdit(
                expected_revision=before.revision,
                body="Unsaved",
                usage={"expected_revision": 1, "projectless": None},
            ),
        )
    assert settings.get("harbor", "guidance") == before
