"""Local machine preparation for native agents; no tool inventory, sandbox or provisioning.

The caller supplies the intended host environment explicitly (not a login shell
command or a persisted bag of credentials). Each attempt owns its checkout and
scratch directories. Native harness configuration controls access to host resources.
Local uses the service host and isolates ordinary edits in Git worktrees.
"""

import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from flowfield.adapters.git_workspace import GitWorkspace
from flowfield.execution_models import RunLocation


@dataclass(frozen=True)
class LocalAttempt:
    run_id: str
    workspace: GitWorkspace
    runtime: Path
    # May include credentials. Never serialize/log the host process environment.
    _environment: Mapping[str, str] = field(repr=False)

    def launch_environment(self) -> dict[str, str]:
        """Independent copy for launch; retain the selected host's HOME/PATH/config."""
        return dict(self._environment)

    @property
    def root(self) -> Path:
        return self.workspace.root

    @property
    def checkout(self) -> Path:
        return self.workspace.checkout

    @property
    def common_git(self) -> Path:
        return self.workspace.common_git

    def snapshot(self, parent: str) -> tuple[str, list[str]]:
        return self.workspace.snapshot(parent, allow_local_commits=True)

    def location(self, base: str, result: str | None) -> RunLocation:
        prefix = f"git -C {shlex.quote(str(self.checkout))}"
        return RunLocation(
            workspace=str(self.checkout),
            diff_command=f"{prefix} diff --no-ext-diff --no-textconv {base} {result}"
            if result
            else f"{prefix} status --short",
            try_command=f"cd {shlex.quote(str(self.checkout))}",
        )


class LocalHost:
    """One reusable local environment; run state is allocated by prepare().

    This is intentionally a concrete adapter, not a speculative provider interface.
    Ports, databases and global installs are not isolated by a worktree. Project setup
    must use separate resources or the caller must serialize that work.
    """

    def __init__(self, environment: Mapping[str, str]):
        self._environment = dict(environment)

    def prepare(self, directory: Path, repository: Path, run_id: str, commit: str) -> LocalAttempt:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
            raise ValueError("Run identity must be a simple name, not a path")
        workspace = GitWorkspace.prepare(
            directory.resolve() / "local-attempts" / run_id, repository, commit
        )
        runtime = workspace.root / "runtime"
        for name in ("tmp", "output"):
            (runtime / name).mkdir(parents=True)
        return self.restore(run_id, workspace, runtime)

    def restore(self, run_id: str, workspace: GitWorkspace, runtime: Path) -> LocalAttempt:
        """Rebuild launch variables from the current host without persisting credentials."""
        variables = self.launch_environment(workspace.checkout, runtime / "tmp")
        variables.update(
            FLOWFIELD_RUN_ID=run_id,
            FLOWFIELD_WORKSPACE=str(workspace.checkout),
            FLOWFIELD_RUNTIME_DIR=str(runtime),
        )
        return LocalAttempt(run_id, workspace, runtime, MappingProxyType(variables))

    def launch_environment(self, cwd: Path, temporary: Path) -> dict[str, str]:
        # Git location variables from a parent process must not redirect commands to
        # another checkout/index. Keep native Git configuration and credential helpers.
        variables = {
            key: value
            for key, value in self._environment.items()
            if key
            not in {
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_INDEX_FILE",
                "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_NAMESPACE",
            }
        }
        variables.update(
            PWD=str(cwd),
            TMPDIR=str(temporary),
            TMP=str(temporary),
            TEMP=str(temporary),
        )
        return variables
