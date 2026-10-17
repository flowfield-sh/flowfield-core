"""Ordinary tests never contact the package registry implicitly."""

import pytest


@pytest.fixture(autouse=True)
def no_automatic_registry_requests(monkeypatch):
    monkeypatch.setenv("FLOWFIELD_UPDATE_CHECKS", "0")
