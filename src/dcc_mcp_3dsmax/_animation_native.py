"""Native 3ds Max controller readback and tangent helpers."""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

TANGENTS = {"auto", "bezier", "linear", "step"}


def native_curve(
    runtime: Any,
    node: Any,
    property_name: str,
    read_transform: Callable[[Any, Any, float, str], List[float]],
) -> Optional[Dict[str, Any]]:
    """Read one transform curve from native top-level or component controllers."""
    get_controller = getattr(runtime, "getPropertyController", None)
    get_count = getattr(runtime, "numKeys", None)
    get_time = getattr(runtime, "getKeyTime", None)
    get_key = getattr(runtime, "getKey", None)
    prs = getattr(node, "controller", None)
    if not all(callable(value) for value in (get_controller, get_count, get_time, get_key)) or prs is None:
        return None
    name = getattr(runtime, "Name", str)
    try:
        top = get_controller(prs, name(property_name))
    except Exception:  # noqa: BLE001
        return None
    if top is None:
        return None
    controllers: List[Tuple[Optional[int], Any]] = []
    try:
        top_count = int(get_count(top) or 0)
    except Exception:  # noqa: BLE001
        top_count = 0
    if top_count > 0:
        controllers.append((None, top))
    else:
        get_names = getattr(runtime, "getPropNames", None)
        if callable(get_names):
            try:
                sub_names = list(get_names(top))
            except Exception:  # noqa: BLE001
                sub_names = []
            for index, sub_name in enumerate(sub_names):
                try:
                    controller = get_controller(top, sub_name)
                    count = int(get_count(controller) or 0) if controller is not None else 0
                except Exception:  # noqa: BLE001
                    continue
                if count > 0:
                    controllers.append((_axis_index(sub_name, fallback=index), controller))
    if not controllers:
        return None

    rows: Dict[float, Dict[str, Any]] = {}
    for axis, controller in controllers:
        try:
            count = int(get_count(controller) or 0)
        except Exception:  # noqa: BLE001
            continue
        for key_index in range(1, count + 1):
            try:
                raw_time = get_time(controller, key_index)
                key = get_key(controller, key_index)
            except Exception:  # noqa: BLE001
                continue
            frame = frame_value(runtime, raw_time)
            row = rows.setdefault(
                frame,
                {
                    "t": frame,
                    "v": list(read_transform(runtime, node, frame, property_name)),
                    "in": "auto",
                    "out": "auto",
                },
            )
            value = getattr(key, "value", None)
            vector = _vector_value(value)
            if axis is None and vector is not None:
                row["v"] = vector
            elif axis is not None and _finite_number(value):
                while len(row["v"]) < 3:
                    row["v"].append(0.0)
                row["v"][axis] = float(value)
            row["in"] = tangent_name(getattr(key, "inTangentType", getattr(key, "in_tangent", "auto")))
            row["out"] = tangent_name(getattr(key, "outTangentType", getattr(key, "out_tangent", "auto")))
    if not rows:
        return None
    ordered = [rows[frame] for frame in sorted(rows)]
    return {
        "target": "{}.{}".format(str(getattr(node, "name", "")), property_name),
        "keys": ordered,
        "pre_infinity": _infinity_name(getattr(top, "beforeORT", "constant")),
        "post_infinity": _infinity_name(getattr(top, "afterORT", "constant")),
        "key_count": len(ordered),
    }


def set_native_key_tangents(runtime: Any, node: Any, key_spec: Dict[str, Any]) -> Optional[bool]:
    """Apply in/out tangent types and verify native MAXKey readback when available."""
    get_controller = getattr(runtime, "getPropertyController", None)
    get_count = getattr(runtime, "numKeys", None)
    get_time = getattr(runtime, "getKeyTime", None)
    get_key = getattr(runtime, "getKey", None)
    prs = getattr(node, "controller", None)
    if not all(callable(value) for value in (get_controller, get_count, get_time, get_key)) or prs is None:
        return None
    name = getattr(runtime, "Name", str)
    try:
        top = get_controller(prs, name(key_spec["property"]))
    except Exception:  # noqa: BLE001
        return None
    if top is None:
        return None
    controllers = [top]
    get_names = getattr(runtime, "getPropNames", None)
    if callable(get_names):
        try:
            subcontrollers = [get_controller(top, sub_name) for sub_name in get_names(top)]
        except Exception:  # noqa: BLE001
            subcontrollers = []
        controllers = [controller for controller in subcontrollers if controller is not None] or controllers
    set_in = getattr(runtime, "setInTangentType", None)
    set_out = getattr(runtime, "setOutTangentType", None)
    native_in = _native_tangent_name(key_spec["in_tangent"])
    native_out = _native_tangent_name(key_spec["out_tangent"])
    changed = 0
    for controller in controllers:
        try:
            count = int(get_count(controller) or 0)
        except Exception:  # noqa: BLE001
            continue
        for key_index in range(1, count + 1):
            try:
                frame = frame_value(runtime, get_time(controller, key_index))
            except Exception:  # noqa: BLE001
                continue
            if not math.isclose(frame, key_spec["frame"], rel_tol=1e-6, abs_tol=1e-6):
                continue
            native_key = get_key(controller, key_index)
            if callable(set_in) and callable(set_out):
                set_in(native_key, name(native_in))
                set_out(native_key, name(native_out))
            else:
                native_key.inTangentType = name(native_in)
                native_key.outTangentType = name(native_out)
            verify_key = get_key(controller, key_index)
            if tangent_name(getattr(verify_key, "inTangentType", native_in)) != key_spec["in_tangent"]:
                return False
            if tangent_name(getattr(verify_key, "outTangentType", native_out)) != key_spec["out_tangent"]:
                return False
            changed += 1
    return True if changed else None


def frame_value(runtime: Any, value: Any) -> float:
    frame = getattr(value, "frame", None)
    if frame is not None:
        return float(frame)
    ticks = float(value)
    ticks_per_frame = float(getattr(runtime, "ticksPerFrame", 160) or 160)
    return ticks / ticks_per_frame


def tangent_name(value: Any) -> str:
    text = str(value).lower().replace("#", "").strip()
    return {"smooth": "auto", "custom": "bezier"}.get(text, text if text in TANGENTS else "auto")


def _axis_index(value: Any, *, fallback: int) -> int:
    text = str(value).lower().replace("#", "").replace("_", " ").strip()
    for index, axis in enumerate(("x", "y", "z")):
        if text == axis or text.startswith(axis + " ") or text.endswith(" " + axis):
            return index
    return min(fallback, 2)


def _vector_value(value: Any) -> Optional[List[float]]:
    if isinstance(value, (list, tuple)) and len(value) >= 3 and all(_finite_number(item) for item in value[:3]):
        return [float(item) for item in value[:3]]
    for attrs in (("x", "y", "z"), ("X", "Y", "Z")):
        try:
            return [float(getattr(value, attr)) for attr in attrs]
        except (AttributeError, TypeError, ValueError):
            continue
    return None


def _native_tangent_name(value: str) -> str:
    return {"auto": "smooth", "bezier": "custom"}.get(value, value)


def _infinity_name(value: Any) -> str:
    text = str(value).lower().replace("#", "").strip()
    return text if text in {"constant", "cycle", "linear", "loop", "pingpong", "relative_repeat"} else "constant"


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
