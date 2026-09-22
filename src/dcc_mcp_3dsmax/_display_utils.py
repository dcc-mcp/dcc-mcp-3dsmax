"""Helpers for 3ds Max display layer and custom property skill scripts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._scene_utils import (
    ATTR_APPLIED,
    apply_object_attribute,
    iter_scene_nodes,
    node_identity,
    resolve_node_objects,
    summarize_attribute_results,
)


def display_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def display_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def resolve_display_targets(
    runtime: Any,
    *,
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    use_selection: bool = False,
    require_targets: bool = False,
) -> Dict[str, Any]:
    """Resolve target nodes for display/property operations."""
    if use_selection:
        try:
            selected = list(runtime.selection)
        except Exception:  # noqa: BLE001
            selected = []
        if not selected:
            return display_error("Current selection is empty", objects=[])
        return {"success": True, "status": "success", "message": "Resolved selected nodes", "objects": selected}
    if node_names or handles:
        result = resolve_node_objects(runtime, node_names=node_names, handles=handles)
        if not result.get("success"):
            return display_error(result["message"], errors=result.get("errors", []), objects=[])
        result["status"] = "success"
        return result
    if require_targets:
        return display_error("node_names, handles, or use_selection=true is required", objects=[])
    nodes = iter_scene_nodes(runtime)
    return {"success": True, "status": "success", "message": "Resolved scene nodes", "objects": nodes}


def list_layers(runtime: Any, *, include_nodes: bool = False, include_properties: bool = False) -> Dict[str, Any]:
    """List display/layer groups."""
    layers = [
        _layer_summary(layer, include_nodes=include_nodes, include_properties=include_properties)
        for layer in _iter_layers(runtime)
    ]
    return display_success("Listed display layers", layers=layers, count=len(layers))


def create_layer(runtime: Any, *, name: str) -> Dict[str, Any]:
    """Create or return an existing display layer."""
    existing = _find_layer(runtime, name)
    if existing is not None:
        return display_success("Display layer already exists", layer=_layer_summary(existing), changed_layer_count=0)
    layer, warnings = _new_layer(runtime, name)
    if layer is None:
        return display_error("No supported layer creation API was available", layer_name=name, warnings=warnings)
    return display_success(
        "Created display layer", layer=_layer_summary(layer), changed_layer_count=1, warnings=warnings
    )


def delete_layer(runtime: Any, *, name: str, delete_nodes: bool = False) -> Dict[str, Any]:
    """Delete a display layer by name."""
    layer = _find_layer(runtime, name)
    if layer is None:
        return display_error("Display layer was not found", layer_name=name, changed_layer_count=0)
    nodes = list(getattr(layer, "nodes", []) or [])
    if nodes and not delete_nodes:
        for node in nodes:
            _set_optional_attr(node, "layer", None)
    remover = getattr(getattr(runtime, "LayerManager", None), "deleteLayerByName", None)
    if callable(remover):
        try:
            remover(name)
        except Exception as exc:  # noqa: BLE001
            return display_error(
                "Could not delete display layer", layer_name=name, error=str(exc), changed_layer_count=0
            )
    else:
        layers = getattr(runtime, "layers", None)
        if isinstance(layers, dict):
            layers.pop(name, None)
        elif isinstance(layers, list) and layer in layers:
            layers.remove(layer)
        else:
            return display_error(
                "No supported layer deletion API was available", layer_name=name, changed_layer_count=0
            )
    return display_success("Deleted display layer", layer_name=name, changed_layer_count=1)


def assign_nodes_to_layer(
    runtime: Any, *, layer_name: str, nodes: Sequence[Any], create_if_missing: bool = True
) -> Dict[str, Any]:
    """Assign nodes to a display layer."""
    layer = _find_layer(runtime, layer_name)
    if layer is None and create_if_missing:
        layer, warnings = _new_layer(runtime, layer_name)
    else:
        warnings = []
    if layer is None:
        return display_error(
            "Display layer was not found", layer_name=layer_name, changed_node_count=0, warnings=warnings
        )
    changed = []
    rejected = []
    unverified = []
    for node in nodes:
        _add_node_to_layer(layer, node, warnings)
        _mirror_layer_name(node, layer_name, warnings)
        identity = node_identity(node)
        membership = _layer_membership(layer, node)
        if membership is True:
            changed.append(identity)
        elif membership is False:
            rejected.append({"node": identity, "message": "The host did not keep the node in the layer"})
        else:
            warnings.append(
                "Could not read back layer membership for {}".format(
                    identity.get("node_name") or identity.get("object_id")
                )
            )
            unverified.append(identity)
            changed.append(identity)
    data = {
        "layer": _layer_summary(layer),
        "nodes": changed,
        "unverified": unverified,
        "errors": rejected,
        "changed_node_count": len(changed),
        "warnings": warnings,
    }
    if rejected:
        return display_error("Could not assign every node to the display layer", **data)
    return display_success("Assigned nodes to display layer", **data)


def _mirror_layer_name(node: Any, layer_name: str, warnings: List[str]) -> None:
    """Mirror the layer name onto the node when the host exposes that property.

    ``node.layer`` is a convenience mirror; membership in the layer's node list is
    what makes an assignment real, so a host that refuses the mirror is reported
    as a warning rather than failing the assignment.
    """
    row = apply_object_attribute(node, "layer", layer_name, optional=True)
    if row["status"] != ATTR_APPLIED and row["warning"]:
        warnings.append(row["warning"])


def _layer_membership(layer: Any, node: Any) -> Optional[bool]:
    """Report whether ``node`` is held by ``layer``, or ``None`` when unreadable."""
    members = getattr(layer, "nodes", None)
    if members is None:
        return None
    try:
        members = list(members)
    except TypeError:
        return None
    handle = getattr(node, "handle", None)
    name = str(getattr(node, "name", "") or "")
    for member in members:
        if member is node:
            return True
        member_handle = getattr(member, "handle", None)
        if handle is not None and member_handle is not None:
            try:
                if int(member_handle) == int(handle):
                    return True
            except (TypeError, ValueError):
                pass
        if name and str(getattr(member, "name", "") or "") == name:
            return True
    assigned = getattr(node, "layer", None)
    if assigned is not None and str(assigned) == str(getattr(layer, "name", "")):
        return True
    return False


def display_state_summary(node: Any) -> Dict[str, Any]:
    """Return common node display-state metadata."""
    return {
        "node": node_identity(node),
        "hidden": _hidden(node),
        "frozen": bool(getattr(node, "isFrozen", getattr(node, "frozen", False))),
        "wire_color": _color_value(getattr(node, "wireColor", getattr(node, "wire_color", None))),
        "object_color": _color_value(getattr(node, "objectColor", getattr(node, "object_color", None))),
        "display_mode": str(getattr(node, "displayMode", getattr(node, "display_mode", "normal"))),
        "layer": getattr(node, "layer", None),
    }


# Node display-state properties. Alternate names are tried in order and the
# first one the host exposes is the one that gets written and read back.
NODE_DISPLAY_PROPERTY_MAP: Dict[str, Tuple[str, ...]] = {
    "hidden": ("isHidden", "hidden"),
    "frozen": ("isFrozen", "frozen"),
    "wire_color": ("wireColor", "wire_color"),
    "object_color": ("objectColor", "object_color"),
    "display_mode": ("displayMode", "display_mode"),
}


def set_display_state(
    node: Any,
    *,
    hidden: Optional[bool] = None,
    frozen: Optional[bool] = None,
    wire_color: Optional[Sequence[int]] = None,
    object_color: Optional[Sequence[int]] = None,
    display_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Set common node display-state metadata and verify each write."""
    requested: List[Tuple[str, Any]] = []
    if hidden is not None:
        requested.append(("hidden", bool(hidden)))
    if frozen is not None:
        requested.append(("frozen", bool(frozen)))
    if wire_color is not None:
        requested.append(("wire_color", _color_list(wire_color)))
    if object_color is not None:
        requested.append(("object_color", _color_list(object_color)))
    if display_mode is not None:
        requested.append(("display_mode", str(display_mode)))

    results = [
        apply_object_attribute(
            node, NODE_DISPLAY_PROPERTY_MAP[name][0], value, label=name, candidates=NODE_DISPLAY_PROPERTY_MAP[name]
        )
        for name, value in requested
    ]
    summary = summarize_attribute_results(results)
    data = {
        "state": display_state_summary(node),
        "changed_fields": summary["applied"],
        "applied": summary["applied"],
        "unverified": summary["unverified"],
        "errors": summary["errors"],
        "warnings": summary["warnings"],
    }
    if not requested:
        return display_error("No display state was requested", **data)
    if summary["errors"]:
        return display_error("Could not apply every requested display state change", **data)
    return display_success("Updated node display state", **data)


def custom_properties(node: Any) -> Dict[str, Any]:
    """Return user-defined properties for one node."""
    props = getattr(node, "user_properties", None)
    if props is None:
        props = getattr(node, "custom_properties", None)
    if props is None:
        props = {}
        _set_optional_attr(node, "user_properties", props)
    return props


def custom_property_summary(node: Any) -> Dict[str, Any]:
    """Return a serializable property summary for one node."""
    props = dict(custom_properties(node))
    return {"node": node_identity(node), "properties": props, "count": len(props)}


def get_custom_property(node: Any, *, property_name: str) -> Dict[str, Any]:
    """Get one custom property from a node."""
    props = custom_properties(node)
    if property_name not in props:
        return display_error("Custom property was not found", node=node_identity(node), property_name=property_name)
    return display_success(
        "Read custom property", node=node_identity(node), property_name=property_name, value=props[property_name]
    )


def set_custom_property(node: Any, *, property_name: str, value: Any) -> Dict[str, Any]:
    """Set one custom property on a node."""
    props = custom_properties(node)
    props[property_name] = value
    return display_success(
        "Set custom property",
        node=node_identity(node),
        property_name=property_name,
        value=value,
        changed_property_count=1,
    )


def delete_custom_property(node: Any, *, property_name: str) -> Dict[str, Any]:
    """Delete one custom property from a node."""
    props = custom_properties(node)
    if property_name not in props:
        return display_error(
            "Custom property was not found",
            node=node_identity(node),
            property_name=property_name,
            changed_property_count=0,
        )
    value = props.pop(property_name)
    return display_success(
        "Deleted custom property",
        node=node_identity(node),
        property_name=property_name,
        previous_value=value,
        changed_property_count=1,
    )


def _iter_layers(runtime: Any) -> List[Any]:
    layers = getattr(runtime, "layers", None)
    if isinstance(layers, dict):
        return list(layers.values())
    if isinstance(layers, list):
        return list(layers)
    manager = getattr(runtime, "LayerManager", None)
    if manager is None:
        return []
    count = getattr(manager, "count", None)
    getter = getattr(manager, "getLayer", None)
    if callable(getter) and isinstance(count, int):
        result = []
        for index in range(count):
            try:
                result.append(getter(index))
            except Exception:  # noqa: BLE001
                continue
        return result
    return []


def _find_layer(runtime: Any, name: str) -> Any:
    manager = getattr(runtime, "LayerManager", None)
    getter = getattr(manager, "getLayerFromName", None) if manager is not None else None
    if callable(getter):
        try:
            layer = getter(name)
            if layer is not None:
                return layer
        except Exception:  # noqa: BLE001
            pass
    for layer in _iter_layers(runtime):
        if str(getattr(layer, "name", "")) == str(name):
            return layer
    return None


def _new_layer(runtime: Any, name: str) -> Tuple[Any, List[str]]:
    warnings = []
    manager = getattr(runtime, "LayerManager", None)
    creator = getattr(manager, "newLayerFromName", None) if manager is not None else None
    if callable(creator):
        try:
            return creator(name), warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not create layer through LayerManager: {}".format(exc))
    layers = getattr(runtime, "layers", None)
    if isinstance(layers, dict):
        layer = type("DisplayLayer", (), {"name": name, "nodes": []})()
        layers[name] = layer
        return layer, warnings
    return None, warnings


def _layer_summary(layer: Any, *, include_nodes: bool = False, include_properties: bool = False) -> Dict[str, Any]:
    nodes = list(getattr(layer, "nodes", []) or [])
    payload = {
        "name": str(getattr(layer, "name", "")),
        "hidden": bool(getattr(layer, "isHidden", getattr(layer, "hidden", False))),
        "frozen": bool(getattr(layer, "isFrozen", getattr(layer, "frozen", False))),
        "node_count": len(nodes),
    }
    if include_nodes:
        payload["nodes"] = [node_identity(node) for node in nodes]
    if include_properties:
        payload["properties"] = layer_property_summary(layer)
    return payload


# Layer properties exposed by 3ds Max layer objects. Each entry maps a tool
# parameter to the property names hosts are known to use, tried in order, so a
# write is only reported applied when one of them reads back the requested value.
LAYER_PROPERTY_MAP: Dict[str, Tuple[str, ...]] = {
    "visible": ("on", "isOn"),
    "hidden": ("isHidden", "hidden"),
    "frozen": ("isFrozen", "frozen"),
    "renderable": ("renderable",),
    "cast_shadows": ("castShadows",),
    "receive_shadows": ("receiveShadows",),
    "motion_blur": ("motionBlur",),
    "primary_visibility": ("primaryVisibility",),
    "secondary_visibility": ("secondaryVisibility",),
    "visible_in_reflections": ("visibleInReflections",),
    "visible_in_refractions": ("visibleInRefractions",),
    "box_mode": ("boxMode",),
    "back_cull": ("backCull",),
    "all_edges": ("allEdges",),
    "ignore_extents": ("ignoreExtents",),
    "show_trajectory": ("showTrajectory",),
    "show_frozen_in_gray": ("showFrozenInGray",),
    "xray": ("xray",),
    "display_by_layer": ("displayByLayer",),
    "inherit_visibility": ("inheritVisibility",),
    "color": ("wireColor", "color"),
}

LAYER_PROPERTY_NAMES = tuple(sorted(LAYER_PROPERTY_MAP))


def layer_property_summary(layer: Any) -> Dict[str, Any]:
    """Read back every known layer property that the host exposes."""
    summary: Dict[str, Any] = {}
    for name, candidates in LAYER_PROPERTY_MAP.items():
        value = None
        for attribute in candidates:
            try:
                value = getattr(layer, attribute)
            except Exception:  # noqa: BLE001 - an unexposed property is reported as None.
                continue
            break
        summary[name] = _jsonable(value)
    return summary


def _jsonable(value: Any) -> Any:
    """Convert a host value into JSON-safe data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    channels = _numeric_channels(value)
    if channels is not None:
        return [int(channel) if float(channel).is_integer() else channel for channel in channels]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_jsonable(item) for item in value]
    return str(value)


def _numeric_channels(value: Any) -> Optional[List[float]]:
    """Return numeric channels for colors and vectors, or ``None``."""
    if isinstance(value, (bool, str, bytes, Mapping)):
        return None
    if isinstance(value, Sequence):
        try:
            channels = [float(item) for item in value]
        except (TypeError, ValueError):
            return None
        return channels or None
    for axes in (("r", "g", "b"), ("x", "y", "z")):
        if all(hasattr(value, axis) for axis in axes):
            try:
                return [float(getattr(value, axis)) for axis in axes]
            except (TypeError, ValueError):
                return None
    return None


def set_layer_properties(runtime: Any, *, layer_name: str, properties: Dict[str, Any]) -> Dict[str, Any]:
    """Write layer properties and verify the host kept every one of them."""
    if not properties:
        return display_error("No layer properties were requested", layer_name=layer_name, applied=[], errors=[])
    unknown = [name for name in properties if name not in LAYER_PROPERTY_MAP]
    if unknown:
        return display_error(
            "Unknown layer properties: {}".format(", ".join(sorted(unknown))),
            layer_name=layer_name,
            unknown=sorted(unknown),
            supported=list(LAYER_PROPERTY_NAMES),
        )
    layer = _find_layer(runtime, layer_name)
    if layer is None:
        return display_error("Display layer was not found", layer_name=layer_name, applied=[], errors=[])
    results = [
        apply_object_attribute(
            layer, LAYER_PROPERTY_MAP[name][0], value, label=name, candidates=LAYER_PROPERTY_MAP[name]
        )
        for name, value in properties.items()
    ]
    summary = summarize_attribute_results(results)
    data = {
        "layer": _layer_summary(layer, include_properties=True),
        "applied": summary["applied"],
        "applied_property_count": summary["applied_count"],
        "unverified": summary["unverified"],
        "errors": summary["errors"],
        "warnings": summary["warnings"],
        "property_results": summary["property_results"],
    }
    if summary["errors"]:
        return display_error("Could not apply every requested layer property", layer_name=layer_name, **data)
    return display_success("Updated display layer properties", layer_name=layer_name, **data)


def _add_node_to_layer(layer: Any, node: Any, warnings: List[str]) -> None:
    add_node = getattr(layer, "addNode", None)
    if callable(add_node):
        try:
            add_node(node)
            return
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not add node through layer API: {}".format(exc))
    nodes = getattr(layer, "nodes", None)
    if nodes is None:
        try:
            layer.nodes = []
            nodes = layer.nodes
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not store layer node list: {}".format(exc))
            return
    if node not in nodes:
        nodes.append(node)


def _hidden(node: Any) -> bool:
    value = getattr(node, "isHidden", None)
    if callable(value):
        try:
            value = value()
        except Exception:  # noqa: BLE001
            value = None
    return bool(value)


def _set_optional_attr(node: Any, attr: str, value: Any) -> None:
    try:
        setattr(node, attr, value)
    except Exception:  # noqa: BLE001
        pass


def _color_list(value: Sequence[int]) -> List[int]:
    if len(value) < 3:
        raise ValueError("Color values require three channels")
    return [max(0, min(255, int(value[0]))), max(0, min(255, int(value[1]))), max(0, min(255, int(value[2])))]


def _color_value(value: Any) -> Optional[List[int]]:
    if value is None:
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 3:
        return _color_list(value)
    return None
