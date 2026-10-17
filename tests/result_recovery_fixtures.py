"""Reusable isolated setup and scripted peers; no collected test cases."""

import os

from flowfield.adapters.git_workspace import git
from flowfield.adapters.local_execution import LocalHost


def target_change(service, repo, base, name="target.txt", content="target change\n"):
    env = LocalHost(os.environ).prepare(
        service.workspace.directory, repo, "target-" + name.replace(".", "-"), base
    )
    (env.checkout / name).write_text(content)
    commit, _ = env.snapshot(base)
    git(repo, "merge", "--ff-only", commit)
    return commit
