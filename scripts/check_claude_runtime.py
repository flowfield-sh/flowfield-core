"""Explicit signed-out native check of a verified bundle; sends no model prompt."""

import argparse
import asyncio
import json
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
    config = home / ".claude"
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"model": "sonnet", "availableModels": ["sonnet"]})
    )
    (home / ".env").write_text(f"CLAUDE_AGENT_LOGS={scratch / 'forbidden-logs'}\n")
    (home / "bunfig.toml").write_text('preload = ["./must-not-load.js"]\n')
    (home / "must-not-load.js").write_text('throw new Error("Unexpected preload");\n')
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
        await client.start([str(binary)], cwd=home, env=env, mcp_servers=[])
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
    return {
        "noPromptSent": True,
        "emptyPath": True,
        "isolatedNativeConfig": True,
        "installedVersion": claude_install.VERSION,
        "model": info["model"],
        "policyFilteredCatalog": True,
        "privateDiagnosticsUnavailable": True,
        "projectAutoloadDisabled": True,
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
