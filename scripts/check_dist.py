"""Exercise the exact release artifacts through pip and uv tool in disposable environments."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]


def artifacts(directory: Path, version: str) -> list[Path]:
    wheels = sorted(directory.glob(f"flowfield_core-{version}-*.whl"))
    source = directory / f"flowfield_core-{version}.tar.gz"
    if not source.is_file() or not wheels:
        raise ValueError("Missing the selected version's wheel or source archive.")
    if {p for p in directory.iterdir() if p.name != ".gitignore"} != {*wheels, source}:
        raise ValueError("Unexpected distribution artifacts.")
    supported = set(sys_tags())
    matches = [wheel for wheel in wheels if parse_wheel_filename(wheel.name)[3] & supported]
    if len(matches) != 1:
        raise ValueError("Expected exactly one wheel compatible with this platform.")
    return [matches[0], source]


def environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("FLOWFIELD_", "UV_", "PIP_"))
        and key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    } | {"FLOWFIELD_UPDATE_CHECKS": "0"}


def verify(directory: Path, version: str, install_check: Path) -> None:
    wheel, source = artifacts(directory, version)
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("Install uv to create isolated distribution-check environments.")
    for method, artifact in [("pip wheel", wheel), ("pip source", source), ("uv tool", wheel)]:
        with tempfile.TemporaryDirectory(prefix="flowfield-dist-") as temporary:
            scratch = Path(temporary)
            env = environment()
            env["PATH"] = str(Path(sys.executable).parent)

            def run(
                *command: str,
                cwd: Path = scratch,
                process_env: dict[str, str] = env,
            ) -> None:
                subprocess.run(command, cwd=cwd, env=process_env, check=True, timeout=180)

            if method == "uv tool":
                env.update(UV_TOOL_DIR=str(scratch / "tools"), UV_TOOL_BIN_DIR=str(scratch / "bin"))
                run(
                    uv,
                    "tool",
                    "install",
                    "--no-config",
                    "--default-index",
                    "https://pypi.org/simple",
                    str(artifact),
                )
                python = scratch / "tools/flowfield-core/bin/python"
                run(str(scratch / "bin/flowfield"), "--version")
            else:
                python = scratch / "venv/bin/python"
                run(
                    uv,
                    "venv",
                    "--no-config",
                    "--seed",
                    "--python",
                    sys.executable,
                    str(python.parent.parent),
                )
                run(
                    str(python),
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--index-url",
                    "https://pypi.org/simple",
                    str(artifact),
                )
                run(str(python), "-m", "pip", "check")
            # Copy the standalone check so the application never depends on the checkout.
            check = scratch / "check-install.py"
            shutil.copyfile(install_check, check)
            shutil.copyfile(ROOT / "tests/fake_claude_cli.py", scratch / "native-fixture.py")
            shutil.copyfile(ROOT / "tests/fake_pi.py", scratch / "pi-fixture.py")
            isolated = {
                **env,
                "PATH": str(python.parent),
                "EXPECTED_VERSION": version,
            }
            subprocess.run(
                [str(python), "-I", str(check)], cwd=scratch, env=isolated, check=True, timeout=120
            )
            print(f"Verified {method}: {artifact.name}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--version")
    args = parser.parse_args()
    version = (
        args.version or tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    )
    # Also works in a downloaded release-assets artifact, without pyproject.toml/a checkout.
    verify(args.dist.resolve(), version, ROOT / "scripts/check-install.py")


if __name__ == "__main__":
    main()
