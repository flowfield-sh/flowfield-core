"""Seed a report attempt through execution owners with the service queue paused."""

import json
import sys
from pathlib import Path

from lifecycle_fixtures import run_report

from flowfield.application import Workspace

if __name__ == "__main__":
    store = Workspace(Path(sys.argv[1]))
    value = run_report(store, sys.argv[2], sys.argv[3], **json.loads(sys.argv[4]))
    print(value.model_dump_json())
