"""Helpers for 3ds Max asset-readiness validation skill scripts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from dcc_mcp_3dsmax._scene_utils import iter_scene_nodes, node_identity, point3_to_list, resolve_node_objects

VALIDATORS = (
    "naming",
    "transforms",
    "pivots",
    "mesh_topology",
    "smoothing_groups",
    "material_assignments",
    "texture_paths",
    "uv_channels",
    "uv_overlaps",
)


def validation_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def validation_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def resolve_validation_targets(
    runtime: Any,
    *,
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Resolve validation targets; default to all scene nodes."""
    if use_selection:
        try:
            selected = list(runtime.selection)
        except Exception:  # noqa: BLE001
            selected = []
        if not selected:
            return validation_error("Current selection is empty", objects=[])
        return {"success": True, "status": "success", "message": "Resolved selected nodes", "objects": selected}
    if node_names or handles:
        result = resolve_node_objects(runtime, node_names=node_names, handles=handles)
        if not result.get("success"):
            return validation_error(result["message"], errors=result.get("errors", []), objects=[])
        result["status"] = "success"
        return result
    nodes = iter_scene_nodes(runtime)
    if not nodes:
        return validation_error("Scene contains no nodes", objects=[])
    return {"success": True, "status": "success", "message": "Resolved scene nodes", "objects": nodes}


def response_for(validator: str, checks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Wrap validator checks in a structured report."""
    return validation_success(
        "Ran {} validation".format(validator),
        validator=validator,
        summary=summarize_checks(checks),
        checks=list(checks),
    )


def summarize_checks(checks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize pass/fail/warning counts."""
    counts = {"passed": 0, "failed": 0, "warnings": 0}
    for check in checks:
        status = check.get("status")
        if status == "passed":
            counts["passed"] += 1
        elif status == "failed":
            counts["failed"] += 1
        elif status == "warning":
            counts["warnings"] += 1
    overall = "failed" if counts["failed"] else "warning" if counts["warnings"] else "passed"
    return {"status": overall, "total": len(checks), **counts}


def run_validators(
    nodes: Sequence[Any],
    *,
    validators: Optional[Sequence[str]] = None,
    required_uv_channels: Optional[Sequence[int]] = None,
    naming_pattern: str = r"^[A-Za-z][A-Za-z0-9_]*$",
) -> Dict[str, Any]:
    """Run selected validators and return aggregate details."""
    selected = list(validators or VALIDATORS)
    unknown = [name for name in selected if name not in VALIDATORS]
    if unknown:
        return validation_error("Unknown validators requested", validators=unknown)

    all_checks: List[Dict[str, Any]] = []
    by_validator = []
    for validator in selected:
        checks = _run_one_validator(
            validator,
            nodes,
            required_uv_channels=required_uv_channels,
            naming_pattern=naming_pattern,
        )
        all_checks.extend(checks)
        by_validator.append({"validator": validator, "summary": summarize_checks(checks)})

    return validation_success(
        "Ran asset-readiness validators",
        validators=by_validator,
        summary=summarize_checks(all_checks),
        checks=all_checks,
    )


def validate_naming(
    nodes: Sequence[Any], *, pattern: str = r"^[A-Za-z][A-Za-z0-9_]*$", allow_spaces: bool = False
) -> List[Dict[str, Any]]:
    """Validate node names against a public pattern and duplicate check."""
    regex = re.compile(pattern)
    seen: Dict[str, int] = {}
    for node in nodes:
        name = str(getattr(node, "name", ""))
        seen[name] = seen.get(name, 0) + 1
    checks = []
    for node in nodes:
        name = str(getattr(node, "name", ""))
        problems = []
        if not regex.match(name):
            problems.append("does not match pattern")
        if not allow_spaces and " " in name:
            problems.append("contains spaces")
        if seen.get(name, 0) > 1:
            problems.append("is duplicated")
        checks.append(
            _check(
                "naming",
                node,
                "failed" if problems else "passed",
                "Node name is ready" if not problems else "Node name {}".format(", ".join(problems)),
                "Rename the node using the requested pattern and a unique name.",
                {"pattern": pattern, "name": name},
            )
        )
    return checks


def validate_transforms(nodes: Sequence[Any], *, tolerance: float = 0.001) -> List[Dict[str, Any]]:
    """Validate zeroed position/rotation and unit scale."""
    checks = []
    for node in nodes:
        position = _vector_value(getattr(node, "position", None), [0.0, 0.0, 0.0])
        rotation = _vector_value(getattr(node, "rotation", None), [0.0, 0.0, 0.0])
        scale = _vector_value(getattr(node, "scale", None), [1.0, 1.0, 1.0])
        problems = []
        if not _near(position, [0.0, 0.0, 0.0], tolerance):
            problems.append("position is not zeroed")
        if not _near(rotation, [0.0, 0.0, 0.0], tolerance):
            problems.append("rotation is not zeroed")
        if not _near(scale, [1.0, 1.0, 1.0], tolerance):
            problems.append("scale is not one")
        checks.append(
            _check(
                "transforms",
                node,
                "failed" if problems else "passed",
                "Transforms are zeroed" if not problems else "; ".join(problems),
                "Freeze/reset transforms or move mesh data under a clean parent.",
                {"position": position, "rotation": rotation, "scale": scale, "tolerance": tolerance},
            )
        )
    return checks


def validate_pivots(
    nodes: Sequence[Any], *, mode: str = "bounds_center", tolerance: float = 0.001
) -> List[Dict[str, Any]]:
    """Validate pivot placement against origin or bounds center."""
    checks = []
    for node in nodes:
        pivot = _vector_value(getattr(node, "pivot", None), [0.0, 0.0, 0.0])
        expected = [0.0, 0.0, 0.0]
        if mode == "bounds_center":
            minimum = point3_to_list(getattr(node, "min", None))
            maximum = point3_to_list(getattr(node, "max", None))
            if minimum is not None and maximum is not None:
                expected = [(minimum[index] + maximum[index]) / 2.0 for index in range(3)]
        status = "passed" if _near(pivot, expected, tolerance) else "failed"
        checks.append(
            _check(
                "pivots",
                node,
                status,
                "Pivot placement is ready" if status == "passed" else "Pivot is not at {}".format(mode),
                "Move the pivot to the expected placement for handoff.",
                {"pivot": pivot, "expected": expected, "mode": mode, "tolerance": tolerance},
            )
        )
    return checks


def validate_mesh_topology(
    nodes: Sequence[Any],
    *,
    max_open_edges: int = 0,
    max_isolated_vertices: int = 0,
    max_ngons: int = 0,
) -> List[Dict[str, Any]]:
    """Validate topology counts such as open edges, isolated vertices, and face makeup."""
    checks = []
    for node in nodes:
        open_edges = _int_attr(node, ("open_edge_count", "open_edges"), 0)
        isolated_vertices = _int_attr(node, ("isolated_vertex_count", "isolated_vertices"), 0)
        triangle_count = _int_attr(node, ("triangle_count", "triangles"), 0)
        quad_count = _int_attr(node, ("quad_count", "quads"), 0)
        ngon_count = _int_attr(node, ("ngon_count", "ngons", "non_quad_count"), 0)
        problems = []
        if open_edges > max_open_edges:
            problems.append("open edges exceed limit")
        if isolated_vertices > max_isolated_vertices:
            problems.append("isolated vertices exceed limit")
        if ngon_count > max_ngons:
            problems.append("non-quad faces exceed limit")
        checks.append(
            _check(
                "mesh_topology",
                node,
                "failed" if problems else "passed",
                "Mesh topology is ready" if not problems else "; ".join(problems),
                "Repair open edges, remove isolated vertices, or retopologize non-quad faces.",
                {
                    "open_edges": open_edges,
                    "isolated_vertices": isolated_vertices,
                    "triangle_count": triangle_count,
                    "quad_count": quad_count,
                    "ngon_count": ngon_count,
                },
            )
        )
    return checks


def validate_smoothing_groups(nodes: Sequence[Any]) -> List[Dict[str, Any]]:
    """Validate smoothing-group coverage."""
    checks = []
    for node in nodes:
        groups = getattr(node, "smoothing_groups", None)
        if not groups:
            checks.append(
                _check(
                    "smoothing_groups",
                    node,
                    "warning",
                    "No smoothing group data was available",
                    "Assign smoothing groups or run a mesh topology inspection in 3ds Max.",
                    {},
                )
            )
            continue
        values = list(groups.values()) if isinstance(groups, dict) else list(groups)
        missing = sum(1 for value in values if int(value or 0) == 0)
        checks.append(
            _check(
                "smoothing_groups",
                node,
                "failed" if missing else "passed",
                "Smoothing groups are assigned" if not missing else "Some faces have no smoothing group",
                "Assign smoothing groups to faces with value 0.",
                {"face_count": len(values), "missing_count": missing},
            )
        )
    return checks


def validate_material_assignments(nodes: Sequence[Any]) -> List[Dict[str, Any]]:
    """Validate that each target has a material assignment."""
    checks = []
    for node in nodes:
        material = getattr(node, "material", None)
        checks.append(
            _check(
                "material_assignments",
                node,
                "passed" if material is not None else "failed",
                "Material is assigned" if material is not None else "Material is missing",
                "Assign a material before export or handoff.",
                {"material": _material_name(material)},
            )
        )
    return checks


def validate_texture_paths(nodes: Sequence[Any]) -> List[Dict[str, Any]]:
    """Validate referenced texture file availability."""
    checks = []
    for node in nodes:
        paths = _texture_paths(getattr(node, "material", None))
        missing = [path for path in paths if not Path(path).is_file()]
        if not paths:
            status = "warning"
            message = "No texture paths were referenced"
        elif missing:
            status = "failed"
            message = "One or more texture paths are missing"
        else:
            status = "passed"
            message = "Texture paths are available"
        checks.append(
            _check(
                "texture_paths",
                node,
                status,
                message,
                "Relink missing bitmap files or use accessible project-relative paths.",
                {"paths": paths, "missing": missing},
            )
        )
    return checks


def validate_uv_channels(
    nodes: Sequence[Any], *, required_channels: Optional[Sequence[int]] = None
) -> List[Dict[str, Any]]:
    """Validate required UV channels."""
    required = [int(channel) for channel in (required_channels or [1])]
    checks = []
    for node in nodes:
        channels = _uv_channels(node)
        missing = [channel for channel in required if channel not in channels]
        checks.append(
            _check(
                "uv_channels",
                node,
                "failed" if missing else "passed",
                "Required UV channels are present" if not missing else "Required UV channels are missing",
                "Create or copy the required UV channels before export.",
                {"channels": channels, "required": required, "missing": missing},
            )
        )
    return checks


def validate_uv_overlaps(nodes: Sequence[Any], *, max_overlap_count: int = 0) -> List[Dict[str, Any]]:
    """Validate UV overlap counts."""
    checks = []
    for node in nodes:
        overlap_count = _int_attr(node, ("uv_overlap_count", "uv_overlaps"), 0)
        checks.append(
            _check(
                "uv_overlaps",
                node,
                "failed" if overlap_count > max_overlap_count else "passed",
                "UV overlaps are within limit" if overlap_count <= max_overlap_count else "UV overlaps exceed limit",
                "Repack or separate overlapping UV shells.",
                {"overlap_count": overlap_count, "max_overlap_count": max_overlap_count},
            )
        )
    return checks


def _run_one_validator(
    validator: str,
    nodes: Sequence[Any],
    *,
    required_uv_channels: Optional[Sequence[int]],
    naming_pattern: str,
) -> List[Dict[str, Any]]:
    if validator == "naming":
        return validate_naming(nodes, pattern=naming_pattern)
    if validator == "transforms":
        return validate_transforms(nodes)
    if validator == "pivots":
        return validate_pivots(nodes)
    if validator == "mesh_topology":
        return validate_mesh_topology(nodes)
    if validator == "smoothing_groups":
        return validate_smoothing_groups(nodes)
    if validator == "material_assignments":
        return validate_material_assignments(nodes)
    if validator == "texture_paths":
        return validate_texture_paths(nodes)
    if validator == "uv_channels":
        return validate_uv_channels(nodes, required_channels=required_uv_channels)
    if validator == "uv_overlaps":
        return validate_uv_overlaps(nodes)
    return []


def _check(
    validator: str,
    node: Any,
    status: str,
    message: str,
    hint: str,
    details: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "validator": validator,
        "status": status,
        "node": node_identity(node),
        "message": message,
        "hint": hint,
        "details": details,
    }


def _vector_value(value: Any, default: Sequence[float]) -> List[float]:
    vector = point3_to_list(value)
    if vector is None:
        vector = point3_to_list(getattr(value, "value", None))
    return vector or [float(item) for item in default]


def _near(left: Sequence[float], right: Sequence[float], tolerance: float) -> bool:
    return all(abs(float(left[index]) - float(right[index])) <= tolerance for index in range(3))


def _int_attr(node: Any, names: Sequence[str], default: int) -> int:
    for name in names:
        value = getattr(node, name, None)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return default


def _material_name(material: Any) -> Optional[str]:
    if material is None:
        return None
    return str(getattr(material, "name", "") or type(material).__name__)


def _texture_paths(material: Any) -> List[str]:
    if material is None:
        return []
    paths: List[str] = []
    for attr in ("texture_paths", "textures", "bitmap_paths"):
        value = getattr(material, attr, None)
        if isinstance(value, str):
            paths.append(value)
        elif isinstance(value, Iterable):
            for item in value:
                if isinstance(item, str):
                    paths.append(item)
                else:
                    filename = getattr(item, "filename", None)
                    if filename:
                        paths.append(str(filename))
    return paths


def _uv_channels(node: Any) -> List[int]:
    value = getattr(node, "uv_channels", None)
    if isinstance(value, dict):
        raw_channels = value.keys()
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        raw_channels = value
    else:
        raw_channels = []
    channels = []
    for channel in raw_channels:
        try:
            channels.append(int(channel))
        except (TypeError, ValueError):
            continue
    return sorted(set(channels))
