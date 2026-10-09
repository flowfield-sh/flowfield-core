"""HTTP client for local interfaces. Only the service opens the database."""

import json
import shlex
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from flowfield.errors import ApplicationError
from flowfield.reads import receipt
from flowfield.state import data_path


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class Client:
    def __init__(self, directory: Path, port: int):
        self.directory = directory
        self.port = port

    def serve_command(self) -> str:
        command = ["flowfield"]
        if self.directory.resolve() != data_path().resolve():
            command += ["--data-dir", str(self.directory)]
        return shlex.join([*command, "serve", "--port", str(self.port)])

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: int = 60,
        compact: bool = True,
    ) -> Any:
        request = Request(
            f"http://127.0.0.1:{self.port}/api/{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(
                request, timeout=timeout
            ) as response:
                payload = json.load(response)
                return receipt(payload) if method != "GET" and compact else payload
        except HTTPError as error:
            payload = json.loads(error.read())
            detail = payload.get("error", {})
            message = detail.get("message")
            if message is None:
                validation = payload.get("detail", "Request failed.")
                if isinstance(validation, list):
                    fields = []
                    for item in validation:
                        location = ".".join(
                            str(part)
                            for part in item.get("loc", [])
                            if part not in ("body", "query", "path")
                        )
                        fields.append(f"- {location or 'Request'}: {item['msg']}")
                    message = "Invalid request:\n" + "\n".join(fields)
                else:
                    message = str(validation)
            raise ApplicationError(
                detail.get("code", "invalid_request"),
                message,
                error.code,
            ) from error
        except (URLError, TimeoutError) as error:
            raise ApplicationError(
                "service_unavailable",
                f"Cannot reach Flowfield on port {self.port}.\n"
                f"Start it with: {self.serve_command()}\n"
                "Keep that terminal open while using Flowfield.",
            ) from error
