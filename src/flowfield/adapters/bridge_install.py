"""Checked standalone bundles with bounded downloads and immutable installations.

Native executable discovery, configuration and authentication remain adapter-owned.
"""

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from flowfield import __version__
from flowfield.errors import ApplicationError

MAX_ARCHIVE = 128 * 1024 * 1024
MAX_UNPACKED = 256 * 1024 * 1024
RELEASES = "https://api.github.com/repos/flowfield-sh/flowfield-core/releases/tags/"


def target() -> str:
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    machine = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64"}.get(platform.machine())
    if not system or not machine:
        raise ApplicationError(
            "unsupported_platform", "Managed agents require macOS or Linux on arm64/x64."
        )
    return f"{system}-{machine}"


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def invalid(message: str) -> ApplicationError:
    return ApplicationError("invalid_bridge_bundle", message, 409)


class BridgeBundle:
    def __init__(
        self,
        harness: str,
        label: str,
        executable: str,
        spec: dict[str, str],
        *,
        max_archive: int = MAX_ARCHIVE,
        max_unpacked: int = MAX_UNPACKED,
    ):
        self.harness = harness
        self.label = label
        self.spec = spec
        self.version = spec["version"]
        self.executable = executable
        self.files = {self.executable, "LICENSE", "THIRD_PARTY_NOTICES.txt"}
        self.max_archive = max_archive
        self.max_unpacked = max_unpacked

    def manifest(self, path: Path) -> dict[str, Any]:
        if path.stat().st_size > 16384:
            raise invalid("Bridge manifest is too large.")
        try:
            value = json.loads(path.read_text())
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "schema",
                    "name",
                    "version",
                    "upstream_revision",
                    "bun_version",
                    "platform",
                    "files",
                }
                or type(value["schema"]) is not int
                or value["schema"] != 1
                or value["name"] != self.executable
                or value["platform"] != target()
                or any(value.get(key) != expected for key, expected in self.spec.items())
                or not isinstance(value["files"], dict)
                or set(value["files"]) != self.files
                or any(
                    not isinstance(h, str) or re.fullmatch(r"[0-9a-f]{64}", h) is None
                    for h in value["files"].values()
                )
            ):
                raise invalid("The bundle is incompatible with this Flowfield version or platform.")
            return value
        except (ValueError, UnicodeError) as error:
            raise invalid("Bridge manifest is not valid JSON.") from error

    def verify(self, directory: Path) -> dict[str, Any]:
        if directory.is_symlink() or any(
            (directory / name).is_symlink() for name in self.files | {"manifest.json"}
        ):
            raise invalid("Bridge installation must contain regular files.")
        value = self.manifest(directory / "manifest.json")
        for name, expected in value["files"].items():
            path = directory / name
            if (
                not path.is_file()
                or path.stat().st_size > self.max_unpacked
                or digest(path) != expected
            ):
                raise invalid(
                    "Bridge files do not match their recorded checksums. Reinstall the bundle."
                )
        return value

    def installed(self, directory: Path) -> Path:
        root = directory / "harnesses" / self.harness / self.version / target()
        try:
            pointer = root / "current"
            if pointer.stat().st_size > 100:
                raise invalid("Invalid bridge installation pointer.")
            current = pointer.read_text().strip()
            if re.fullmatch(r"[0-9a-f]{64}-[0-9a-f]{32}", current) is None:
                raise invalid("Invalid bridge installation pointer.")
            selected = root / current
            self.verify(selected)
            binary = selected / self.executable
            if not os.access(binary, os.X_OK):
                raise invalid("The installed bridge is not executable. Reinstall the bundle.")
            return binary
        except FileNotFoundError as error:
            raise ApplicationError(
                "bridge_missing",
                f"Install the managed {self.label} runtime with "
                f"`flowfield harness install {self.harness}`.",
                409,
            ) from error

    def install(self, directory: Path, bundle: Path, expected_sha256: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise invalid(
                "Provide the bundle's SHA-256 checksum (64 lowercase hexadecimal characters)."
            )
        root = directory / "harnesses" / self.harness / self.version / target()
        import fcntl

        root.mkdir(parents=True, exist_ok=True)
        with (root / "install.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ApplicationError(
                    "install_busy", f"Another {self.label} installation is in progress.", 409
                ) from error
            with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temporary:
                scratch = Path(temporary)
                archive = scratch / "bundle.zip"
                checksum = hashlib.sha256()
                size = 0
                with bundle.open("rb") as source, archive.open("wb") as output:
                    while block := source.read(1024 * 1024):
                        size += len(block)
                        if size > self.max_archive:
                            raise invalid("Bridge archive is too large.")
                        checksum.update(block)
                        output.write(block)
                if checksum.hexdigest() != expected_sha256:
                    raise invalid(
                        "Bridge archive checksum mismatch. The installed version was preserved."
                    )
                candidate = scratch / "candidate"
                candidate.mkdir()
                try:
                    with zipfile.ZipFile(archive) as zipped:
                        entries = zipped.infolist()
                        if (
                            len(entries) != len(self.files) + 1
                            or {e.filename for e in entries} != self.files | {"manifest.json"}
                            or sum(e.file_size for e in entries) > self.max_unpacked
                            or any(
                                e.is_dir() or stat.S_ISLNK(e.external_attr >> 16) for e in entries
                            )
                        ):
                            raise invalid("Unexpected files or size in the bridge archive.")
                        for entry in entries:
                            with (
                                zipped.open(entry) as source,
                                (candidate / entry.filename).open("wb") as output,
                            ):
                                shutil.copyfileobj(source, output, length=1024 * 1024)
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
                    raise invalid("Invalid or unsupported bridge archive.") from error
                self.verify(candidate)
                (candidate / self.executable).chmod(0o755)
                try:
                    previous = self.installed(directory).parent
                    if previous.name.startswith(expected_sha256 + "-"):
                        return previous / self.executable
                except (ApplicationError, OSError):
                    pass  # A fresh generation repairs damaged files without mutating old paths.
                destination = root / (expected_sha256 + "-" + uuid4().hex)
                candidate.rename(destination)
                pointer = scratch / "current"
                pointer.write_text(destination.name + "\n")
                os.replace(pointer, root / "current")
                return destination / self.executable

    def download_install(self, directory: Path) -> Path:
        try:
            return self.installed(directory)
        except (ApplicationError, OSError):
            pass
        name = f"{self.executable}-{self.version}-{target()}.zip"
        tag = "v" + __version__
        deadline = time.monotonic() + 120
        try:
            with httpx.Client(
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "flowfield/" + __version__},
            ) as client:
                with client.stream("GET", RELEASES + tag) as response:
                    if response.status_code == 404:
                        raise ApplicationError(
                            "bridge_unavailable",
                            f"No {self.label} bundle is published for this Flowfield release. "
                            "Use --bundle PATH --sha256 CHECKSUM for a verified local build.",
                            409,
                        )
                    response.raise_for_status()
                    metadata = bytearray()
                    for block in response.iter_bytes(16384):
                        metadata.extend(block)
                        if len(metadata) > 1024 * 1024 or time.monotonic() > deadline:
                            raise invalid("Release metadata exceeded its size or time limit.")
                release = json.loads(metadata)
                if not isinstance(release, dict) or not isinstance(release.get("assets"), list):
                    raise invalid("Invalid release metadata.")
                assets = [
                    item
                    for item in release["assets"]
                    if isinstance(item, dict) and item.get("name") == name
                ]
                if release.get("draft") or release.get("tag_name") != tag or len(assets) != 1:
                    raise ApplicationError(
                        "bridge_unavailable",
                        f"No compatible {self.label} bundle is published for this Flowfield "
                        "version and platform.",
                        409,
                    )
                asset = assets[0]
                expected_url = (
                    f"https://github.com/flowfield-sh/flowfield-core/releases/download/{tag}/{name}"
                )
                checksum = asset.get("digest", "")
                if (
                    asset.get("browser_download_url") != expected_url
                    or not isinstance(checksum, str)
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", checksum)
                ):
                    raise invalid("Release asset has no verified source and SHA-256 digest.")
                with tempfile.TemporaryDirectory(prefix="flowfield-bridge-") as temporary:
                    bundle = Path(temporary) / name
                    size = 0
                    with client.stream("GET", expected_url) as stream, bundle.open("wb") as output:
                        stream.raise_for_status()
                        for block in stream.iter_bytes(1024 * 1024):
                            size += len(block)
                            if size > self.max_archive or time.monotonic() > deadline:
                                raise invalid("Bridge download exceeded its size or time limit.")
                            output.write(block)
                    return self.install(directory, bundle, checksum.removeprefix("sha256:"))
        except (httpx.HTTPError, ValueError) as error:
            raise ApplicationError(
                "bridge_download_failed",
                f"Cannot download the managed {self.label} bundle. "
                "Retry or use --bundle PATH --sha256 CHECKSUM.",
                409,
            ) from error
