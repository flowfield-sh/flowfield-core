"""Explicit signed-out native check of a verified bundle; sends no model prompt."""

import argparse
import asyncio
import json
import shlex
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from acp.exceptions import RequestError

from flowfield.adapters import claude_install
from flowfield.adapters.acp_session import AcpSession
from flowfield.adapters.claude_cleanup import CLAUDE_SHUTDOWN_TIMEOUTS, quiesce, require_cleanup


async def probe(bundle: Path, native: Path, scratch: Path) -> dict:
    binary = claude_install.install(scratch / "installed", bundle, claude_install.digest(bundle))
    home = scratch / "home"
    home.mkdir()
    project = scratch / "project"
    project.mkdir()
    config = home / ".claude"
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"model": "sonnet", "availableModels": ["sonnet"]})
    )
    ambient = scratch / "ambient-mcp-started"
    (project / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ambient_fixture": {
                        "command": sys.executable,
                        "args": [
                            "-c",
                            "from pathlib import Path; "
                            f"Path({str(ambient)!r}).write_text('started')",
                        ],
                    }
                }
            }
        )
    )
    hook_marker = scratch / "startup-hook-ran"
    hook_code = f"from pathlib import Path; Path({str(hook_marker)!r}).write_text('started')"
    (project / ".claude").mkdir()
    (project / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": " ".join(
                                        shlex.quote(value)
                                        for value in [
                                            sys.executable,
                                            "-c",
                                            hook_code,
                                        ]
                                    ),
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )
    (project / ".env").write_text(f"CLAUDE_AGENT_LOGS={scratch / 'forbidden-logs'}\n")
    (project / "bunfig.toml").write_text('preload = ["./must-not-load.js"]\n')
    (project / "must-not-load.js").write_text('throw new Error("Unexpected preload");\n')
    env = {
        "HOME": str(home),
        "PATH": "",
        "CLAUDE_CONFIG_DIR": str(config),
        "CLAUDE_CODE_EXECUTABLE": str(native),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_AGENT_ACP_EXPERIMENTAL_V2": "1",
    }
    client = AcpSession(
        lambda _: None,
        cleanup=quiesce,
        request_timeout=30,
        shutdown_timeouts=CLAUDE_SHUTDOWN_TIMEOUTS,
    )
    try:
        await client.start(
            [str(binary)],
            cwd=project,
            env=env,
            mcp_servers=[],
            session_metadata={
                "claudeCode": {
                    "options": {
                        "persistSession": False,
                        "strictMcpConfig": True,
                        "permissionMode": "default",
                        "allowDangerouslySkipPermissions": False,
                    }
                }
            },
        )
        require_cleanup(client.capabilities)
        assert client.connection is not None
        info = await client.connection.ext_method(
            "flowfield/sessionInfo", {"sessionId": client.session_id}
        )
        assert set(info) == {"version", "sessionId", "model", "models"}
        assert info["model"] == "claude-sonnet-5-5"
        assert [item["id"] for item in info["models"]] == ["default", "sonnet"]
        for method, session_id in [
            ("flowfield/proofStatus", client.session_id),
            ("flowfield/sessionInfo", "another-session"),
        ]:
            try:
                await client.connection.ext_method(method, {"sessionId": session_id})
            except RequestError:
                pass
            else:
                raise AssertionError("Private or foreign-session diagnostics were exposed")
    finally:
        receipt = await client.close()
    assert receipt.owned_work_stopped is True and receipt.process_group_exited
    assert not (scratch / "forbidden-logs").exists()
    assert not ambient.exists()
    return {
        "noPromptSent": True,
        "emptyPath": True,
        "isolatedNativeConfig": True,
        "installedVersion": claude_install.VERSION,
        "model": info["model"],
        "policyFilteredCatalog": True,
        "privateDiagnosticsUnavailable": True,
        "projectAutoloadDisabled": True,
        "ambientMcpExcluded": True,
        "nativeStartupHookObserved": hook_marker.exists(),
        "stop": asdict(receipt),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--native", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="flowfield-claude-runtime-") as temporary:
        result = asyncio.run(
            probe(
                args.bundle.resolve(strict=True), args.native.resolve(strict=True), Path(temporary)
            )
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
