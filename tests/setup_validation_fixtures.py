"""Reusable isolated setup and scripted peers; no collected test cases."""

from guidance_fixtures import setup

from flowfield.adapters.git_workspace import git
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig
from flowfield.setup_validation import SetupValidation


def configured(tmp_path, monkeypatch, **changes):
    root, guidance = setup(tmp_path)
    git(root, "init", "-b", "main")
    (root / "base.txt").write_text("base")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@invalid", "commit", "-m", "base")
    service = SetupValidation(guidance.workspace)
    settings = Integrations(guidance.workspace).configure(
        "project",
        IntegrationConfig(
            runtime="local",
            expected_revision=1,
            target_branch="delivery",
            create_from="HEAD",
            checks=changes.pop("checks", ["test -f base.txt"]),
            **changes,
        ),
    )
    return root, service, settings
