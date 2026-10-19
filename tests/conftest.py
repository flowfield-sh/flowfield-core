"""Ordinary tests never contact the package registry implicitly."""

import pytest


@pytest.fixture
def catalog_host(tmp_path, monkeypatch):
    """Discovery tests replace native calls; host detection must also be isolated."""
    import sys

    for executable, configuration in (
        ("CODEX_PATH", "CODEX_HOME"),
        ("CLAUDE_CODE_EXECUTABLE", "CLAUDE_CONFIG_DIR"),
        ("PI_PATH", "PI_CODING_AGENT_DIR"),
    ):
        monkeypatch.setenv(executable, sys.executable)
        monkeypatch.setenv(configuration, str(tmp_path))


@pytest.fixture(autouse=True)
def no_automatic_registry_requests(monkeypatch):
    monkeypatch.setenv("FLOWFIELD_UPDATE_CHECKS", "0")
