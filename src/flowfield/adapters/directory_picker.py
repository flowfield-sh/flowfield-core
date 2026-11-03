"""Explicit native directory selection on the Local service host; never upload files."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from threading import Lock

from pydantic import BaseModel

from flowfield.errors import ApplicationError

_selection = Lock()
TIMEOUT = 300


class DirectorySelection(BaseModel):
    path: str | None = None


def picker_command() -> list[str]:
    if sys.platform == "darwin":
        return [
            "osascript",
            "-e",
            'try\nreturn POSIX path of (choose folder with prompt "Choose your Flowfield project")'
            '\non error number -128\nreturn ""\nend try',
        ]
    if sys.platform == "win32":
        return [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-Command",
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$picker = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$picker.Description = 'Choose your Flowfield project'; "
            "$picker.ShowNewFolderButton = $false; "
            "try { if ($picker.ShowDialog() -eq 'OK') { "
            "[Console]::Write($picker.SelectedPath) } } finally { $picker.Dispose() }",
        ]
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        if shutil.which("zenity"):
            return [
                "zenity",
                "--file-selection",
                "--directory",
                "--title=Choose your Flowfield project",
            ]
        if shutil.which("kdialog"):
            return [
                "kdialog",
                "--getexistingdirectory",
                str(Path.home()),
                "--title",
                "Choose your Flowfield project",
            ]
    raise ApplicationError(
        "directory_picker_unavailable",
        "No desktop folder chooser is available on the Flowfield service host. "
        "Ask the Coordinator to add the project using its full directory path.",
        409,
    )


def select_directory() -> DirectorySelection:
    if not _selection.acquire(blocking=False):
        raise ApplicationError("directory_picker_busy", "A folder chooser is already open.", 409)
    try:
        try:
            result = subprocess.run(
                picker_command(), capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT
            )
        except subprocess.TimeoutExpired as error:
            raise ApplicationError(
                "directory_picker_timeout",
                "Folder selection timed out. Choose a directory again.",
                409,
            ) from error
        except OSError as error:
            raise ApplicationError(
                "directory_picker_unavailable",
                "Could not open the host folder chooser. Ask the Coordinator to add "
                "the project using its full directory path.",
                409,
            ) from error
        # Linux dialog tools use exit 1 for Cancel; macOS/Windows return empty output.
        if result.returncode == 1 and sys.platform not in ("darwin", "win32"):
            return DirectorySelection()
        if result.returncode != 0:
            raise ApplicationError(
                "directory_picker_failed",
                "Could not select a directory. Try again or ask the Coordinator to add it.",
                409,
            )
        value = result.stdout.removesuffix("\n").removesuffix("\r")
        if not value:
            return DirectorySelection()
        path = Path(value)
        if not path.is_absolute() or not path.is_dir():
            raise ApplicationError("invalid_path", "Choose an existing project directory.")
        return DirectorySelection(path=str(path.resolve()))
    finally:
        _selection.release()
