"""Applicability updates preserve exact assignments and share project concurrency."""

import pytest
from execution_fixtures import fixture
from project_fixtures import task_request
from worker_documents_fixtures import prepare

from flowfield.application import Edit, ProjectEdit, TaskPublish
from flowfield.document_usage import DocumentUsage, DocumentUsageEdit
from flowfield.documents import DocumentCreate, DocumentEdit, Documents
from flowfield.errors import ApplicationError


def test_projectless_defaults_freeze_sources_and_allow_opt_out(tmp_path):
    store = fixture(tmp_path).workspace
    docs = Documents(store)
    doc = docs.create("harbor", DocumentCreate(title="Capture conventions", body="Use 1024px."))
    usage = DocumentUsage(store)
    assert usage.get("harbor", "guidance").projectless == "required"
    assert usage.get("harbor", doc.id).projectless is None
    usage.edit("harbor", doc.id, DocumentUsageEdit(expected_revision=1, projectless="reference"))
    task = store.create_task(
        "harbor",
        task_request(
            id="capture",
            title="Take a screenshot",
            body="Capture the homepage at 1024px and return its image.",
        ),
    )
    task = store.publish_task(
        "harbor", task.id, TaskPublish(expected_revision=task.revision, completion="report")
    )
    frozen = task.publication.documents
    assert {(item.id, item.purpose) for item in frozen} == {
        (doc.id, "reference"),
        ("guidance", "required"),
    }
    docs.edit("harbor", doc.id, DocumentEdit(expected_revision=1, body="Use 1200px."))
    assert store.task("harbor", task.id).publication.documents == frozen
    with pytest.raises(ApplicationError, match="projectless defaults"):
        docs.delete("harbor", doc.id, Edit(expected_revision=2))
    usage.edit("harbor", doc.id, DocumentUsageEdit(expected_revision=2, projectless=None))
    assert store.task("harbor", task.id).publication.documents == frozen
    second = store.create_task(
        "harbor", task_request(id="other", title="Other task", body="Write a short report.")
    )
    second = store.publish_task(
        "harbor", second.id, TaskPublish(expected_revision=second.revision, completion="report")
    )
    assert [item.id for item in second.publication.documents] == ["guidance"]
    with pytest.raises(ApplicationError, match="all tasks"):
        usage.edit("harbor", "guidance", DocumentUsageEdit(expected_revision=1, projectless=None))


def test_project_association_is_shared_atomic_and_preserves_pins(tmp_path):
    store = fixture(tmp_path).workspace
    docs = Documents(store)
    doc = docs.create("harbor", DocumentCreate(title="Architecture"))
    usage = DocumentUsage(store)
    project = store.project("harbor")
    store.edit_project(
        "harbor",
        ProjectEdit(
            expected_revision=project.revision, worker_documents=[{"id": doc.id, "revision": 1}]
        ),
    )
    before = usage.get("harbor", doc.id)
    project = store.project("harbor")
    store.edit_project(
        "harbor", ProjectEdit(expected_revision=project.revision, description="Changed elsewhere")
    )
    with pytest.raises(ApplicationError, match="revision"):
        usage.edit(
            "harbor",
            doc.id,
            DocumentUsageEdit(
                expected_revision=before.revision,
                projectless="required",
                projects=[
                    {"id": "harbor", "expected_revision": project.revision, "purpose": "reference"}
                ],
            ),
        )
    assert usage.get("harbor", doc.id).projectless is None
    project = store.project("harbor")
    usage.edit(
        "harbor",
        doc.id,
        DocumentUsageEdit(
            expected_revision=before.revision,
            projectless="required",
            projects=[
                {"id": "harbor", "expected_revision": project.revision, "purpose": "reference"}
            ],
        ),
    )
    selected = next(ref for ref in store.project("harbor").worker_documents if ref.id == doc.id)
    assert selected.revision == 1 and selected.purpose == "reference"
    task = prepare(store)
    assert (
        next(ref for ref in task.publication.documents if ref.id == doc.id).purpose == "reference"
    )
    with pytest.raises(ApplicationError, match="revision"):
        usage.edit("harbor", doc.id, DocumentUsageEdit(expected_revision=1, projectless=None))
