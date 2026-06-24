"""Helpers for 3ds Max asset import skill scripts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._geometry_io import import_geometry_file, resolve_import_file
from dcc_mcp_3dsmax._scene_utils import iter_scene_nodes, node_bounding_box, node_identity

SUPPORTED_ASSET_IMPORT_FORMATS = {"fbx", "obj", "3ds"}


def import_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def import_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def import_asset_to_scene(
    runtime: Any,
    descriptor: Any,
    *,
    group_name: Optional[str] = None,
    name_prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """Import one asset descriptor into the current scene."""
    payload = _descriptor_payload(descriptor)
    path = Path(str(payload.get("path", ""))).expanduser()
    if not path.name:
        return import_error("Asset descriptor is missing a path", descriptor=payload)

    format_name = _descriptor_format(payload, path)
    path, error = resolve_import_file(str(path), expected_format=format_name)
    if error is not None:
        error["data"]["descriptor"] = payload
        return error
    if format_name not in SUPPORTED_ASSET_IMPORT_FORMATS:
        return import_error(
            "Unsupported asset import format",
            descriptor=payload,
            supported_formats=sorted(SUPPORTED_ASSET_IMPORT_FORMATS),
        )

    before = _node_keys(iter_scene_nodes(runtime))
    result = import_geometry_file(runtime, path, format_name=format_name, fbx_options={})
    if not result.get("success"):
        result["data"]["descriptor"] = payload
        return result

    created_nodes = [node for node in iter_scene_nodes(runtime) if _node_key(node) not in before]
    prefix = _safe_name(name_prefix or group_name or payload.get("name") or path.stem)
    renamed = _rename_nodes(runtime, created_nodes, prefix)
    bounds = _combined_bounds(created_nodes)
    return import_success(
        "Imported asset into scene",
        descriptor=payload,
        format=format_name,
        group_name=group_name,
        imported_nodes=[node_identity(node) for node in created_nodes],
        imported_node_names=[str(getattr(node, "name", "")) for node in created_nodes],
        renamed_nodes=renamed,
        bounding_box=bounds,
        created_count=len(created_nodes),
        warnings=list(result["data"].get("warnings", [])),
    )


def _descriptor_payload(descriptor: Any) -> Dict[str, Any]:
    if isinstance(descriptor, Mapping):
        return {str(key): value for key, value in descriptor.items()}
    payload = {}
    for key in ("id", "name", "path", "format", "size_bytes", "metadata"):
        if hasattr(descriptor, key):
            payload[key] = getattr(descriptor, key)
    return payload


def _descriptor_format(payload: Mapping[str, Any], path: Path) -> str:
    fmt = str(payload.get("format") or path.suffix.lstrip(".")).lower()
    return fmt


def _node_key(node: Any) -> Tuple[Optional[int], str]:
    handle = getattr(node, "handle", None)
    try:
        object_id = int(handle) if handle is not None else None
    except (TypeError, ValueError):
        object_id = None
    return object_id, str(getattr(node, "name", ""))


def _node_keys(nodes: Iterable[Any]) -> set:
    return {_node_key(node) for node in nodes}


def _safe_name(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value).strip())
    return text.strip("_") or "asset"


def _rename_nodes(runtime: Any, nodes: Sequence[Any], prefix: str) -> List[Dict[str, Any]]:
    renamed = []
    for index, node in enumerate(nodes, start=1):
        if len(nodes) == 1:
            new_name = prefix
        else:
            new_name = "{}_{:02d}".format(prefix, index)
        old_name = str(getattr(node, "name", ""))
        try:
            setattr(node, "name", new_name)
        except Exception:  # noqa: BLE001
            pass
        renamed.append({"from": old_name, "to": str(getattr(node, "name", new_name))})
    return renamed


def _combined_bounds(nodes: Sequence[Any]) -> Optional[Dict[str, Any]]:
    bounds = [node_bounding_box(node) for node in nodes]
    points = [box for box in bounds if box.get("min") and box.get("max")]
    if not points:
        return None
    mins = [point[:] for point in (box["min"] for box in points)]
    maxs = [point[:] for point in (box["max"] for box in points)]
    return {
        "min": [min(point[i] for point in mins) for i in range(3)],
        "max": [max(point[i] for point in maxs) for i in range(3)],
    }
