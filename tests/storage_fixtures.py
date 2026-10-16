"""Reusable isolated test support; importing this module never collects a test suite."""

from contextlib import closing

from flowfield import storage


def raw(directory):
    return closing(storage.connect(directory / storage.DATABASE))


def logical_data(directory):
    with raw(directory) as db:
        return list(db.iterdump())
