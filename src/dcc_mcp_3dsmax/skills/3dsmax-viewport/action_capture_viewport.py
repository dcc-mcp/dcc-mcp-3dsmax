"""Capture the active 3ds Max viewport."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from dcc_mcp_3dsmax.api import get_runtime, max_error, with_max


@with_max
def main(file_path: Optional[str] = None) -> Dict[str, Any]:
    """Capture the active viewport to a PNG file.

    The capture is written to a temporary file in the destination directory and
    atomically renamed into place only after the file is confirmed non-empty, so
    a success response always means the target file exists and is fully flushed.
    """
    try:
        output_path = _resolve_output_path(file_path)
    except ValueError as exc:
        return max_error(str(exc))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    rt = get_runtime()

    temp_fd, temp_name = tempfile.mkstemp(
        prefix=output_path.name + ".",
        suffix=output_path.suffix,
        dir=str(output_path.parent),
    )
    os.close(temp_fd)
    temp_path = Path(temp_name)
    try:
        script = _capture_script(temp_path)
        rt.execute(script)

        if not temp_path.exists() or not temp_path.is_file():
            return max_error(
                "Viewport capture did not produce a file",
                file_path=str(output_path),
            )
        if temp_path.stat().st_size <= 0:
            return max_error(
                "Viewport capture produced an empty file",
                file_path=str(output_path),
                size_bytes=0,
            )

        os.replace(temp_path, output_path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    size_bytes = output_path.stat().st_size
    return {
        "success": True,
        "message": "Captured active viewport",
        "data": {
            "file_path": str(output_path),
            "size_bytes": size_bytes,
        },
    }


def _resolve_output_path(file_path: Optional[str]) -> Path:
    if file_path is None or not str(file_path).strip():
        return Path(tempfile.gettempdir()) / "dcc_mcp_3dsmax_viewport.png"

    path = Path(str(file_path)).expanduser()
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
        raise ValueError("file_path must end with .png, .jpg, .jpeg, or .bmp")
    return path


def _capture_script(output_path: Path) -> str:
    path = _maxscript_string(str(output_path))
    directory = _maxscript_string(str(output_path.parent))
    return """(
    makeDir {directory} all:true
    completeRedraw()
    local viewportBitmap = gw.getViewportDib()
    viewportBitmap.filename = {path}
    save viewportBitmap
    {path}
)""".format(directory=directory, path=path)


def _maxscript_string(value: Any) -> str:
    text = str(value).replace("\\", "/").replace('"', '\\"')
    return '"{}"'.format(text)
