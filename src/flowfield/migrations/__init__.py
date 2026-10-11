"""Ordered database-only upgrades from the Flowfield schema-44 baseline."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from flowfield.migrations import (
    v045_host_harnesses,
    v046_harness_catalogs,
    v047_worker_choices,
    v048_coordinator_handoffs,
    v049_coordinator_generations,
    v050_native_adapter_provenance,
    v051_coordinator_prose,
    v052_pi_harness,
    v053_coordinator_welcome,
    v054_contextual_announcements,
    v055_workspaces,
    v056_workspace_workers,
    v057_coordinator_homes,
    v058_workspace_coordinator_settings,
)

BASELINE_VERSION = 44


@dataclass(frozen=True)
class Migration:
    version: int
    apply: Callable[[sqlite3.Connection], None]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(45, v045_host_harnesses.apply),
    Migration(46, v046_harness_catalogs.apply),
    Migration(47, v047_worker_choices.apply),
    Migration(48, v048_coordinator_handoffs.apply),
    Migration(49, v049_coordinator_generations.apply),
    Migration(50, v050_native_adapter_provenance.apply),
    Migration(51, v051_coordinator_prose.apply),
    Migration(52, v052_pi_harness.apply),
    Migration(53, v053_coordinator_welcome.apply),
    Migration(54, v054_contextual_announcements.apply),
    Migration(55, v055_workspaces.apply),
    Migration(56, v056_workspace_workers.apply),
    Migration(57, v057_coordinator_homes.apply),
    Migration(58, v058_workspace_coordinator_settings.apply),
)


def current_version() -> int:
    versions = [migration.version for migration in MIGRATIONS]
    if versions != list(range(BASELINE_VERSION + 1, BASELINE_VERSION + 1 + len(versions))):
        raise RuntimeError("Database migrations must be contiguous and ordered.")
    return versions[-1] if versions else BASELINE_VERSION
