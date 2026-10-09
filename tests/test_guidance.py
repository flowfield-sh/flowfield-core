"""Adoption owns only its unchanged content, including across interruptions."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import flowfield.guidance as guidance
from flowfield.api import create_app
from flowfield.application import ProjectSetup, Workspace
from flowfield.cli import app
from flowfield.errors import ApplicationError
from flowfield.guidance import GUIDE, MANIFEST, Guidance, GuidanceChange, template
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig


def setup(tmp_path: Path) -> tuple[Path, Guidance]:
    root = tmp_path / "project"
    root.mkdir()
    workspace = Workspace(tmp_path / "state")
    workspace.setup_project(ProjectSetup(path=str(root)))
    return root, Guidance(workspace)


def change(service: Guidance, action: str = "install"):
    return service.change(
        "project", GuidanceChange(action=action, expected_revision=service.get("project").revision)
    )


def test_install_idempotent_and_remove_preserves_exact_existing_text(tmp_path: Path) -> None:
    root, service = setup(tmp_path)
    original = b"# Local rules\r\n\r\nKeep this exactly.\r\n"
    (root / "AGENTS.md").write_bytes(original)
    view = service.get("project")
    assert view.status == "available" and not (root / GUIDE).exists()
    installed = change(service)
    assert installed.status == "installed"
    assert set(installed.changed_files) == {"AGENTS.md", GUIDE, MANIFEST}
    assert any("fresh coding conversation" in step for step in installed.next_steps)
    assert (root / "AGENTS.md").read_bytes().startswith(original)
    snapshot = {name: (root / name).read_bytes() for name in ["AGENTS.md", GUIDE, MANIFEST]}
    unchanged = change(service)
    assert unchanged.status == "installed" and unchanged.changed_files == []
    assert snapshot == {name: (root / name).read_bytes() for name in snapshot}
    with pytest.raises(ApplicationError, match="changed"):
        service.change("project", GuidanceChange(action="remove", expected_revision=view.revision))
    change(service, "remove")
    assert (root / "AGENTS.md").read_bytes() == original
    assert not (root / GUIDE).exists() and not (root / MANIFEST).exists()
    assert (root / ".flowfield/config.toml").exists()


def test_remove_preserves_local_modifications_and_references(tmp_path: Path) -> None:
    root, service = setup(tmp_path)
    change(service)
    section = (root / "AGENTS.md").read_text().replace("Working with", "Local work with")
    (root / "AGENTS.md").write_text(section)
    assert service.get("project").status == "conflict"
    with pytest.raises(ApplicationError, match="preserved"):
        change(service)
    change(service, "remove")
    assert (root / "AGENTS.md").read_text() == section
    assert (root / GUIDE).exists()
    # Restore only the owned section. Local guide edits still survive removal.
    (root / "AGENTS.md").write_text(template("agents-section.md"))
    (root / GUIDE).write_text("My local playbook\n")
    change(service, "remove")
    assert not (root / "AGENTS.md").exists()
    assert (root / GUIDE).read_text() == "My local playbook\n"


def test_manual_content_is_never_claimed_or_removed(tmp_path: Path) -> None:
    root, service = setup(tmp_path)
    (root / "AGENTS.md").write_text(template("agents-section.md"))
    assert any("missing" in notice for notice in service.get("project").notices)
    change(service)
    change(service, "remove")
    assert (root / "AGENTS.md").read_text() == template("agents-section.md")
    assert (root / GUIDE).exists()  # Still referenced by unowned instructions.
    (root / MANIFEST).unlink()
    assert service.get("project").status == "manual"
    change(service, "remove")
    assert (root / GUIDE).exists()


def test_update_and_interrupted_install_can_resume(tmp_path: Path, monkeypatch) -> None:
    root, service = setup(tmp_path)
    real_write = guidance.write

    def interrupted(path, before, after):
        if path.name == "AGENTS.md":
            raise OSError("interrupted")
        real_write(path, before, after)

    with monkeypatch.context() as patch:
        patch.setattr(guidance, "write", interrupted)
        with pytest.raises(ApplicationError, match="interrupted"):
            change(service)
    service = Guidance(Workspace(service.workspace.directory))
    assert service.get("project").status == "partial"
    change(service)
    old_template = guidance.template
    monkeypatch.setattr(
        guidance,
        "template",
        lambda name: (
            old_template(name)
            .replace("Working with Flowfield", "Working with Flowfield (updated)")
            .replace("# Coordinating with Flowfield", "# Coordinating with Flowfield (updated)")
        ),
    )
    assert service.get("project").status == "update_available"
    assert change(service).status == "installed"
    assert "(updated)" in (root / GUIDE).read_text()
    change(service, "remove")
    assert not (root / "AGENTS.md").exists()


def test_overrides_and_symlinks_are_visible_and_preserved(tmp_path: Path) -> None:
    root, service = setup(tmp_path)
    nested = root / "src"
    nested.mkdir()
    (nested / "AGENTS.override.md").write_text("Nested rules")
    (root / "AGENTS.override.md").write_text("Root override")
    view = service.get("project")
    assert view.instruction_files == ["AGENTS.override.md", "src/AGENTS.override.md"]
    assert any("override" in n for n in view.notices)
    outside = tmp_path / "outside"
    outside.write_text("Never change")
    (root / "AGENTS.md").symlink_to(outside)
    with pytest.raises(ApplicationError, match="regular file"):
        change(service)
    assert outside.read_text() == "Never change"


def test_baseline_uses_destination_and_never_commits(tmp_path: Path) -> None:
    root, service = setup(tmp_path)

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args]
        )

    git("init", "-b", "main")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base")
    git("branch", "delivery")
    before = git("rev-parse", "HEAD")
    change(service)
    assert service.get("project").baseline == "not_committed"
    assert git("rev-parse", "HEAD") == before
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "guidance")
    assert service.get("project").baseline == "committed"
    integrations = Integrations(service.workspace)
    integrations.configure(
        "project",
        IntegrationConfig(
            runtime="local", expected_revision=1, target_branch="delivery", checks=["true"]
        ),
    )
    assert service.get("project").baseline == "not_committed"
    assert service.get("project").baseline_ref == "refs/heads/delivery"


def test_committed_guidance_still_reports_untracked_adoption_metadata(tmp_path: Path) -> None:
    root, service = setup(tmp_path)

    def run_git(*args):
        return subprocess.check_output(
            ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args]
        )

    run_git("init", "-b", "main")
    change(service)
    run_git("add", "AGENTS.md", GUIDE)
    run_git(
        "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "guide"
    )
    before = run_git("status", "--porcelain")
    view = service.get("project")
    assert view.baseline == "committed"
    notice = next(n for n in view.notices if n.startswith("Untracked adoption files:"))
    assert ".flowfield/config.toml" in notice and MANIFEST in notice
    assert "blocks code delivery" in notice
    assert run_git("status", "--porcelain") == before

    # Both supported human choices remove only the metadata notice.
    run_git("add", ".flowfield/config.toml")
    run_git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "id")
    notice = next(n for n in service.get("project").notices if n.startswith("Untracked adoption"))
    assert ".flowfield/config.toml" not in notice and MANIFEST in notice
    with (root / ".git/info/exclude").open("a") as stream:
        stream.write(f"\n/{MANIFEST}\n")
    (root / "human-note.txt").write_text("Keep unrelated human work")
    assert not any(n.startswith("Untracked adoption") for n in service.get("project").notices)
    assert (root / MANIFEST).exists()
    assert run_git("status", "--porcelain") == b"?? human-note.txt\n"


def test_http_preview_stale_writes_and_export(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        client.post("/api/projects/initialize", json={"path": str(root)})
        endpoint = "/api/projects/project/guidance"
        preview = client.get(endpoint).json()
        payload = {"action": "install", "expected_revision": preview["revision"]}
        assert client.post(endpoint, json=payload).json()["status"] == "installed"
        assert client.post(endpoint, json=payload).status_code == 409
        assert client.get(endpoint).json()["skill"] == template("flowfield-coordinator/SKILL.md")
    output = CliRunner().invoke(app, ["project", "guidance", "export", "--part", "agents"])
    assert output.exit_code == 0 and output.stdout == template("agents-section.md")
    assert CliRunner().invoke(app, ["project", "create", str(root)]).exit_code != 0


def test_local_write_between_preview_and_mutation_is_preserved(tmp_path: Path, monkeypatch) -> None:
    root, service = setup(tmp_path)
    preview = service.get("project")
    real_load = service._load
    calls = 0

    def racing_load(project_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / GUIDE).parent.mkdir(parents=True, exist_ok=True)
            (root / GUIDE).write_text("Local guide arrived after preview\n")
        return real_load(project_id)

    monkeypatch.setattr(service, "_load", racing_load)
    with pytest.raises(ApplicationError, match="changed"):
        service.change(
            "project", GuidanceChange(action="install", expected_revision=preview.revision)
        )
    assert (root / GUIDE).read_text() == "Local guide arrived after preview\n"
    assert not (root / MANIFEST).exists()
    assert not (root / "AGENTS.md").exists()


def test_skill_parent_symlink_is_not_followed(tmp_path):
    root, service = setup(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".agents").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ApplicationError, match="symlinked"):
        change(service)
    assert list(outside.iterdir()) == []


def test_install_and_remove_leave_unmanaged_guidance_untouched(tmp_path):
    root, service = setup(tmp_path)
    unmanaged = root / ".flowfield/coordinator.md"
    unmanaged.write_text("Project-owned instructions\n")
    change(service)
    change(service, "remove")
    assert unmanaged.read_text() == "Project-owned instructions\n"
