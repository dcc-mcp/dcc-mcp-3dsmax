"""Create a native Lathe object from a bounded profile."""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax.api import get_runtime, with_max


def _validation_error(message: str) -> dict:
    return {"success": False, "status": "error", "message": message, "data": {}}


def _rollback(runtime, node) -> bool:
    delete = getattr(runtime, "delete", None)
    if not callable(delete):
        return False
    try:
        delete(node)
    except Exception:  # noqa: BLE001 - rollback must not hide the readback failure.
        return False
    return True


def _validated_inputs(
    profile_points: Sequence[Sequence[float]],
    name: Optional[str],
    degrees: float,
    segments: int,
    weld_core: bool,
    flip_normals: bool,
) -> Tuple[List[Tuple[float, float]], Optional[str], float, int, bool, bool]:
    if isinstance(profile_points, (str, bytes)) or not isinstance(profile_points, Sequence):
        raise ValueError("profile_points must be an array of radius/height pairs")
    if not 2 <= len(profile_points) <= 256:
        raise ValueError("profile_points must contain between 2 and 256 pairs")

    normalized_points = []
    for point in profile_points:
        if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
            raise ValueError("profile_points entries must contain exactly radius and height")
        if isinstance(point[0], bool) or isinstance(point[1], bool):
            raise ValueError("profile_points values must be finite numbers")
        try:
            radius, height = float(point[0]), float(point[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("profile_points values must be finite numbers") from exc
        if not math.isfinite(radius) or not math.isfinite(height) or radius < 0.0:
            raise ValueError("profile_points require finite heights and non-negative finite radii")
        normalized_points.append((radius, height))

    if name is not None and (not isinstance(name, str) or not 1 <= len(name) <= 255 or "\x00" in name):
        raise ValueError("name must be a non-empty string of at most 255 characters")
    if isinstance(degrees, bool):
        raise ValueError("degrees must be a number greater than 0 and at most 360")
    try:
        normalized_degrees = float(degrees)
    except (TypeError, ValueError) as exc:
        raise ValueError("degrees must be a number greater than 0 and at most 360") from exc
    if not math.isfinite(normalized_degrees) or not 0.0 < normalized_degrees <= 360.0:
        raise ValueError("degrees must be a number greater than 0 and at most 360")
    if isinstance(segments, bool) or not isinstance(segments, int) or not 3 <= segments <= 256:
        raise ValueError("segments must be an integer between 3 and 256")
    if not isinstance(weld_core, bool):
        raise ValueError("weld_core must be a boolean")
    if not isinstance(flip_normals, bool):
        raise ValueError("flip_normals must be a boolean")
    return normalized_points, name, normalized_degrees, segments, weld_core, flip_normals


@with_max
def main(
    profile_points: Sequence[Sequence[float]],
    name: Optional[str] = None,
    degrees: float = 360.0,
    segments: int = 16,
    weld_core: bool = True,
    flip_normals: bool = False,
) -> dict:
    """Create an XZ-plane spline with a native Lathe modifier."""
    try:
        points, name, degrees, segments, weld_core, flip_normals = _validated_inputs(
            profile_points, name, degrees, segments, weld_core, flip_normals
        )
    except ValueError as exc:
        return _validation_error(str(exc))

    rt = get_runtime()
    shape = None
    stage = "create_native_lathe"
    try:
        shape = rt.SplineShape()
        rt.addNewSpline(shape)
        for radius, height in points:
            rt.addKnot(
                shape,
                1,
                rt.Name("corner"),
                rt.Name("line"),
                rt.Point3(float(radius), 0.0, float(height)),
            )
        rt.updateShape(shape)

        modifier = rt.Lathe()
        modifier.degrees = float(degrees)
        modifier.segs = int(segments)
        modifier.weldCore = bool(weld_core)
        modifier.flipNormals = bool(flip_normals)
        rt.addModifier(shape, modifier)

        if name is not None:
            shape.name = name

        stage = "readback_native_lathe"
        node_name = str(shape.name)
        node = rt.getNodeByName(node_name)
        readback_modifiers = list(getattr(node, "modifiers", [])) if node is not None else []
        point_count = int(rt.numKnots(node, 1)) if node is not None else 0
        readback_modifier = readback_modifiers[0] if len(readback_modifiers) == 1 else None
        readback_matches = (
            node is not None
            and int(getattr(node, "handle", -1)) == int(getattr(shape, "handle", -2))
            and point_count == len(points)
            and readback_modifier is not None
            and math.isclose(float(readback_modifier.degrees), degrees, rel_tol=0.0, abs_tol=1e-6)
            and int(readback_modifier.segs) == segments
            and bool(readback_modifier.weldCore) is weld_core
            and bool(readback_modifier.flipNormals) is flip_normals
        )
    except Exception as exc:  # noqa: BLE001 - host failures return a typed, rolled-back envelope.
        rolled_back = shape is not None and _rollback(rt, shape)
        return {
            "success": False,
            "status": "error",
            "message": "Lathe profile failed during native host execution",
            "data": {
                "failure_stage": stage,
                "exception_type": type(exc).__name__,
                "rolled_back": rolled_back,
            },
        }

    if not readback_matches:
        rolled_back = _rollback(rt, shape)
        return {
            "success": False,
            "status": "error",
            "message": "Lathe profile did not pass native scene readback",
            "data": {
                "failure_stage": "readback_native_lathe",
                "node_name": node_name,
                "profile_point_count": point_count,
                "rolled_back": rolled_back,
            },
        }

    return {
        "success": True,
        "status": "success",
        "message": "Created lathed profile: {}".format(node_name),
        "data": {
            "node_name": node_name,
            "object_id": int(shape.handle) if hasattr(shape, "handle") else None,
            "profile_plane": "xz",
            "profile_point_count": point_count,
            "degrees": float(readback_modifier.degrees),
            "segments": int(readback_modifier.segs),
            "weld_core": bool(readback_modifier.weldCore),
            "flip_normals": bool(readback_modifier.flipNormals),
        },
    }
