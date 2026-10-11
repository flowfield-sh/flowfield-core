"""Seed a deterministic release observation; no package registry or model calls."""

import sys
from pathlib import Path

from flowfield.announcements import announce_update
from flowfield.application import Workspace, now
from flowfield.updates import save_state, state

workspace = Workspace(Path(sys.argv[1]))
with workspace.connection(write=True, notify=False) as db:
    saved = state(db)
    checked = now()
    saved.update(latest_version=sys.argv[2], last_success=checked, last_attempt=checked, error=None)
    save_state(db, saved)
    import time

    announce_update(db, sys.argv[2], time.time())
