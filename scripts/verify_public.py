"""Read-only public release, docs and static-site verification with bounded propagation waits."""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read(url: str, wait: float = 120) -> bytes:
    deadline = time.monotonic() + wait
    while True:
        try:
            with urlopen(
                Request(url, headers={"User-Agent": "Flowfield-release-verification"}), timeout=30
            ) as response:
                value = response.read(512 * 1024 * 1024 + 1)
                if len(value) > 512 * 1024 * 1024:
                    raise ValueError(f"Public response exceeds verification limit: {url}")
                return value
        except (HTTPError, URLError, TimeoutError) as error:
            if isinstance(error, HTTPError) and error.code not in {404, 429, 500, 502, 503, 504}:
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(2, max(0, deadline - time.monotonic())))


def matching(url: str, expected: str, *, sha: bool = True, wait: float = 120) -> None:
    deadline = time.monotonic() + wait
    while True:
        value = read(url, wait=max(0, deadline - time.monotonic()))
        matches = digest(value) == expected if sha else expected in normalized(value.decode())
        if matches:
            return
        if time.monotonic() >= deadline:
            raise ValueError(f"Public content differs: {url}")
        time.sleep(min(2, max(0, deadline - time.monotonic())))


def normalized(value: str) -> str:
    # Mintlify expands relative Markdown links to absolute .md destinations.
    return re.sub(r"\s+", " ", re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)).strip()


def parallel(checks: list[tuple[str, str]], *, sha: bool = True) -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(matching, url, value, sha=sha) for url, value in checks]
        for future in futures:
            future.result()


def package(candidate: Path, install: bool) -> dict:
    receipt = json.loads((candidate / "candidate.json").read_text())
    if receipt.get("repository") != "flowfield-sh/flowfield-core":
        raise ValueError("Candidate belongs to another repository")
    version, commit = receipt["version"], receipt["commit"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid candidate version/source")
    for name, checksum in receipt["files"].items():
        path = candidate / name
        if not path.resolve().is_relative_to(candidate.resolve()) or path.is_symlink():
            raise ValueError("Unsafe candidate payload path")
        if digest(path.read_bytes()) != checksum:
            raise ValueError(f"Candidate payload changed: {name}")
    release = json.loads(
        subprocess.check_output(
            ["gh", "api", f"repos/flowfield-sh/flowfield-core/releases/tags/v{version}"], text=True
        )
    )
    tag_commit = subprocess.check_output(
        ["gh", "api", f"repos/flowfield-sh/flowfield-core/commits/v{version}", "--jq", ".sha"],
        text=True,
    ).strip()
    if tag_commit != commit or release["draft"] or release["prerelease"]:
        raise ValueError("Published release identity differs")
    if release["body"].strip() != (candidate / "release-notes.md").read_text().strip():
        raise ValueError("Published release notes differ")
    index = json.loads(read(f"https://pypi.org/pypi/flowfield-core/{version}/json"))
    expected = {
        name.removeprefix("dist/"): checksum
        for name, checksum in receipt["files"].items()
        if name.startswith("dist/")
    }
    if len(expected) != 4 or set(expected) != {file["filename"] for file in index["urls"]}:
        raise ValueError("Unexpected PyPI distribution inventory")
    assets = {asset["name"]: asset["browser_download_url"] for asset in release["assets"]}
    if set(assets) != {*expected, "SHA256SUMS"}:
        raise ValueError("Unexpected GitHub release inventory")
    checks = [(assets[name], checksum) for name, checksum in expected.items()]
    checks += [(file["url"], expected[file["filename"]]) for file in index["urls"]]
    checks.append((assets["SHA256SUMS"], receipt["files"]["SHA256SUMS"]))
    parallel(checks)
    # Wait for the installation index independently of PyPI's JSON API.
    index_url = "https://pypi.org/simple/flowfield-core/"
    matching(index_url, f"flowfield_core-{version}", sha=False)
    if install:
        with tempfile.TemporaryDirectory(prefix="flowfield-public-install-") as temporary:
            directory = Path(temporary)
            python = directory / "venv" / "bin" / "python"
            subprocess.run(
                ["uv", "venv", "--python", sys.executable, str(directory / "venv")], check=True
            )
            subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--no-cache",
                    "--index-url",
                    "https://pypi.org/simple",
                    f"flowfield-core=={version}",
                ],
                check=True,
            )
            subprocess.run(["uv", "pip", "check", "--python", str(python)], check=True)
            for source, target in [
                ("scripts/check-install.py", "check-install.py"),
                ("tests/fake_claude_cli.py", "native-fixture.py"),
                ("tests/fake_pi.py", "pi-fixture.py"),
            ]:
                original = subprocess.check_output(
                    ["git", "show", f"{commit}:{source}"], cwd=Path(__file__).resolve().parents[1]
                )
                if original != (candidate / source).read_bytes():
                    raise ValueError(
                        f"Installation fixture differs from published source: {source}"
                    )
                shutil.copy2(candidate / source, directory / target)
            env = {
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONPATH", "FLOWFIELD_DATA_DIR", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR"}
            }
            env["FLOWFIELD_UPDATE_CHECKS"] = "0"
            subprocess.run(
                [str(python), str(directory / "check-install.py")],
                cwd=directory,
                env=env,
                check=True,
                timeout=300,
            )
            subprocess.run(
                [
                    str(python),
                    "-c",
                    """
import json, platform, urllib.request
from flowfield import __version__
from flowfield.updates import latest_release, available
import sys
assert __version__ == sys.argv[1]
request = urllib.request.Request('https://pypi.org/simple/flowfield-core/',
    headers={'Accept': 'application/vnd.pypi.simple.v1+json'})
with urllib.request.urlopen(request, timeout=30) as response:
    payload = json.load(response)
latest = latest_release(payload, '0.0.0', platform.python_version())
assert latest and tuple(map(int, latest.split('.'))) >= tuple(map(int, __version__.split('.')))
assert available(latest, '0.0.0') == latest
assert available(latest, latest) is None
print('Public installation and update discovery verified.')
""",
                    version,
                ],
                cwd=directory,
                env=env,
                check=True,
                timeout=60,
            )
    return {"version": version, "source": commit, "files": expected, "installed": install}


def docs(root: Path, base: str) -> dict:
    config = json.loads((root / "docs.json").read_text())
    pages = [page for group in config["navigation"]["groups"] for page in group["pages"]]
    checks = []
    for page in pages:
        source = (root / f"{page}.mdx").read_text()
        title = re.search(r"^title: (.*)$", source, re.MULTILINE)[1]
        paragraph = source.split("---", 2)[2].strip().split("\n\n")[0]
        checks += [
            (f"{base}/{page}", normalized(title)),
            (f"{base}/{page}.md", normalized(paragraph)),
        ]
    parallel(checks, sha=False)
    images = [
        (f"{base}/images/{path.name}", digest(path.read_bytes()))
        for path in (root / "images").glob("*.png")
    ]
    parallel(images)
    for redirect in config.get("redirects", []):
        with urlopen(base + redirect["source"], timeout=30) as response:
            if response.url.rstrip("/") != base + redirect["destination"]:
                raise ValueError(f"Wrong docs redirect: {redirect['source']}")
    return {"pages": pages, "images": len(images)}


def site(root: Path, base: str) -> dict:
    checks = []
    for path in root.rglob("*"):
        if path.is_file():
            route = path.relative_to(root).as_posix()
            if route.endswith("index.html"):
                route = route.removesuffix("index.html")
            checks.append((f"{base}/{route}", digest(path.read_bytes())))
    if not checks:
        raise ValueError("Static site build is empty")
    parallel(checks)
    return {"files": len(checks)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--docs-root", type=Path)
    parser.add_argument("--docs-url", default="https://docs.flowfield.sh")
    parser.add_argument("--site-build", type=Path)
    parser.add_argument("--site-url", default="https://www.flowfield.sh")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if not (args.candidate or args.docs_root or args.site_build) or (
        args.install and not args.candidate
    ):
        parser.error("Choose verification surfaces; --install requires --candidate")
    result = {"started_at": time.time(), "checks": {}}
    try:
        if args.candidate:
            result["checks"]["package"] = package(args.candidate.resolve(), args.install)
        if args.docs_root:
            result["checks"]["docs"] = docs(args.docs_root, args.docs_url.rstrip("/"))
        if args.site_build:
            result["checks"]["site"] = site(args.site_build, args.site_url.rstrip("/"))
        result["success"] = True
    except Exception as error:
        result.update(success=False, error=str(error))
        raise
    finally:
        result["finished_at"] = time.time()
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
