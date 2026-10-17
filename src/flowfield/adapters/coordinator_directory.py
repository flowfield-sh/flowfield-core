"""Host-local coordinator homes, independent of workspace names and repository paths."""

from pathlib import Path

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.workspaces import Workspaces


def prepare(store: Workspace, workspace_id: str) -> Path:
    identity = Workspaces(store).get(workspace_id).id
    directory = store.directory
    try:
        for name in ("coordinators", identity):
            directory = directory / name
            if directory.is_symlink():
                raise OSError("The managed coordinator directory cannot be a symbolic link")
            directory.mkdir(mode=0o700, exist_ok=True)
        return directory
    except OSError as error:
        raise ApplicationError(
            "coordinator_directory_unavailable",
            "Flowfield could not prepare the workspace coordinator directory. "
            "Check the service's data-directory permissions and available storage.",
            409,
        ) from error


def for_scope(store: Workspace, identity: str) -> Path:
    return prepare(store, identity)


def repositories_for_scope(store: Workspace, identity: str) -> tuple[Path, ...]:
    return tuple(sorted({Path(project.path) for project in Workspaces(store).projects(identity)}))
