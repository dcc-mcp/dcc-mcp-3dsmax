"""Configure native system units before creating scene content."""

from __future__ import annotations

import math
from typing import Any, Dict

from dcc_mcp_3dsmax._scene_lifecycle import _string_value
from dcc_mcp_3dsmax.api import get_runtime, with_max

SYSTEM_UNITS = ("inches", "feet", "miles", "millimeters", "centimeters", "meters", "kilometers")


def _snapshot(units: Any) -> Dict[str, Any]:
    return {
        "system_unit": _string_value(units.SystemType).lower(),
        "system_scale": float(units.SystemScale),
    }


def _matches(actual: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    return actual["system_unit"] == expected["system_unit"] and math.isclose(
        actual["system_scale"], expected["system_scale"], rel_tol=1e-6, abs_tol=0.0
    )


@with_max
def main(system_unit: str, system_scale: float = 1.0) -> Dict[str, Any]:
    """Set and verify system units on an empty scene without rescaling content."""
    if system_unit not in SYSTEM_UNITS:
        return {"success": False, "message": "Unsupported system_unit", "data": {"failure_stage": "precondition"}}
    if (
        isinstance(system_scale, bool)
        or not isinstance(system_scale, (int, float))
        or not math.isfinite(system_scale)
        or system_scale <= 0
    ):
        return {
            "success": False,
            "message": "system_scale must be finite and positive",
            "data": {"failure_stage": "precondition"},
        }
    rt = get_runtime()
    try:
        if any(True for _ in rt.objects):
            return {
                "success": False,
                "message": "System units can only be configured on an empty scene",
                "data": {"failure_stage": "precondition", "failure_reason": "scene_not_empty"},
            }
        units = rt.units
        before = _snapshot(units)
        if (
            before["system_unit"] not in SYSTEM_UNITS
            or not math.isfinite(before["system_scale"])
            or before["system_scale"] <= 0
        ):
            raise ValueError("Current system units cannot be restored safely")
        requested_type = rt.Name(system_unit)
        original_type = rt.Name(before["system_unit"])
    except Exception as exc:
        return {"success": False, "message": str(exc), "data": {"failure_stage": "snapshot"}}
    requested = {"system_unit": system_unit, "system_scale": float(system_scale)}
    after = None
    try:
        units.SystemType = requested_type
        units.SystemScale = system_scale
        after = _snapshot(units)
        if not _matches(after, requested):
            raise RuntimeError("Native system-unit readback differs from the request")
    except Exception as exc:
        restore_errors = []
        for field, value in (("SystemScale", before["system_scale"]), ("SystemType", original_type)):
            try:
                setattr(units, field, value)
            except Exception as restore_exc:
                restore_errors.append({"field": field, "error": str(restore_exc)})
        restored = None
        try:
            restored = _snapshot(units)
        except Exception as restore_exc:
            restore_errors.append({"field": "readback", "error": str(restore_exc)})
        return {
            "success": False,
            "message": str(exc),
            "data": {
                "failure_stage": "write_or_verify",
                "before": before,
                "requested": requested,
                "after": after,
                "restored": restored,
                "rollback_verified": restored is not None and _matches(restored, before),
                "restore_errors": restore_errors,
            },
        }
    return {
        "success": True,
        "message": "Configured and verified empty-scene system units",
        "data": {"before": before, "after": after, "verified": True, "rescaled_objects": False},
    }
