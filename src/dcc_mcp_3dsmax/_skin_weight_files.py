"""Atomic JSON file exchange for typed Skin weights."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._rigging_utils import rig_error, rig_success
from dcc_mcp_3dsmax._skin_weights import SKIN_WEIGHTS_SCHEMA, get_skin_weights, set_skin_weights

MAX_FILE_BYTES = 16 * 1024 * 1024


def export_skin_weights(
    runtime: Any,
    *,
    output_path: str,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    vertex_indices: Optional[Sequence[int]] = None,
    modifier_name: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Write native Skin weights atomically to an explicit JSON path."""
    path, error = _validated_path(output_path, must_exist=False)
    if error is not None:
        return error
    if not isinstance(overwrite, bool):
        return rig_error("overwrite must be a boolean")
    if path.exists() and not overwrite:
        return rig_error("Skin weight output already exists; set overwrite=true to replace it")
    result = get_skin_weights(
        runtime,
        node_name=node_name,
        handle=handle,
        vertex_indices=vertex_indices,
        modifier_name=modifier_name,
    )
    if not result.get("success"):
        return result
    payload = {
        "schema": SKIN_WEIGHTS_SCHEMA,
        "weights": result["data"]["weights"],
    }
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        return rig_error("Skin weight payload exceeds the file size bound", maximum_bytes=MAX_FILE_BYTES)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".dcc-mcp-skin-",
            suffix=".tmp",
            dir=str(path.parent),
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temp_path), str(path))
    except OSError as exc:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
        return rig_error("Could not write Skin weight file", reason=str(exc))
    return rig_success(
        "Exported Skin weights",
        output_path=str(path),
        sha256=hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
        vertex_count=len(payload["weights"]["vertices"]),
    )


def import_skin_weights(
    runtime: Any,
    *,
    input_path: str,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    modifier_name: Optional[str] = None,
    expected_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify and load an explicit JSON Skin weight file."""
    path, error = _validated_path(input_path, must_exist=True)
    if error is not None:
        return error
    try:
        size = path.stat().st_size
        if size < 1 or size > MAX_FILE_BYTES:
            return rig_error("Skin weight file size is outside the supported bound", size_bytes=size)
        encoded = path.read_bytes()
    except OSError as exc:
        return rig_error("Could not read Skin weight file", reason=str(exc))
    digest = hashlib.sha256(encoded).hexdigest()
    if expected_sha256 is not None:
        if not _valid_digest(expected_sha256):
            return rig_error("expected_sha256 must be 64 lowercase hexadecimal characters")
        if digest != expected_sha256:
            return rig_error("Skin weight file checksum did not match", actual_sha256=digest)
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return rig_error("Skin weight file is not valid UTF-8 JSON", reason=str(exc))
    if not isinstance(payload, dict) or payload.get("schema") != SKIN_WEIGHTS_SCHEMA:
        return rig_error("Skin weight file uses an unsupported schema")
    weights = payload.get("weights")
    if not isinstance(weights, dict) or not isinstance(weights.get("vertices"), list):
        return rig_error("Skin weight file does not contain typed vertex weights")
    result = set_skin_weights(
        runtime,
        node_name=node_name,
        handle=handle,
        modifier_name=modifier_name,
        vertices=weights["vertices"],
    )
    if result.get("success"):
        result["data"]["input_path"] = str(path)
        result["data"]["sha256"] = digest
    return result


def _validated_path(value: str, *, must_exist: bool) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        return None, rig_error("Skin weight path must contain 1 to 4096 characters")
    unresolved = Path(value).expanduser()
    if not unresolved.is_absolute() or unresolved.suffix.lower() != ".json":
        return None, rig_error("Skin weight path must be an absolute .json path")
    if unresolved.is_symlink():
        return None, rig_error("Skin weight path must not be a symlink")
    path = unresolved.resolve()
    if must_exist:
        if not path.is_file() or path.is_symlink():
            return None, rig_error("Skin weight input must be an existing non-symlink file")
    elif not path.parent.is_dir() or path.parent.is_symlink() or path.is_symlink():
        return None, rig_error("Skin weight output parent must be an existing non-symlink directory")
    return path, None


def _valid_digest(value: str) -> bool:
    return len(value) == 64 and value == value.lower() and all(char in "0123456789abcdef" for char in value)
