"""3ds Max skill authoring helpers.

Provides utility functions for skill developers to interact with 3ds Max
via the pymxs Python API.
"""

# Import future modules
from __future__ import annotations

# Import built-in modules
import logging
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

# ── Response helpers ─────────────────────────────────────────────────────


def max_success(message: str, **kwargs: Any) -> Dict[str, Any]:
    """Create a success response dictionary.

    Args:
        message: Success message.
        **kwargs: Additional key-value pairs to include in response.

    Returns:
        Dict with ``"status"`` = ``"success"`` and ``"message"``.
    """
    result = {
        "status": "success",
        "message": message,
    }
    result.update(kwargs)
    return result


def max_error(message: str, **kwargs: Any) -> Dict[str, Any]:
    """Create an error response dictionary.

    Args:
        message: Error message.
        **kwargs: Additional key-value pairs to include in response.

    Returns:
        Dict with ``"status"`` = ``"error"`` and ``"message"``.
    """
    result = {
        "status": "error",
        "message": message,
    }
    result.update(kwargs)
    return result


def max_warning(message: str, **kwargs: Any) -> Dict[str, Any]:
    """Create a warning response dictionary.

    Args:
        message: Warning message.
        **kwargs: Additional key-value pairs to include in response.

    Returns:
        Dict with ``"status"`` = ``"warning"`` and ``"message"``.
    """
    result = {
        "status": "warning",
        "message": message,
    }
    result.update(kwargs)
    return result


def max_from_exception(exc: Exception) -> Dict[str, Any]:
    """Create an error response from an exception.

    Args:
        exc: The exception to convert.

    Returns:
        Error response dict with exception message.
    """
    return {
        "status": "error",
        "message": str(exc),
        "exception_type": type(exc).__name__,
    }


# ── Decorators ───────────────────────────────────────────────────────────


def with_max(func: Callable) -> Callable:
    """Decorator to ensure 3ds Max is available before calling function.

    Wraps the function so it checks for pymxs availability and
    catches common 3ds Max exceptions.

    Usage::

        @with_max
        def create_sphere(radius: float = 50.0) -> dict:
            import pymxs
            rt = pymxs.runtime
            sphere_obj = rt.Sphere(radius=radius)
            return max_success("Created sphere", object=str(sphere_obj))
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        try:
            import pymxs  # noqa: PLC0415
        except ImportError:
            return max_error("pymxs not available - not running inside 3ds Max")
        if getattr(pymxs, "runtime", None) is None:
            return max_error("pymxs runtime not available - not running inside 3ds Max")

        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error in %s: %s", func.__name__, exc)
            return max_from_exception(exc)

    return wrapper


# ── Parameter validation helpers ────────────────────────────────────────


class MissingParamError(ValueError):
    """Raised when a required parameter is missing."""

    pass


def require_param(params: Dict[str, Any], param_name: str) -> Any:
    """Get a required parameter or raise MissingParamError.

    Args:
        params: Parameter dictionary.
        param_name: Name of the required parameter.

    Returns:
        The parameter value.

    Raises:
        MissingParamError: If the parameter is missing or None.
    """
    if param_name not in params or params[param_name] is None:
        raise MissingParamError(f"Missing required parameter: {param_name}")
    return params[param_name]


def require_any_param(params: Dict[str, Any], *param_names: str) -> Any:
    """Get one of the required parameters (OR logic).

    Args:
        params: Parameter dictionary.
        *param_names: Names of parameters (at least one required).

    Returns:
        The first found parameter value.

    Raises:
        MissingParamError: If none of the parameters are present.
    """
    for name in param_names:
        if name in params and params[name] is not None:
            return params[name]
    raise MissingParamError(f"Missing required parameter (one of: {', '.join(param_names)})")


def get_param(params: Dict[str, Any], param_name: str, default: Any = None) -> Any:
    """Get an optional parameter with a default value.

    Args:
        params: Parameter dictionary.
        param_name: Name of the parameter.
        default: Default value if parameter is missing.

    Returns:
        The parameter value or default.
    """
    return params.get(param_name, default)


def missing_param_error(param_name: str) -> Dict[str, Any]:
    """Create a 'missing parameter' error response.

    Args:
        param_name: Name of the missing parameter.

    Returns:
        Error response dict.
    """
    return max_error(f"Missing required parameter: {param_name}")


# ── 3ds Max scene helpers ──────────────────────────────────────────────


def is_max_available() -> bool:
    """Check if 3ds Max Python API (pymxs) is available.

    Returns:
        ``True`` if running inside 3ds Max.
    """
    try:
        import pymxs  # noqa: PLC0415

        return pymxs is not None
    except ImportError:
        return False


def get_runtime():
    """Get the 3ds Max runtime object (pymxs.runtime).

    Returns:
        The runtime object or None if not available.
    """
    try:
        import pymxs  # noqa: PLC0415

        return pymxs.runtime
    except ImportError:
        return None


def validate_node_exists(node_name: str) -> bool:
    """Check if a node exists in the scene.

    Args:
        node_name: Name of the node to check.

    Returns:
        ``True`` if the node exists.
    """
    rt = get_runtime()
    if rt is None:
        return False
    return rt.getNodeByName(node_name) is not None


def get_selection() -> list:
    """Get current selection as a list of node names.

    Returns:
        List of selected node names.
    """
    rt = get_runtime()
    if rt is None:
        return []
    selection = rt.selection
    return [str(node.name) for node in selection]


def clear_selection() -> None:
    """Clear the current selection."""
    rt = get_runtime()
    if rt is not None:
        rt.clearSelection()


def select_nodes(node_names: list) -> int:
    """Select nodes by name.

    Args:
        node_names: List of node names to select.

    Returns:
        Number of nodes successfully selected.
    """
    rt = get_runtime()
    if rt is None:
        return 0

    count = 0
    for name in node_names:
        node = rt.getNodeByName(name)
        if node is not None:
            rt.selectMore(node)
            count += 1
    return count
