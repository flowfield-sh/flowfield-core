"""Reusable isolated setup and scripted peers; no collected test cases."""

from pathlib import Path

from flowfield.adapters.git_workspace import baseline, git


def commit(repository: Path, filename: str, content: str) -> str:
    (repository / filename).write_text(content)
    git(repository, "add", filename)
    git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        filename,
    )
    return baseline(repository)
