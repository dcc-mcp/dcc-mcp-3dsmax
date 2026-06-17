"""Helpers for 3ds Max display layer and custom property skill scripts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._scene_utils import iter_scene_nodes, node_identity, resolve_node_objects


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


def list_layers(runtime: Any, *, include_nodes: bool = False) -> Dict[str, Any]:
    """List display/layer groups."""
    layers = [_layer_summary(layer, include_nodes=include_nodes) for layer in _iter_layers(runtime)]
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
    for node in nodes:
        _add_node_to_layer(layer, node, warnings)
        _set_optional_attr(node, "layer", layer_name)
        changed.append(node_identity(node))
    return display_success(
        "Assigned nodes to display layer",
        layer=_layer_summary(layer),
        nodes=changed,
        changed_node_count=len(changed),
        warnings=warnings,
    )


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


def set_display_state(
    node: Any,
    *,
    hidden: Optional[bool] = None,
    frozen: Optional[bool] = None,
    wire_color: Optional[Sequence[int]] = None,
    object_color: Optional[Sequence[int]] = None,
    display_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Set common node display-state metadata."""
    changed = []
    if hidden is not None:
        _set_optional_attr(node, "isHidden", bool(hidden))
        changed.append("hidden")
    if frozen is not None:
        _set_optional_attr(node, "isFrozen", bool(frozen))
        _set_optional_attr(node, "frozen", bool(frozen))
        changed.append("frozen")
    if wire_color is not None:
        _set_optional_attr(node, "wireColor", _color_list(wire_color))
        _set_optional_attr(node, "wire_color", _color_list(wire_color))
        changed.append("wire_color")
    if object_color is not None:
        _set_optional_attr(node, "objectColor", _color_list(object_color))
        _set_optional_attr(node, "object_color", _color_list(object_color))
        changed.append("object_color")
    if display_mode is not None:
        _set_optional_attr(node, "displayMode", str(display_mode))
        _set_optional_attr(node, "display_mode", str(display_mode))
        changed.append("display_mode")
    return display_success("Updated display state", state=display_state_summary(node), changed_fields=changed)


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


def _layer_summary(layer: Any, *, include_nodes: bool = False) -> Dict[str, Any]:
    nodes = list(getattr(layer, "nodes", []) or [])
    payload = {
        "name": str(getattr(layer, "name", "")),
        "hidden": bool(getattr(layer, "isHidden", getattr(layer, "hidden", False))),
        "frozen": bool(getattr(layer, "isFrozen", getattr(layer, "frozen", False))),
        "node_count": len(nodes),
    }
    if include_nodes:
        payload["nodes"] = [node_identity(node) for node in nodes]
    return payload


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
