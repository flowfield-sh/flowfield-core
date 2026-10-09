"""Reject distributable artifacts missing their prebuilt admin UI."""

import lzma
import os
import platform
import re
import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name == "wheel" and version == "editable":
            return
        web = Path(self.root) / "src/flowfield/_web"
        index = web / "index.html"
        error = "Admin UI is missing or incomplete. Run `pnpm --dir web build` before packaging."
        if not index.is_file():
            raise RuntimeError(error)
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index.read_text())
        if not any(asset.endswith(".js") for asset in assets) or not any(
            asset.endswith(".css") for asset in assets
        ):
            raise RuntimeError(error)
        if any(not (web / asset.lstrip("/")).is_file() for asset in assets):
            raise RuntimeError(error)
        notices = [
            "third-party-licenses.md",
            "shadcn-license.txt",
            "tailwindcss-license.txt",
            "react-remove-scroll-bar-license.txt",
            "harness-icons-license.txt",
            "pi-logo-notice.txt",
        ]
        if any(
            not (web / "assets" / notice).is_file()
            or not (web / "assets" / notice).read_text().strip()
            for notice in notices
        ):
            raise RuntimeError("Bundled UI license notices are missing. Rebuild the browser UI.")
        if self.target_name == "wheel":
            machine = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64"}.get(
                platform.machine()
            )
            system = platform.system().lower()
            target = os.environ.get("FLOWFIELD_BUILD_TARGET")
            target = target or f"{system}-{machine}"
            if target not in {"darwin-arm64", "linux-arm64", "linux-x64"}:
                raise RuntimeError(
                    "Flowfield supports Apple Silicon Macs and Linux arm64/x64. "
                    "Intel Macs are not supported."
                )
            system, machine = target.split("-")
            native = Path(self.root) / "src/flowfield/_native" / f"claude-sdk-{system}-{machine}"
            binary = native / "claude-sdk"
            if not binary.is_file() and (native / "claude-sdk.xz").is_file():
                with (
                    lzma.open(native / "claude-sdk.xz", "rb") as source,
                    binary.open("wb") as output,
                ):
                    shutil.copyfileobj(source, output)
                binary.chmod(0o755)
            if not all(
                (native / name).is_file()
                for name in ("claude-sdk", "version.json", "THIRD_PARTY_NOTICES.txt")
            ):
                raise RuntimeError("Claude SDK runtime is missing. Run the runtime build first.")
            for name in ("claude-sdk", "version.json", "THIRD_PARTY_NOTICES.txt"):
                build_data.setdefault("force_include", {})[str(native / name)] = (
                    "flowfield/_native/claude-sdk/" + name
                )
            if system == "darwin":
                tag = "macosx_13_0_arm64"
            elif system == "linux":
                tag = f"manylinux_2_28_{'aarch64' if machine == 'arm64' else 'x86_64'}"
            else:
                raise RuntimeError("Native integration builds currently support macOS and Linux.")
            build_data["tag"] = "py3-none-" + tag
            build_data["pure_python"] = False
