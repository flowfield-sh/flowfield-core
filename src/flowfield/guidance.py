"""Project-local static guidance, with narrow ownership and preservation of local edits."""

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from flowfield.adapters.git_workspace import git
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.integration import Integrations
from flowfield.project_config import read_config

BEGIN = "<!-- flowfield:begin -->"
END = "<!-- flowfield:end -->"
GUIDE = ".agents/skills/flowfield-coordinator/SKILL.md"
SKILL_TEMPLATE = "flowfield-coordinator/SKILL.md"
MANIFEST = ".flowfield/guidance.json"
LIMIT = 256_000


def template(name: str) -> str:
    return files("flowfield").joinpath("templates", name).read_text(encoding="utf-8")


def digest(value: str | None) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True).encode()).hexdigest()


class Ownership(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: Literal[2] = 2
    section_hashes: list[str] = Field(default_factory=list, max_length=2)
    skill_hashes: list[str] = Field(default_factory=list, max_length=2)
    separator: Literal["", "\n\n"] = ""
    created_agents: bool = False


class GuidanceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(min_length=64, max_length=64)
    action: Literal["install", "remove"]


class GuidanceTemplates(BaseModel):
    section: str
    skill: str


class GuidanceView(GuidanceTemplates):
    project_id: str
    revision: str
    status: Literal["available", "installed", "manual", "update_available", "partial", "conflict"]
    can_install: bool
    can_remove: bool
    notices: list[str]
    instruction_files: list[str]
    baseline: Literal["committed", "not_committed", "unavailable"]
    baseline_ref: str
    message: str = ""
    changed_files: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


def read(path: Path) -> str | None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ApplicationError(
            "guidance_conflict", f"Preserve {path}: expected a regular file.", 409
        )
    try:
        with path.open("rb") as stream:
            data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError("File too large")
        return data.decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as error:
        raise ApplicationError(
            "guidance_conflict", f"Cannot safely read {path}; preserve and inspect it.", 409
        ) from error


def section_range(text: str) -> tuple[int, int] | None:
    if BEGIN not in text and END not in text:
        return None
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(END) < text.index(BEGIN):
        raise ApplicationError(
            "guidance_conflict",
            "AGENTS.md has ambiguous Flowfield markers; preserve and edit manually.",
            409,
        )
    start, end = text.index(BEGIN), text.index(END) + len(END)
    if text[end : end + 2] == "\r\n":
        end += 2
    elif text[end : end + 1] == "\n":
        end += 1
    return start, end


def write(path: Path, before: str | None, after: str | None) -> None:
    if before == after:
        return
    if read(path) != before:
        raise ApplicationError(
            "guidance_changed", "Guidance changed; refresh before continuing.", 409
        )
    if after is None:
        path.unlink(missing_ok=True)
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(after.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if before is not None:
            temporary.chmod(path.stat().st_mode & 0o777)
        else:
            temporary.chmod(0o644)
        if read(path) != before:
            raise ApplicationError(
                "guidance_changed", "Guidance changed; refresh before continuing.", 409
            )
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


class Guidance:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _load(self, project_id: str) -> tuple[Path, str | None, str | None, str | None, Ownership]:
        root = Path(self.workspace.project(project_id).path)
        config = read_config(root)
        if config is None or config.project_id != project_id:
            raise ApplicationError(
                "guidance_conflict", "Restore the registered project config first.", 409
            )
        for name in (GUIDE, MANIFEST):
            for parent in (root / name).relative_to(root).parents:
                if (root / parent).is_symlink():
                    raise ApplicationError(
                        "guidance_conflict", f"Preserve symlinked {parent}.", 409
                    )
        agents, guide, raw = (read(root / name) for name in ("AGENTS.md", GUIDE, MANIFEST))
        try:
            owner = Ownership.model_validate_json(raw) if raw else Ownership()
        except ValidationError as error:
            raise ApplicationError(
                "guidance_conflict", "Preserve and inspect .flowfield/guidance.json.", 409
            ) from error
        return root, agents, guide, raw, owner

    @staticmethod
    def _instruction_files(root: Path) -> tuple[list[str], bool]:
        found, visited = [], 0
        for directory, subdirs, names in os.walk(root, followlinks=False):
            subdirs[:] = sorted(
                name
                for name in subdirs
                if name
                not in {
                    ".git",
                    ".flowfield",
                    "node_modules",
                    ".venv",
                    "venv",
                    "dist",
                    "build",
                    "site",
                }
                and not (Path(directory) / name).is_symlink()
            )
            visited += 1
            for name in ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CLAUDE.local.md"):
                if name in names:
                    found.append(str((Path(directory) / name).relative_to(root)))
            if visited >= 2000 or len(found) >= 100:
                return found[:100], True
        if (root / ".codex/config.toml").exists():
            found.append(".codex/config.toml")
        return found, False

    def get(self, project_id: str) -> GuidanceView:
        root, agents, guide, raw, owner = self._load(project_id)
        wanted_section, wanted_guide = template("agents-section.md"), template(SKILL_TEMPLATE)
        region = section_range(agents or "")
        section = agents[region[0] : region[1]] if agents and region else None
        conflict = bool(
            (
                section is not None
                and section != wanted_section
                and digest(section) not in owner.section_hashes
            )
            or (
                guide is not None
                and guide != wanted_guide
                and digest(guide) not in owner.skill_hashes
            )
        )
        current = section == wanted_section and guide == wanted_guide
        owned = bool(owner.section_hashes or owner.skill_hashes)
        status: Literal[
            "available", "installed", "manual", "update_available", "partial", "conflict"
        ] = (
            "conflict"
            if conflict
            else "installed"
            if current and owned
            else "manual"
            if current
            else "partial"
            if owned and (section is None or guide is None)
            else "update_available"
            if owned
            else "available"
        )
        notices: list[str] = []
        try:
            untracked_metadata = (
                git(
                    root,
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "--",
                    ".flowfield/config.toml",
                    MANIFEST,
                )
                .decode()
                .splitlines()
            )
        except ApplicationError:
            untracked_metadata = []  # A project may not yet have a Git repository.
        if untracked_metadata:
            notices.append(
                "Untracked adoption files: "
                + ", ".join(untracked_metadata)
                + ". Review and commit them, or deliberately ignore them under your "
                "repository's rules. Leaving them untracked blocks code delivery. "
                "Flowfield will not commit, ignore or discard them for you."
            )
        if conflict:
            notices.append(
                "Local guidance differs. Installation will preserve it; "
                "review/copy the templates manually."
            )
        if agents and GUIDE in agents and guide is None:
            notices.append(f"AGENTS.md references the missing {GUIDE} skill.")
        discovered, truncated = self._instruction_files(root)
        if any(name != "AGENTS.md" for name in discovered):
            notices.append(
                "Review additional instruction files: "
                + ", ".join(name for name in discovered if name != "AGENTS.md")
                + ". They may override this guidance."
            )
        if truncated:
            notices.append(
                "Instruction discovery was bounded; additional nested instructions may exist."
            )
        baseline: Literal["committed", "not_committed", "unavailable"] = "unavailable"
        branch = Integrations(self.workspace).settings(project_id).target_branch
        baseline_ref = f"refs/heads/{branch}" if branch else "HEAD"
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", f"{baseline_ref}^{{commit}}"],
                cwd=root,
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                baseline = "committed"
                for name, content in (("AGENTS.md", agents), (GUIDE, guide)):
                    result = subprocess.run(
                        ["git", "show", f"{baseline_ref}:./{name}"],
                        cwd=root,
                        capture_output=True,
                        timeout=5,
                    )
                    if content is None or result.returncode or result.stdout != content.encode():
                        baseline = "not_committed"
        except (OSError, subprocess.TimeoutExpired):
            baseline = "unavailable"
        next_steps = []
        if current:
            if baseline != "committed":
                next_steps.append(
                    f"Review and commit the guidance to {baseline_ref} under your repository's "
                    "rules before running workers."
                )
            next_steps.append(
                "Start a fresh coding conversation; open sessions do not reload guidance. "
                "Ask the harness to read AGENTS.md and the coordinator skill explicitly."
            )
        return GuidanceView(
            project_id=project_id,
            revision=digest(json.dumps([agents, guide, raw])),
            status=status,
            section=wanted_section,
            skill=wanted_guide,
            can_install=not conflict and not current,
            can_remove=owned,
            notices=notices,
            instruction_files=discovered,
            baseline=baseline,
            baseline_ref=baseline_ref,
            next_steps=next_steps,
        )

    def change(self, project_id: str, request: GuidanceChange) -> GuidanceView:
        try:
            return self._change(project_id, request)
        except OSError as error:
            raise ApplicationError(
                "guidance_write_failed",
                "Guidance write was interrupted. Files and ownership evidence are preserved; "
                "check directory permissions, refresh and review before retrying.",
                409,
            ) from error

    def _change(self, project_id: str, request: GuidanceChange) -> GuidanceView:
        # Serialize service writers without putting runtime locks into the user's repository.
        with (self.workspace.directory / f"guidance-{project_id}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            view = self.get(project_id)
            if request.expected_revision != view.revision:
                raise ApplicationError(
                    "guidance_changed",
                    "Guidance changed; refresh and review before continuing.",
                    409,
                )
            root, agents, guide, raw, owner = self._load(project_id)
            if digest(json.dumps([agents, guide, raw])) != request.expected_revision:
                raise ApplicationError(
                    "guidance_changed", "Guidance changed; refresh before continuing.", 409
                )
            region = section_range(agents or "")
            section = agents[region[0] : region[1]] if agents and region else None
            message = "Guidance already matches; no files changed."
            if request.action == "install":
                if view.status == "conflict":
                    raise ApplicationError(
                        "guidance_conflict",
                        "Local instructions were preserved. Review/copy the templates manually.",
                        409,
                    )
                if not view.can_install:
                    return view.model_copy(update={"message": message})
                new_agents = agents or ""
                if section != view.section:
                    if region:
                        new_agents = (
                            new_agents[: region[0]] + view.section + new_agents[region[1] :]
                        )
                    else:
                        owner.separator = "\n\n" if new_agents else ""
                        owner.created_agents = agents is None
                        new_agents += owner.separator + view.section
                    owner.section_hashes = (
                        list(dict.fromkeys([digest(section), digest(view.section)]))
                        if section
                        else [digest(view.section)]
                    )
                if guide != view.skill:
                    owner.skill_hashes = (
                        list(dict.fromkeys([digest(guide), digest(view.skill)]))
                        if guide
                        else [digest(view.skill)]
                    )
                # Publish ownership intent first: an interrupted two-file install can be resumed.
                pending = owner.model_dump_json(indent=2) + "\n"
                write(root / MANIFEST, raw, pending)
                (root / GUIDE).parent.mkdir(parents=True, exist_ok=True)
                write(root / GUIDE, guide, view.skill)
                write(root / "AGENTS.md", agents, new_agents)
                if owner.section_hashes:
                    owner.section_hashes = [digest(view.section)]
                if owner.skill_hashes:
                    owner.skill_hashes = [digest(view.skill)]
                write(root / MANIFEST, pending, owner.model_dump_json(indent=2) + "\n")
                message = "Project guidance installed."
            else:
                preserved = []
                modified_section = (
                    section is not None
                    and bool(owner.section_hashes)
                    and digest(section) not in owner.section_hashes
                )
                if owner.section_hashes and not modified_section:
                    if agents is not None and region:
                        start, end = region
                        if owner.separator and agents[:start].endswith(owner.separator):
                            start -= len(owner.separator)
                        remaining = agents[:start] + agents[end:]
                        write(
                            root / "AGENTS.md",
                            agents,
                            None if not remaining and owner.created_agents else remaining,
                        )
                    owner.section_hashes = []
                elif modified_section:
                    preserved.append("locally modified AGENTS.md section")
                retained_agents = read(root / "AGENTS.md") or ""
                if owner.skill_hashes:
                    if guide is not None and (
                        digest(guide) not in owner.skill_hashes or GUIDE in retained_agents
                    ):
                        preserved.append(f"{GUIDE} (modified or still referenced)")
                    else:
                        write(root / GUIDE, guide, None)
                        owner.skill_hashes = []
                after = (
                    owner.model_dump_json(indent=2) + "\n"
                    if owner.skill_hashes or owner.section_hashes
                    else None
                )
                write(root / MANIFEST, raw, after)
                message = "Unchanged owned guidance removed; project identity retained."
                if preserved:
                    message += " Preserved " + "; ".join(preserved) + "."
            before = dict(
                zip(
                    ("AGENTS.md", GUIDE, MANIFEST),
                    (agents, guide, raw),
                    strict=True,
                )
            )
            changed = [name for name, content in before.items() if read(root / name) != content]
            return self.get(project_id).model_copy(
                update={"message": message, "changed_files": changed}
            )
