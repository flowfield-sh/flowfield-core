"""Reusable isolated setup and scripted peers; no collected test cases."""

from pathlib import Path

from project_fixtures import adopt

from flowfield.application import ProjectSetup, Workspace
from flowfield.guidance import Guidance


def setup(tmp_path: Path) -> tuple[Path, Guidance]:
    root = tmp_path / "project"
    root.mkdir()
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(root)))
    return root, Guidance(workspace)
