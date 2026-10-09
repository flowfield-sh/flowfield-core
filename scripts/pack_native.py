"""Compress prebuilt SDK executables for a portable source archive; no runtime tools."""

import lzma
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/flowfield/_native"

for target in ("darwin-arm64", "darwin-x64", "linux-arm64", "linux-x64"):
    directory = ROOT / f"claude-sdk-{target}"
    with (directory / "claude-sdk").open("rb") as source:
        with lzma.open(directory / "claude-sdk.xz", "wb", preset=3) as output:
            shutil.copyfileobj(source, output)
