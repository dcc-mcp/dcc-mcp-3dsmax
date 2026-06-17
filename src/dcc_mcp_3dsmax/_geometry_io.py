"""Helpers for 3ds Max geometry import/export skill scripts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._scene_utils import iter_scene_nodes, node_identity

SUPPORTED_IMPORT_FORMATS = {
    ".fbx": "fbx",
    ".obj": "obj",
    ".3ds": "3ds",
}
SUPPORTED_EXPORT_FORMATS = {
    ".fbx": "fbx",
    ".obj": "obj",
}
FBX_UNITS = {"mm", "cm", "dm", "m", "km", "in", "ft", "yd"}
FBX_UP_AXIS = {"Y", "Z"}
FBX_IMPORT_MODES = {"create", "merge", "exmerge"}

_IMPORT_PLUGINS = {
    "fbx": ("FBXIMP", "FbxImporter"),
    "obj": ("OBJIMP", "ObjImporter"),
}
_EXPORT_PLUGINS = {
    "fbx": ("FBXEXP", "FbxExporter"),
    "obj": ("OBJEXP", "ObjExporter"),
}


def io_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def io_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def file_info(path: Path) -> Dict[str, Any]:
    """Return JSON-safe file metadata."""
    exists = path.exists()
    return {
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "exists": exists,
        "is_file": path.is_file() if exists else False,
        "size_bytes": path.stat().st_size if path.is_file() else None,
    }


def resolve_import_file(
    file_path: str, expected_format: Optional[str] = None
) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    """Validate an existing import file path."""
    path = Path(file_path).expanduser()
    format_name = _format_from_path(path, SUPPORTED_IMPORT_FORMATS, expected_format)
    if format_name is None:
        return None, io_error(
            "Unsupported geometry import format",
            file=file_info(path),
            supported_formats=sorted(set(SUPPORTED_IMPORT_FORMATS.values())),
        )
    if not path.exists():
        return None, io_error("Geometry file does not exist", file=file_info(path), format=format_name)
    if not path.is_file():
        return None, io_error("Geometry path is not a file", file=file_info(path), format=format_name)
    return path, None


def resolve_export_file(
    output_path: str,
    *,
    expected_extension: str,
    overwrite: bool,
) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    """Validate an export target path."""
    path = Path(output_path).expanduser()
    expected = expected_extension.lower()
    if not expected.startswith("."):
        expected = ".{}".format(expected)
    if path.suffix.lower() != expected:
        return None, io_error("Output path extension must be {}".format(expected), file=file_info(path))
    if not path.parent.exists():
        return None, io_error("Output directory does not exist", file=file_info(path), parent=str(path.parent))
    if path.exists() and not path.is_file():
        return None, io_error("Output path exists and is not a file", file=file_info(path))
    if path.exists() and not overwrite:
        return None, io_error("Output file already exists; pass overwrite=true to replace it", file=file_info(path))
    return path, None


def import_geometry_file(
    runtime: Any,
    file_path: Path,
    *,
    format_name: str,
    fbx_options: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Import one geometry file and return created node identities."""
    before = _node_keys(iter_scene_nodes(runtime))
    warnings = []
    if format_name == "fbx":
        warnings.extend(apply_fbx_import_options(runtime, fbx_options or {}))

    try:
        plugin = _plugin(runtime, _IMPORT_PLUGINS.get(format_name, ()))
        args = [str(file_path), _no_prompt(runtime)]
        kwargs = {"using": plugin} if plugin is not None else {}
        result = runtime.importFile(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return io_error(
            "Geometry import failed",
            file=file_info(file_path),
            format=format_name,
            warnings=warnings,
            exception_type=type(exc).__name__,
            error=str(exc),
        )

    after_nodes = iter_scene_nodes(runtime)
    created_nodes = [node for node in after_nodes if _node_key(node) not in before]
    if result is False:
        return io_error(
            "Geometry import did not complete",
            file=file_info(file_path),
            format=format_name,
            warnings=warnings,
            created_nodes=[node_identity(node) for node in created_nodes],
        )
    return io_success(
        "Imported geometry",
        file=file_info(file_path),
        format=format_name,
        warnings=warnings,
        created_nodes=[node_identity(node) for node in created_nodes],
        created_count=len(created_nodes),
    )


def export_geometry_file(
    runtime: Any,
    output_path: Path,
    *,
    format_name: str,
    selected_only: bool,
    fbx_options: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Export geometry and return counts plus observed output metadata."""
    warnings = []
    nodes = _export_nodes(runtime, selected_only)
    material_count = _material_count(runtime)
    if format_name == "fbx":
        warnings.extend(apply_fbx_export_options(runtime, fbx_options or {}))

    try:
        plugin = _plugin(runtime, _EXPORT_PLUGINS.get(format_name, ()))
        args = [str(output_path), _no_prompt(runtime)]
        kwargs = {"selectedOnly": bool(selected_only)}
        if plugin is not None:
            kwargs["using"] = plugin
        result = runtime.exportFile(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return io_error(
            "Geometry export failed",
            file=file_info(output_path),
            format=format_name,
            selected_only=bool(selected_only),
            exception_type=type(exc).__name__,
            error=str(exc),
            warnings=warnings,
        )

    if not output_path.exists():
        warnings.append("Export completed but output file was not observed on disk")
    if result is False:
        return io_error(
            "Geometry export did not complete",
            file=file_info(output_path),
            format=format_name,
            selected_only=bool(selected_only),
            exported_node_count=len(nodes),
            material_count=material_count,
            warnings=warnings,
        )
    return io_success(
        "Exported geometry",
        file=file_info(output_path),
        format=format_name,
        selected_only=bool(selected_only),
        exported_node_count=len(nodes),
        exported_nodes=[node_identity(node) for node in nodes],
        material_count=material_count,
        warnings=warnings,
    )


def apply_fbx_import_options(runtime: Any, options: Mapping[str, Any]) -> List[str]:
    """Apply supported FBX import options and return warnings."""
    fbx_options = []
    mode = options.get("mode")
    if mode is not None:
        fbx_options.append(("Mode", _max_name(runtime, str(mode))))
    if options.get("units") is not None:
        fbx_options.append(("ConvertUnit", str(options["units"])))
    if options.get("up_axis") is not None:
        fbx_options.append(("UpAxis", str(options["up_axis"]).upper()))
    if options.get("include_animation") is not None:
        fbx_options.append(("Animation", bool(options["include_animation"])))
    return _apply_options(runtime, ("FbxImporterSetParam", "FBXImporterSetParam", "FBXIMPSetParam"), fbx_options)


def apply_fbx_export_options(runtime: Any, options: Mapping[str, Any]) -> List[str]:
    """Apply supported FBX export options and return warnings."""
    fbx_options = []
    if options.get("units") is not None:
        fbx_options.append(("ConvertUnit", str(options["units"])))
    if options.get("up_axis") is not None:
        fbx_options.append(("UpAxis", str(options["up_axis"]).upper()))
    if options.get("include_animation") is not None:
        fbx_options.append(("Animation", bool(options["include_animation"])))
    if options.get("embed_textures") is not None:
        fbx_options.append(("EmbedTextures", bool(options["embed_textures"])))
    if options.get("ascii") is not None:
        fbx_options.append(("ASCII", bool(options["ascii"])))
    return _apply_options(runtime, ("FbxExporterSetParam", "FBXExporterSetParam", "FBXEXPSetParam"), fbx_options)


def fbx_option_error(
    *,
    units: Optional[str] = None,
    up_axis: Optional[str] = None,
    mode: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return an error for unsupported FBX options."""
    if units is not None and units not in FBX_UNITS:
        return io_error("Unsupported FBX unit", units=units, supported_units=sorted(FBX_UNITS))
    if up_axis is not None and up_axis.upper() not in FBX_UP_AXIS:
        return io_error("Unsupported FBX up_axis", up_axis=up_axis, supported_up_axis=sorted(FBX_UP_AXIS))
    if mode is not None and mode not in FBX_IMPORT_MODES:
        return io_error("Unsupported FBX import mode", mode=mode, supported_modes=sorted(FBX_IMPORT_MODES))
    return None


def _format_from_path(path: Path, supported: Mapping[str, str], expected_format: Optional[str]) -> Optional[str]:
    if expected_format:
        expected = expected_format.lower().lstrip(".")
        if expected not in set(supported.values()):
            return None
        if supported.get(path.suffix.lower()) != expected:
            return None
        return expected
    return supported.get(path.suffix.lower())


def _node_key(node: Any) -> Tuple[Optional[int], str]:
    handle = getattr(node, "handle", None)
    try:
        object_id = int(handle) if handle is not None else None
    except (TypeError, ValueError):
        object_id = None
    return object_id, str(getattr(node, "name", ""))


def _node_keys(nodes: Iterable[Any]) -> set:
    return {_node_key(node) for node in nodes}


def _export_nodes(runtime: Any, selected_only: bool) -> List[Any]:
    if selected_only:
        try:
            return list(runtime.selection)
        except Exception:  # noqa: BLE001
            return []
    return iter_scene_nodes(runtime)


def _material_count(runtime: Any) -> int:
    for attr in ("sceneMaterials", "materials"):
        try:
            values = getattr(runtime, attr)
            return len(list(values))
        except Exception:  # noqa: BLE001
            continue
    return 0


def _plugin(runtime: Any, names: Sequence[str]) -> Any:
    for name in names:
        try:
            value = getattr(runtime, name)
        except Exception:  # noqa: BLE001
            continue
        if value is not None:
            return value
    return None


def _no_prompt(runtime: Any) -> Any:
    return _max_name(runtime, "noPrompt")


def _max_name(runtime: Any, value: str) -> Any:
    for attr in ("Name", "name"):
        factory = getattr(runtime, attr, None)
        if callable(factory):
            try:
                return factory(value)
            except Exception:  # noqa: BLE001
                continue
    return "#{}".format(value)


def _apply_options(runtime: Any, setter_names: Sequence[str], options: Sequence[Tuple[str, Any]]) -> List[str]:
    if not options:
        return []
    setter = None
    for name in setter_names:
        candidate = getattr(runtime, name, None)
        if callable(candidate):
            setter = candidate
            break
    if setter is None:
        return ["FBX option setter is unavailable; 3ds Max defaults were used"]

    warnings = []
    for key, value in options:
        try:
            result = setter(key, value)
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not set FBX option {}: {}".format(key, exc))
            continue
        if str(result).lower() == "unsupplied":
            warnings.append("FBX option {} was not recognized by this 3ds Max version".format(key))
    return warnings
