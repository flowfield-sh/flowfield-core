"""One browser test's service; the OS retains ownership of its allocated port."""

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

from flowfield.api import create_app

state, address = map(Path, sys.argv[1:3])
owner = int(sys.argv[3])
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    address.write_text(json.dumps({"port": listener.getsockname()[1], "pid": os.getpid()}))
    server = uvicorn.Server(
        uvicorn.Config(create_app(data_dir=state), host="127.0.0.1", log_level="warning")
    )

    def watch_owner() -> None:
        # A killed Playwright worker cannot run fixture teardown. Stop gracefully
        # instead of leaving its detached service running; preserve its store.
        while not server.should_exit:
            try:
                os.kill(owner, 0)
            except ProcessLookupError:
                server.should_exit = True
                return
            time.sleep(0.25)

    threading.Thread(target=watch_owner, daemon=True).start()
    server.run(sockets=[listener])
