"""Helpers for 3ds Max spline / curve modelling skill scripts.

The helpers split into three groups:

* **Pure validation** - runs anywhere, including the offline unit tests.
* **Host access** - talks to ``pymxs`` only through the runtime object that is
  passed in, so a test can substitute a fake.
* **Verified writes** - every write is read back. A value the host did not
  accept is reported, never swallowed, because a call that reports success
  while the scene changed differently is worse than a call that fails.

Nothing here invents a new execution channel: knots are read and written with
the same ``numSplines`` / ``numKnots`` / ``getKnotPoint`` family the rest of the
adapter uses, and object creation goes through the runtime constructors.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._scene_utils import (
    iter_scene_nodes,
    json_safe,
    node_identity,
    resolve_node_object,
)

# Upper bound on knots per spline. A spline is a small authoring object; an
# unbounded list would let one call stall the main thread.
MAX_KNOTS = 256

# Upper bound on splines inside one shape.
MAX_SPLINES = 64

# Tolerance used when a world-space point is compared against its readback.
# 3ds Max stores coordinates as 32-bit floats, so an exact compare would
# reject a correct write on any value above ~1e6.
POSITION_TOLERANCE = 1e-3

KNOT_TYPES = ("corner", "smooth", "bezier", "bezierCorner")

CURVE_MODEL_PROPERTY = "dcc_mcp_curve_model"
LOFT_PARAM_PROPERTY = "dcc_mcp_loft_params"

PROFILE_KINDS = ("rounded_rect", "rectangle", "circle", "polyline")


class CurveError(Exception):
    """A curve operation that must fail the call rather than warn."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


def curve_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def curve_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


# ── Validation ─────────────────────────────────────────────────────────


def _finite_number(value: Any, name: str) -> float:
    """Return ``value`` as a finite float or raise :class:`ValueError`."""
    if isinstance(value, bool) or value is None:
        raise ValueError("{} must be a finite number".format(name))
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("{} must be a finite number".format(name)) from exc
    if not math.isfinite(number):
        raise ValueError("{} must be a finite number".format(name))
    return number


def validated_vectors(
    points: Any,
    name: str,
    *,
    min_items: int = 2,
    max_items: int = MAX_KNOTS,
    item_length: int = 3,
) -> List[List[float]]:
    """Validate a sequence of XYZ triples and return them as float lists."""
    if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
        raise ValueError("{} must be an array of {}D points".format(name, item_length))
    if not min_items <= len(points) <= max_items:
        raise ValueError(
            "{} must contain between {} and {} points".format(name, min_items, max_items)
        )
    normalized: List[List[float]] = []
    for index, point in enumerate(points):
        label = "{}[{}]".format(name, index)
        if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != item_length:
            raise ValueError("{} must contain exactly {} numbers".format(label, item_length))
        normalized.append([_finite_number(component, label) for component in point])
    return normalized


def validated_name(value: Any, name: str = "name", *, required: bool = False) -> Optional[str]:
    """Validate a scene node name."""
    if value is None:
        if required:
            raise ValueError("{} is required".format(name))
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 255 or "\x00" in value:
        raise ValueError("{} must be a non-empty string of at most 255 characters".format(name))
    return value


def validated_knot_type(value: Any, name: str = "knot_type", *, default: str = "corner") -> str:
    """Validate a 3ds Max knot type name."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError("{} must be one of {}".format(name, ", ".join(KNOT_TYPES)))
    for candidate in KNOT_TYPES:
        if value.lower() == candidate.lower():
            return candidate
    raise ValueError("{} must be one of {}".format(name, ", ".join(KNOT_TYPES)))


def validated_bool(value: Any, name: str, *, default: bool = False) -> bool:
    """Validate a boolean flag without accepting truthy strings."""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError("{} must be a boolean".format(name))
    return value


def validated_int(value: Any, name: str, *, default: int, minimum: int, maximum: int) -> int:
    """Validate a bounded integer."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("{} must be an integer between {} and {}".format(name, minimum, maximum))
    if not minimum <= value <= maximum:
        raise ValueError("{} must be an integer between {} and {}".format(name, minimum, maximum))
    return value


def validated_number(
    value: Any,
    name: str,
    *,
    default: Optional[float] = None,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
    exclusive_minimum: bool = False,
) -> Optional[float]:
    """Validate a bounded finite number."""
    if value is None:
        return default
    number = _finite_number(value, name)
    if minimum is not None:
        if exclusive_minimum and number <= minimum:
            raise ValueError("{} must be greater than {}".format(name, minimum))
        if not exclusive_minimum and number < minimum:
            raise ValueError("{} must be at least {}".format(name, minimum))
    if maximum is not None and number > maximum:
        raise ValueError("{} must be at most {}".format(name, maximum))
    return number


# ── Host probing ───────────────────────────────────────────────────────


def call_first(
    owner: Any,
    names: Sequence[str],
    argument_sets: Sequence[Sequence[Any]],
    *,
    owner_label: str,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Call the first ``owner.<name>(*args)`` combination the host accepts.

    Returns ``(ok, used_name, error)``. Every candidate is tried before giving
    up, and the error names them all, so a missing capability is reported
    instead of being silently downgraded to a no-op.
    """
    attempts: List[str] = []
    for name in names:
        method = getattr(owner, name, None)
        if not callable(method):
            attempts.append("{}: not exposed".format(name))
            continue
        for args in argument_sets:
            try:
                method(*args)
            except Exception as exc:  # noqa: BLE001 - the next candidate may work.
                attempts.append("{}{}: {}".format(name, tuple(args), exc))
                continue
            return True, name, None
    return False, None, "{} exposes none of {} ({})".format(
        owner_label, ", ".join(names), "; ".join(attempts)
    )


def set_property_first(
    owner: Any,
    names: Sequence[str],
    value: Any,
    *,
    owner_label: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Set the first writable property in ``names`` and read it back.

    Returns ``(used_name, error)``. The readback is part of the contract: a
    property assignment the host silently ignores is reported as a failure
    rather than being assumed to have taken effect.
    """
    attempts: List[str] = []
    for name in names:
        if getattr(owner, name, None) is None and not hasattr(owner, name):
            attempts.append("{}: not exposed".format(name))
            continue
        try:
            setattr(owner, name, value)
        except Exception as exc:  # noqa: BLE001 - the next candidate may work.
            attempts.append("{}: {}".format(name, exc))
            continue
        try:
            written = getattr(owner, name)
        except Exception as exc:  # noqa: BLE001 - an unreadable write is unverified.
            return None, "{} accepted {} but the value could not be read back: {}".format(name, value, exc)
        if not _values_equal(written, value):
            return None, "{} kept {} instead of the requested {}".format(name, written, value)
        return name, None
    return None, "{} accepted none of {} ({})".format(owner_label, ", ".join(names), "; ".join(attempts))


def read_property_first(owner: Any, names: Sequence[str]) -> Tuple[bool, Any]:
    """Return ``(found, value)`` for the first readable property in ``names``."""
    for name in names:
        try:
            return True, getattr(owner, name)
        except Exception:  # noqa: BLE001 - probe the next candidate.
            continue
    return False, None


def _values_equal(left: Any, right: Any) -> bool:
    """Compare a written value against its readback."""
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) is bool(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if float(left) == float(right):
            return True
        return math.isclose(float(left), float(right), rel_tol=1e-6, abs_tol=1e-6)
    return left == right


# ── Coordinate mapping ─────────────────────────────────────────────────


def _point_to_list(point: Any) -> Optional[List[float]]:
    """Convert a pymxs Point3-like value into a plain XYZ list."""
    for axis in ("x", "y", "z"):
        if getattr(point, axis, None) is None:
            return None
    try:
        return [float(point.x), float(point.y), float(point.z)]
    except (TypeError, ValueError):
        return None


def object_to_world(node: Any, point: Any) -> Tuple[Optional[List[float]], Optional[str]]:
    """Map one object-space point into world space."""
    transform = getattr(node, "objectTransform", None)
    if transform is None:
        local = _point_to_list(point)
        return local, None
    try:
        world = point * transform
    except Exception as exc:  # noqa: BLE001 - an unmapped point must not be guessed.
        return None, "could not map an object-space point into world space: {}".format(exc)
    mapped = _point_to_list(world)
    if mapped is None:
        return None, "the host returned a point that is not an XYZ triple"
    return mapped, None


def world_to_object(runtime: Any, node: Any, point: Sequence[float]) -> Tuple[Optional[Any], Optional[str]]:
    """Map one world-space XYZ triple into the node's object space."""
    make_point = getattr(runtime, "Point3", None)
    if not callable(make_point):
        return None, "the host does not expose Point3"
    try:
        world_point = make_point(float(point[0]), float(point[1]), float(point[2]))
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return None, "could not build a Point3 from {}: {}".format(list(point), exc)

    transform = getattr(node, "objectTransform", None)
    if transform is None:
        return world_point, None
    inverse = getattr(runtime, "inverse", None)
    if not callable(inverse):
        return None, "the host does not expose inverse(), so world coordinates cannot be mapped"
    try:
        return world_point * inverse(transform), None
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return None, "could not invert the node transform: {}".format(exc)


# ── Spline read / write ────────────────────────────────────────────────


def spline_count(runtime: Any, node: Any) -> Tuple[Optional[int], Optional[str]]:
    """Return the number of splines in a shape node."""
    getter = getattr(runtime, "numSplines", None)
    if not callable(getter):
        return None, "the host does not expose numSplines"
    try:
        return int(getter(node)), None
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return None, "could not read the spline count: {}".format(exc)


def knot_count(runtime: Any, node: Any, spline_index: int) -> Tuple[Optional[int], Optional[str]]:
    """Return the number of knots in one spline."""
    getter = getattr(runtime, "numKnots", None)
    if not callable(getter):
        return None, "the host does not expose numKnots"
    try:
        return int(getter(node, spline_index)), None
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return None, "could not read the knot count: {}".format(exc)


def read_knot(runtime: Any, node: Any, spline_index: int, knot_index: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read one knot as world-space position plus local handle vectors."""
    getters = {
        "position": "getKnotPoint",
        "in_vec": "getInVec",
        "out_vec": "getOutVec",
    }
    raw: Dict[str, Any] = {}
    for key, getter_name in getters.items():
        getter = getattr(runtime, getter_name, None)
        if not callable(getter):
            return None, "the host does not expose {}".format(getter_name)
        try:
            raw[key] = getter(node, spline_index, knot_index)
        except Exception as exc:  # noqa: BLE001 - surface the host rejection.
            return None, "{} failed for knot {}: {}".format(getter_name, knot_index, exc)

    world, error = object_to_world(node, raw["position"])
    if error:
        return None, error
    local = _point_to_list(raw["position"])
    if local is None:
        return None, "the host returned a knot point that is not an XYZ triple"

    knot_type = None
    getter = getattr(runtime, "getKnotType", None)
    if callable(getter):
        try:
            knot_type = str(getter(node, spline_index, knot_index)).lstrip("#")
        except Exception:  # noqa: BLE001 - knot type is descriptive metadata only.
            knot_type = None

    return {
        "index": knot_index,
        "position": world,
        "local_position": local,
        "in_vec": _point_to_list(raw["in_vec"]) or [0.0, 0.0, 0.0],
        "out_vec": _point_to_list(raw["out_vec"]) or [0.0, 0.0, 0.0],
        "knot_type": knot_type,
    }, None


def read_spline_state(runtime: Any, node: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read every spline in a shape node as world-space knots."""
    count, error = spline_count(runtime, node)
    if error:
        return None, error
    splines: List[Dict[str, Any]] = []
    for spline_index in range(1, count + 1):
        knots_total, error = knot_count(runtime, node, spline_index)
        if error:
            return None, error
        closed = None
        is_closed = getattr(runtime, "isClosed", None)
        if callable(is_closed):
            try:
                closed = bool(is_closed(node, spline_index))
            except Exception:  # noqa: BLE001 - closed is descriptive metadata only.
                closed = None
        knots: List[Dict[str, Any]] = []
        for knot_index in range(1, knots_total + 1):
            knot, error = read_knot(runtime, node, spline_index, knot_index)
            if error:
                return None, error
            knots.append(knot)
        splines.append({"index": spline_index, "closed": closed, "knot_count": len(knots), "knots": knots})
    return {"node": node_identity(node), "spline_count": len(splines), "splines": splines}, None


def update_shape(runtime: Any, node: Any) -> Optional[str]:
    """Commit a shape edit and report a host rejection."""
    updater = getattr(runtime, "updateShape", None)
    if not callable(updater):
        return "the host does not expose updateShape"
    try:
        updater(node)
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return "updateShape failed: {}".format(exc)
    return None


def append_knots(
    runtime: Any,
    node: Any,
    spline_index: int,
    local_points: Sequence[Any],
    knot_type: str,
    *,
    curve_type: str,
) -> Optional[str]:
    """Append knots to a spline and report a host rejection."""
    adder = getattr(runtime, "addKnot", None)
    if not callable(adder):
        return "the host does not expose addKnot"
    knot_name = getattr(runtime, "Name", None)
    type_name = knot_name(knot_type) if callable(knot_name) else knot_type
    segment_name = knot_name(curve_type) if callable(knot_name) else curve_type
    for point in local_points:
        try:
            adder(node, spline_index, type_name, segment_name, point)
        except Exception as exc:  # noqa: BLE001 - surface the host rejection.
            return "addKnot failed: {}".format(exc)
    return None


def set_knot_value(
    runtime: Any,
    node: Any,
    spline_index: int,
    knot_index: int,
    *,
    position: Optional[Any] = None,
    in_vec: Optional[Any] = None,
    out_vec: Optional[Any] = None,
    knot_type: Optional[str] = None,
) -> Optional[str]:
    """Write one knot field and report a host rejection."""
    knot_name = getattr(runtime, "Name", None)
    writers: List[Tuple[str, Any]] = []
    if position is not None:
        writers.append(("setKnotPoint", (node, spline_index, knot_index, position)))
    if in_vec is not None:
        writers.append(("setInVec", (node, spline_index, knot_index, in_vec)))
    if out_vec is not None:
        writers.append(("setOutVec", (node, spline_index, knot_index, out_vec)))
    if knot_type is not None:
        type_name = knot_name(knot_type) if callable(knot_name) else knot_type
        writers.append(("setKnotType", (node, spline_index, knot_index, type_name)))

    for name, args in writers:
        writer = getattr(runtime, name, None)
        if not callable(writer):
            return "the host does not expose {}".format(name)
        try:
            writer(*args)
        except Exception as exc:  # noqa: BLE001 - surface the host rejection.
            return "{} failed for knot {}: {}".format(name, knot_index, exc)
    return None


# ── Stale-token protection ─────────────────────────────────────────────


def curve_token(node: Any, state: Dict[str, Any]) -> str:
    """Return a token that changes whenever the spline geometry changes.

    ``edit_curve`` demands the token that ``inspect_curve`` handed out. A knot
    edited from another tool, by the user, or by the host changes the digest,
    so the pending edit fails instead of being applied to a spline the caller
    never looked at.
    """
    payload = {
        "handle": int(getattr(node, "handle", -1)) if getattr(node, "handle", None) is not None else None,
        "name": str(getattr(node, "name", "")),
        "splines": [
            {
                "index": spline["index"],
                "closed": spline.get("closed"),
                "knots": [
                    [
                        round(float(component), 6)
                        for component in (knot.get("position") or [0.0, 0.0, 0.0])
                    ]
                    + [
                        round(float(component), 6)
                        for component in (knot.get("in_vec") or [0.0, 0.0, 0.0])
                    ]
                    + [
                        round(float(component), 6)
                        for component in (knot.get("out_vec") or [0.0, 0.0, 0.0])
                    ]
                    for knot in spline.get("knots", [])
                ],
            }
            for spline in state.get("splines", [])
        ],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:32]


# ── Scene persistence ──────────────────────────────────────────────────

# 3ds Max persists per-node data through the user property buffer. A Python
# attribute on the node wrapper is not that buffer: it survives one call and is
# gone when the wrapper is dropped, so a round-trip through it proves nothing
# about the scene. Persistence therefore goes through the documented user
# property API and is read back through that same API.
_USER_PROP_SETTERS = ("setUserPropVal", "setUserProp")
_USER_PROP_GETTERS = ("getUserPropVal", "getUserProp")
_USER_PROP_DELETERS = ("deleteUserPropVal", "deleteUserProp")


def _user_prop_channel(runtime: Any) -> Tuple[Optional[Any], Optional[Any], Optional[str]]:
    """Return ``(setter, getter, error)`` for the native user property API.

    Setter and getter are paired by index first, so ``setUserPropVal`` is never
    paired with ``getUserProp`` while its own getter is available.
    """
    setters = [getattr(runtime, name, None) for name in _USER_PROP_SETTERS]
    getters = [getattr(runtime, name, None) for name in _USER_PROP_GETTERS]
    for setter, getter in zip(setters, getters):
        if callable(setter) and callable(getter):
            return setter, getter, None
    for setter in setters:
        if callable(setter):
            for getter in getters:
                if callable(getter):
                    return setter, getter, None
    return None, None, "3ds Max exposes none of {} / {}, so parameters cannot be persisted".format(
        ", ".join(_USER_PROP_SETTERS), ", ".join(_USER_PROP_GETTERS)
    )


def store_params(
    runtime: Any, node: Any, property_name: str, params: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    """Persist a parameter mapping on a node through the native channel.

    The write is read back through the same channel, so ``params_stored``
    reflects what the scene retained rather than what Python still holds.
    """
    setter, getter, error = _user_prop_channel(runtime)
    if error:
        return False, error
    encoded = json.dumps(json_safe(params), sort_keys=True)
    try:
        setter(node, property_name, encoded)
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return False, "the user property channel rejected the {} write: {}".format(property_name, exc)
    try:
        readback = getter(node, property_name)
    except Exception as exc:  # noqa: BLE001 - an unreadable write is unverified.
        return False, "the {} write could not be read back: {}".format(property_name, exc)
    if readback != encoded:
        return False, "the user property channel kept {!r} instead of the {} payload".format(
            readback, property_name
        )
    return True, None


def load_params(runtime: Any, node: Any, property_name: str) -> Optional[Dict[str, Any]]:
    """Return persisted parameters for a node, or ``None`` when absent."""
    _setter, getter, error = _user_prop_channel(runtime)
    if error:
        return None
    try:
        encoded = getter(node, property_name)
    except Exception:  # noqa: BLE001 - an unreadable payload is reported as absent.
        return None
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def clear_params(runtime: Any, node: Any, property_name: str) -> bool:
    """Remove persisted parameters from a node and confirm they are gone."""
    setter, getter, error = _user_prop_channel(runtime)
    if error:
        return False
    for name in _USER_PROP_DELETERS:
        deleter = getattr(runtime, name, None)
        if not callable(deleter):
            continue
        try:
            deleter(node, property_name)
        except Exception:  # noqa: BLE001 - fall through to the write-empty path.
            continue
        try:
            return not getter(node, property_name)
        except Exception:  # noqa: BLE001 - an unverified clear is a failed clear.
            return False
    try:
        setter(node, property_name, "")
    except Exception:  # noqa: BLE001 - an unverified clear is a failed clear.
        return False
    try:
        return not getter(node, property_name)
    except Exception:  # noqa: BLE001 - an unverified clear is a failed clear.
        return False


def iter_scene_shapes(runtime: Any, property_name: str) -> List[Dict[str, Any]]:
    """List every scene node that carries a persisted parameter payload."""
    found: List[Dict[str, Any]] = []
    for node in iter_scene_nodes(runtime):
        params = load_params(runtime, node, property_name)
        if params is not None:
            entry = {"node": node_identity(node)}
            entry.update(params)
            found.append(entry)
    return found


# ── Resolution ─────────────────────────────────────────────────────────


def resolve_shape(
    runtime: Any,
    *,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Resolve one node and return ``(node, error_envelope)``."""
    if not node_name and handle is None:
        return None, curve_error("node_name or handle is required")
    result, node = resolve_node_object(runtime, node_name=node_name, handle=_coerce_handle(handle))
    if not result.get("success") or node is None:
        return None, curve_error(result.get("message") or "No matching node found", matches=result.get("matches", []))
    return node, None


def _coerce_handle(handle: Optional[int]) -> Optional[int]:
    if handle is None:
        return None
    try:
        return int(handle)
    except (TypeError, ValueError):
        return None


def delete_node(runtime: Any, node: Any) -> bool:
    """Delete a node and verify it is gone through the handle lookup."""
    try:
        node_handle = int(getattr(node, "handle"))
    except Exception:  # noqa: BLE001 - an unavailable identity cannot prove rollback.
        return False
    delete = getattr(runtime, "delete", None)
    if not callable(delete):
        return False
    try:
        delete(node)
    except Exception:  # noqa: BLE001 - an unverified rollback must fail closed.
        return False
    max_ops = getattr(runtime, "maxOps", None)
    get_node_by_handle = getattr(max_ops, "getNodeByHandle", None) if max_ops is not None else None
    if not callable(get_node_by_handle):
        return False
    try:
        return get_node_by_handle(node_handle) is None
    except Exception:  # noqa: BLE001 - an unverified rollback must fail closed.
        return False


def positions_match(left: Sequence[float], right: Sequence[float], tolerance: float = POSITION_TOLERANCE) -> bool:
    """Compare two XYZ triples with a 32-bit-float friendly tolerance."""
    if len(left) != len(right):
        return False
    return all(math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tolerance) for a, b in zip(left, right))


# ── Parametric profiles ────────────────────────────────────────────────


def rounded_rect_points(
    width: float,
    height: float,
    corner_radius: float,
    corner_segments: int,
) -> Tuple[List[List[float]], Optional[str]]:
    """Return the XY points of a closed rounded rectangle centred on the origin.

    The corner radius is clamped to half of the shorter side, so an
    over-large request is reported as a clamped value instead of producing a
    self-intersecting profile.
    """
    half_width = float(width) / 2.0
    half_height = float(height) / 2.0
    limit = min(half_width, half_height)
    clamped = min(float(corner_radius), limit)
    note = None
    if clamped < float(corner_radius):
        note = "corner_radius was clamped to {} to fit the profile".format(round(clamped, 6))
    if clamped <= 0.0:
        return [[-half_width, -half_height, 0.0], [half_width, -half_height, 0.0], [half_width, half_height, 0.0], [-half_width, half_height, 0.0]], note

    inner_width = half_width - clamped
    inner_height = half_height - clamped
    corners = (
        (inner_width, -inner_height, 0.0),
        (inner_width, inner_height, math.pi / 2.0),
        (-inner_width, inner_height, math.pi),
        (-inner_width, -inner_height, 3.0 * math.pi / 2.0),
    )
    points: List[List[float]] = []
    for centre_x, centre_y, start_angle in corners:
        for step in range(corner_segments + 1):
            angle = start_angle + (math.pi / 2.0) * (float(step) / float(corner_segments))
            points.append(
                [
                    round(centre_x + clamped * math.cos(angle), 6),
                    round(centre_y + clamped * math.sin(angle), 6),
                    0.0,
                ]
            )
    return points, note


def rectangle_points(width: float, height: float) -> List[List[float]]:
    """Return the XY points of a closed rectangle centred on the origin."""
    half_width = float(width) / 2.0
    half_height = float(height) / 2.0
    return [
        [-half_width, -half_height, 0.0],
        [half_width, -half_height, 0.0],
        [half_width, half_height, 0.0],
        [-half_width, half_height, 0.0],
    ]


def circle_points(radius: float, segments: int) -> List[List[float]]:
    """Return the XY points of a closed circle centred on the origin."""
    points: List[List[float]] = []
    for step in range(int(segments)):
        angle = 2.0 * math.pi * float(step) / float(segments)
        points.append([round(float(radius) * math.cos(angle), 6), round(float(radius) * math.sin(angle), 6), 0.0])
    return points


# ── Shape construction ─────────────────────────────────────────────────


def create_spline_shape(
    runtime: Any,
    *,
    world_points: Sequence[Sequence[float]],
    name: Optional[str] = None,
    closed: bool = False,
    knot_type: str = "corner",
    curve_type: str = "line",
) -> Tuple[Optional[Any], Optional[str]]:
    """Create a SplineShape node whose first spline carries ``world_points``.

    Returns ``(node, error)``. The caller owns rollback: this helper does not
    delete a partially built shape, because the caller knows whether the node
    is part of a larger operation.
    """
    factory = getattr(runtime, "SplineShape", None)
    if not callable(factory):
        return None, "3ds Max does not expose the SplineShape constructor"
    try:
        node = factory()
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return None, "SplineShape() failed: {}".format(exc)

    add_new_spline = getattr(runtime, "addNewSpline", None)
    if not callable(add_new_spline):
        return None, "3ds Max does not expose addNewSpline"
    try:
        add_new_spline(node)
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return None, "addNewSpline failed: {}".format(exc)

    local_points: List[Any] = []
    for point in world_points:
        local, error = world_to_object(runtime, node, point)
        if error:
            return node, error
        local_points.append(local)

    error = append_knots(runtime, node, 1, local_points, knot_type, curve_type=curve_type)
    if error:
        return node, error

    if closed:
        closer = getattr(runtime, "closeSpline", None)
        if not callable(closer):
            return node, "3ds Max does not expose closeSpline"
        try:
            closer(node, 1)
        except Exception as exc:  # noqa: BLE001 - surface the host rejection.
            return node, "closeSpline failed: {}".format(exc)

    error = update_shape(runtime, node)
    if error:
        return node, error

    if name is not None:
        try:
            node.name = name
        except Exception as exc:  # noqa: BLE001 - a naming failure is a hard failure.
            return node, "could not name the shape: {}".format(exc)
    return node, None


def verify_world_points(
    runtime: Any,
    node: Any,
    spline_index: int,
    world_points: Sequence[Sequence[float]],
    *,
    expected_closed: Optional[bool] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Read a spline back and compare it against the requested world points."""
    state, error = read_spline_state(runtime, node)
    if error:
        return [], error
    spline = next((item for item in state["splines"] if item["index"] == spline_index), None)
    if spline is None:
        return [], "the shape has no spline at index {} after the write".format(spline_index)

    mismatches: List[Dict[str, Any]] = []
    if spline["knot_count"] != len(world_points):
        mismatches.append(
            {"field": "knot_count", "expected": len(world_points), "actual": spline["knot_count"]}
        )
    else:
        for offset, requested in enumerate(world_points):
            actual = spline["knots"][offset]["position"]
            if not positions_match(requested, actual):
                mismatches.append(
                    {"field": "knots[{}].position".format(offset), "expected": list(requested), "actual": actual}
                )
    if expected_closed is not None and spline["closed"] is not None:
        if bool(spline["closed"]) is not bool(expected_closed):
            mismatches.append({"field": "closed", "expected": expected_closed, "actual": spline["closed"]})
    return mismatches, None


# ── Compound objects and modifiers ─────────────────────────────────────


def apply_properties(
    owner: Any,
    properties: Dict[str, Any],
    *,
    owner_label: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Apply a mapping strictly and split it into applied and rejected entries.

    A property the host refuses, or whose readback differs from the request,
    lands in ``rejected``. The caller decides whether a rejection fails the
    whole call; nothing here downgrades one to a warning on its own.
    """
    applied: Dict[str, Any] = {}
    rejected: List[Dict[str, Any]] = []
    for name, value in properties.items():
        _used, error = set_property_first(owner, (name, str.capitalize(name)), value, owner_label=owner_label)
        if error:
            rejected.append({"property": name, "requested": json_safe(value), "error": error})
            continue
        applied[name] = value
    return applied, rejected


def read_count(runtime: Any, owner: Any, names: Sequence[str]) -> Tuple[Optional[int], bool]:
    """Return ``(count, verified)`` for the first readable count source.

    ``verified`` is False when no candidate produced an integer, which the
    caller must report rather than assume a count of zero.
    """
    for name in names:
        found, value = read_property_first(owner, (name,))
        if found and not isinstance(value, bool):
            try:
                return int(value), True
            except (TypeError, ValueError):
                pass
        function = getattr(runtime, name, None)
        if callable(function):
            try:
                return int(function(owner)), True
            except Exception:  # noqa: BLE001 - probe the next candidate.
                continue
    return None, False


def create_scene_object(runtime: Any, class_names: Sequence[str]) -> Tuple[Optional[Any], Optional[str], Optional[str]]:
    """Instantiate the first available creatable class.

    Returns ``(value, used_class, error)``. The value is whatever the host
    constructor returned - a node for creatable classes - and the caller is
    responsible for checking that it is node-like before using it as one.
    """
    attempts: List[str] = []
    for name in class_names:
        factory = getattr(runtime, name, None)
        if not callable(factory):
            attempts.append("{}: not exposed".format(name))
            continue
        try:
            value = factory()
        except Exception as exc:  # noqa: BLE001 - the next candidate may work.
            attempts.append("{}: {}".format(name, exc))
            continue
        if value is None:
            attempts.append("{}: returned nothing".format(name))
            continue
        return value, name, None
    return None, None, "3ds Max exposes none of {} ({})".format(", ".join(class_names), "; ".join(attempts))


def is_node_like(value: Any) -> bool:
    """Return True when a runtime value looks like a scene node."""
    return hasattr(value, "name") and hasattr(value, "handle")


def detach_modifier(runtime: Any, node: Any, index: int) -> bool:
    """Remove a modifier by 1-based stack index and verify the stack shrank."""
    remover = getattr(runtime, "deleteModifier", None)
    if not callable(remover):
        return False
    try:
        before = len(list(getattr(node, "modifiers", []) or []))
    except Exception:  # noqa: BLE001 - an unreadable stack cannot prove removal.
        return False
    try:
        remover(node, int(index))
    except Exception:  # noqa: BLE001 - an unverified rollback must fail closed.
        return False
    try:
        after = len(list(getattr(node, "modifiers", []) or []))
    except Exception:  # noqa: BLE001 - an unverified rollback must fail closed.
        return False
    return after == before - 1
