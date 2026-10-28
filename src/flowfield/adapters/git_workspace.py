"""Git workspace preparation and result capture, independent of runtime and harness."""

import asyncio
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from flowfield.errors import ApplicationError

FETCH_TIMEOUT_SECONDS = 30


async def _fetch(
    command: list[str], repository: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    from flowfield.adapters.local_process import LocalProcess

    owner = await LocalProcess.start(command, cwd=repository.resolve(), env=environment)
    process = owner.process
    assert process.stdin and process.stdout and process.stderr
    process.stdin.close()

    async def drain(stream: asyncio.StreamReader) -> bytes:
        output = bytearray()
        while block := await stream.read(8192):
            output.extend(block[: max(0, 65536 - len(output))])
        return bytes(output)

    readers = [asyncio.create_task(drain(stream)) for stream in (process.stdout, process.stderr)]
    try:
        async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
            await process.wait()
    finally:
        cleaned = await owner.close()
        _, pending = await asyncio.wait(readers, timeout=5)
        if not cleaned or pending:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            raise ApplicationError(
                "git_cleanup_uncertain",
                f"Git transport cleanup could not be confirmed (process {process.pid}). "
                "Check its child processes before retrying.",
                409,
            )
        output, errors = await asyncio.gather(*readers)
    assert process.returncode is not None
    return subprocess.CompletedProcess(command, process.returncode, output, errors)


def git(
    repository: Path,
    *args: str,
    input: bytes | None = None,
    index: Path | None = None,
    allowed_returncodes: tuple[int, ...] = (0,),
    native_config: bool = False,
) -> bytes:
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    if native_config:
        # Fetch uses the host's existing credential helpers and URL/SSH configuration.
        # Local object/checkout operations continue to ignore global Git behavior.
        environment.update(GCM_INTERACTIVE="Never", SSH_ASKPASS_REQUIRE="never")
        if "fetch" in args:
            configured_ssh = (
                git(
                    repository,
                    "config",
                    "--get",
                    "core.sshCommand",
                    native_config=True,
                    allowed_returncodes=(0, 1),
                )
                .decode()
                .strip()
            )
            if os.environ.get("GIT_SSH_COMMAND") or configured_ssh:
                environment["GIT_SSH_COMMAND"] = os.environ.get("GIT_SSH_COMMAND") or configured_ssh
            elif os.environ.get("GIT_SSH"):
                environment["GIT_SSH"] = os.environ["GIT_SSH"]
            else:
                environment["GIT_SSH_COMMAND"] = "ssh -oBatchMode=yes"
    else:
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    if index:
        environment["GIT_INDEX_FILE"] = str(index)
    # Never run repository hooks, fsmonitor, external diff or credential prompts as the service.
    command = [
        "git",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(repository),
        *args,
    ]
    try:
        result = (
            asyncio.run(_fetch(command, repository, environment))
            if native_config and "fetch" in args
            else subprocess.run(
                command, input=input, capture_output=True, timeout=30, env=environment
            )
        )
    except (OSError, TimeoutError, subprocess.TimeoutExpired) as error:
        raise ApplicationError(
            "git_unavailable",
            "Git could not finish. Check the repository; existing work is preserved.",
        ) from error
    if result.returncode not in allowed_returncodes:
        raise ApplicationError(
            "git_failed",
            f"Git {args[0] if args else ''} failed. "
            "Check the repository and its code baseline; existing work is preserved.",
        )
    return result.stdout


def baseline(repository: Path) -> str:
    return git(repository, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()


def contains(repository: Path, ancestor: str, descendant: str) -> bool:
    try:
        git(repository, "merge-base", "--is-ancestor", ancestor, descendant)
        return True
    except ApplicationError:
        return False


@dataclass(frozen=True)
class GitWorkspace:
    root: Path
    checkout: Path
    common_git: Path

    @classmethod
    def prepare(cls, root: Path, repository: Path, commit: str) -> "GitWorkspace":
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=False)
        checkout = root / "worktree"
        git(repository, "worktree", "add", "--detach", str(checkout), commit)
        common = Path(
            git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")
            .decode()
            .strip()
        ).resolve()
        return cls(root, checkout, common)

    def snapshot(self, parent: str, *, allow_local_commits: bool = False) -> tuple[str, list[str]]:
        """Hash bytes without clean filters/hooks; include nonignored untracked code."""
        observed = Path(
            git(self.checkout, "rev-parse", "--path-format=absolute", "--git-common-dir")
            .decode()
            .strip()
        ).resolve()
        head = baseline(self.checkout)
        valid_head = head == parent
        if allow_local_commits:
            detached = not git(
                self.checkout, "symbolic-ref", "-q", "HEAD", allowed_returncodes=(0, 1)
            ).strip()
            valid_head = detached and contains(self.checkout, parent, head)
        if observed != self.common_git or not valid_head:
            raise ApplicationError(
                "result_repository_changed",
                "The worker changed its Git checkout metadata or baseline. "
                "Work is preserved; inspect it before creating a result.",
            )
        paths = set(
            git(
                self.checkout, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
            ).split(b"\0")
        ) - {b""}
        index = self.root / "result.index"
        git(self.checkout, "read-tree", "--empty", index=index)
        entries = []
        for encoded in sorted(paths):
            relative = os.fsdecode(encoded)
            path = self.checkout / relative
            if not path.parent.resolve().is_relative_to(self.checkout):
                raise ApplicationError(
                    "unsafe_result_path",
                    (
                        "A result path traverses a symlink outside the checkout; inspect "
                        "it before submitting."
                    ),
                )
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(mode):
                content, git_mode = os.fsencode(os.readlink(path)), "120000"
            elif stat.S_ISREG(mode):
                content = path.read_bytes()
                git_mode = "100755" if mode & stat.S_IXUSR else "100644"
            else:
                raise ApplicationError(
                    "unsupported_result",
                    (
                        "The result contains a submodule or non-file path. Submit ordinary"
                        " files in this local slice."
                    ),
                )
            object_id = git(
                self.checkout, "hash-object", "--no-filters", "-w", "--stdin", input=content
            ).strip()
            entries.append(git_mode.encode() + b" " + object_id + b"\t" + encoded + b"\0")
        git(
            self.checkout,
            "update-index",
            "-z",
            "--index-info",
            index=index,
            input=b"".join(entries),
        )
        tree = git(self.checkout, "write-tree", index=index).decode().strip()
        commit = (
            git(
                self.checkout,
                "-c",
                "user.name=Flowfield",
                "-c",
                "user.email=local@flowfield.invalid",
                "commit-tree",
                tree,
                "-p",
                parent,
                "-m",
                "Managed task result",
            )
            .decode()
            .strip()
        )
        git(self.checkout, "update-ref", f"refs/flowfield/results/{self.root.name}", commit)
        ignored = [
            os.fsdecode(p)
            for p in git(
                self.checkout, "ls-files", "--others", "--ignored", "--exclude-standard", "-z"
            ).split(b"\0")
            if p
        ]
        return commit, ignored
