"""Pi's JSONL record format over the shared bounded native process transport."""

from typing import Any

from flowfield.adapters.json_rpc import JsonRpc, NativeError


class PiRpc(JsonRpc):
    def call_frame(self, identity: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {**params, "id": str(identity), "type": method}

    def decode_frame(self, value: dict[str, Any]) -> dict[str, Any]:
        kind = value.get("type")
        if kind == "response":
            identity = value.get("id")
            if not isinstance(identity, str) or not identity.isdecimal():
                raise NativeError("Uncorrelated Pi response")
            if value.get("success") is not True:
                return {"id": int(identity), "error": "Native request failed"}
            return {"id": int(identity), "result": value.get("data") or {}}
        if kind == "extension_ui_request":
            if value.get("method") not in {"confirm", "select", "input", "editor"}:
                return {"method": kind, "params": value}
            return {"id": value["id"], "method": kind, "params": value}
        if not isinstance(kind, str):
            raise NativeError("Invalid Pi event")
        return {"method": kind, "params": value}

    def response_frame(self, identity: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {**result, "id": identity, "type": "extension_ui_response"}

    def error_frame(self, identity: Any) -> dict[str, Any]:
        return self.response_frame(identity, {"cancelled": True})
