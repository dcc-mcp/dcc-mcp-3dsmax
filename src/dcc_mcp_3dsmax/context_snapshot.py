"""3ds Max context snapshot provider for gateway routing and REST ``/v1/context``.

Mirrors the Maya adapter's ``context_snapshot`` module: a small, pure,
crash-tolerant collaborator that feeds 3ds Max-specific live scene state into
core's post-tool ``append_context_snapshot`` helper and into
:meth:`DccServerBase.update_gateway_metadata`.

* :class:`MaxContextSnapshotProvider` is a callable returning a fresh context
  dict on every invocation.  In standalone / headless / sidecar contexts (no
  ``pymxs``) it returns a minimal ``{"dcc": "3dsmax", "available": False}``
  stub instead of raising.
* :func:`collect_gateway_metadata` returns the subset consumed by
  :meth:`DccServerBase.update_gateway_metadata` (scene / version / documents /
  display_name).

Both helpers obey Single Responsibility — they only collect state, never
mutate 3ds Max, and never raise.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "MaxContextSnapshotProvider",
    "collect_gateway_metadata",
    "make_snapshot_provider",
]


# ---------------------------------------------------------------------------
# Snapshot provider
# ---------------------------------------------------------------------------


class MaxContextSnapshotProvider:
    """Callable that returns a fresh 3ds Max context snapshot.

    Parameters
    ----------
    runtime_provider:
        Optional factory returning ``pymxs.runtime`` (or a duck-typed
        stand-in for tests).  Defaults to a lazy import of ``pymxs`` with a
        headless-safe fallback.
    """

    def __init__(
        self,
        runtime_provider: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._runtime_provider = runtime_provider or _default_runtime_provider

    # ------------------------------------------------------------------ API

    def __call__(self) -> Dict[str, Any]:
        return self.collect()

    def collect(self) -> Dict[str, Any]:
        """Return a fresh context snapshot dict.

        Keys (all optional — omitted when unavailable)::

            {
                "dcc":          "3dsmax",
                "scene":        "C:/proj/shot.max",
                "selection":    ["Box001", ...],
                "frame":        10,
                "node_count":   42,
                "units":        "Centimeters",
                "display_name": "3ds Max 2024 — shot.max",
                "version":      "2024",
                "pid":          12345,
                "available":    True | False,
            }

        The method never raises; 3ds Max-specific probes are guarded so
        headless contexts return ``{"dcc": "3dsmax", "available": False}``.
        """
        snapshot: Dict[str, Any] = {
            "dcc": "3dsmax",
            "pid": os.getpid(),
            "available": False,
        }

        rt = self._safe_runtime()
        if rt is None:
            return snapshot

        snapshot["available"] = True

        # Scene path ---------------------------------------------------------
        scene = _safe_scene_path(rt)
        if scene:
            snapshot["scene"] = scene

        # Selection ----------------------------------------------------------
        selection = _safe_selection_names(rt)
        if selection is not None:
            snapshot["selection"] = selection

        # Timeline -----------------------------------------------------------
        frame = _safe_int(_safe_attr(rt, "currentTime"))
        if frame is not None:
            snapshot["frame"] = frame

        # Node count ---------------------------------------------------------
        node_count = _safe_node_count(rt)
        if node_count is not None:
            snapshot["node_count"] = node_count

        # Units --------------------------------------------------------------
        units = _safe_units(rt)
        if units:
            snapshot["units"] = units

        # Version ------------------------------------------------------------
        version = _safe_version()
        if version:
            snapshot["version"] = version

        # Display name -------------------------------------------------------
        display = _derive_display_name(snapshot.get("scene"), snapshot.get("version"))
        if display:
            snapshot["display_name"] = display

        return snapshot

    # ------------------------------------------------------------ internals

    def _safe_runtime(self) -> Any:
        try:
            return self._runtime_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("MaxContextSnapshotProvider: runtime unavailable: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Gateway metadata helper
# ---------------------------------------------------------------------------


def collect_gateway_metadata(
    provider: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, Optional[Any]]:
    """Return a subset snapshot suitable for :meth:`update_gateway_metadata`.

    Returns keys: ``scene`` (str | None), ``version`` (str | None),
    ``documents`` (list[str] | None), ``display_name`` (str | None).  3ds Max
    is a single-document host, so ``documents`` is ``[scene]`` when a scene is
    open, otherwise ``[]``.
    """
    if provider is None:
        provider = MaxContextSnapshotProvider()
    snapshot = provider() or {}
    scene = snapshot.get("scene")
    documents: Optional[List[str]] = [scene] if scene else []
    return {
        "scene": scene if scene else None,
        "version": snapshot.get("version"),
        "documents": documents,
        "display_name": snapshot.get("display_name"),
    }


def make_snapshot_provider(
    runtime_provider: Optional[Callable[[], Any]] = None,
) -> MaxContextSnapshotProvider:
    """Factory for a :class:`MaxContextSnapshotProvider`."""
    return MaxContextSnapshotProvider(runtime_provider=runtime_provider)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _default_runtime_provider() -> Any:
    """Return ``pymxs.runtime`` when available, else ``None``."""
    try:
        import pymxs  # noqa: PLC0415

        return pymxs.runtime
    except Exception:  # noqa: BLE001 — headless / non-Max interpreters
        return None


def _safe_attr(rt: Any, name: str) -> Any:
    """Return ``getattr(rt, name)`` swallowing any exception."""
    try:
        return getattr(rt, name, None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("MaxContextSnapshot: getattr(rt, %s) raised %s", name, exc)
        return None


def _safe_scene_path(rt: Any) -> Optional[str]:
    """Compose the open scene path from ``maxFilePath`` + ``maxFileName``."""
    file_name = _safe_attr(rt, "maxFileName")
    if not file_name:
        return None
    file_path = _safe_attr(rt, "maxFilePath") or ""
    try:
        combined = "{}{}".format(str(file_path), str(file_name))
    except Exception:  # noqa: BLE001
        return str(file_name)
    return combined.strip() or None


def _safe_selection_names(rt: Any) -> Optional[List[str]]:
    """Return selected node names, or ``None`` when unavailable."""
    selection = _safe_attr(rt, "selection")
    if selection is None:
        return None
    names: List[str] = []
    try:
        for node in selection:
            name = getattr(node, "name", None)
            if name:
                names.append(str(name))
    except Exception as exc:  # noqa: BLE001
        logger.debug("MaxContextSnapshot: selection iteration failed: %s", exc)
        return None
    return names


def _safe_node_count(rt: Any) -> Optional[int]:
    """Return the count of scene objects, or ``None`` when unavailable."""
    objects = _safe_attr(rt, "objects")
    if objects is None:
        return None
    try:
        return int(len(list(objects)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("MaxContextSnapshot: objects count failed: %s", exc)
        return None


def _safe_units(rt: Any) -> Optional[str]:
    """Return the system unit type, or ``None`` when unavailable.

    MAXScript ``units.SystemType`` may be a callable (newer builds) or a
    plain name literal such as ``#inches``.  We handle both and strip the
    ``#`` prefix so callers always see a clean lowercase string.
    """
    units = _safe_attr(rt, "units")
    if units is None:
        return None
    system_type = getattr(units, "SystemType", None)
    if system_type is None:
        return None
    try:
        if callable(system_type):
            raw = str(system_type())
        else:
            raw = str(system_type)
    except Exception as exc:  # noqa: BLE001
        logger.debug("MaxContextSnapshot: units.SystemType() failed: %s", exc)
        return None
    normalized = raw.lstrip("#").strip().lower()
    return normalized or None


def _safe_version() -> Optional[str]:
    """Return the 3ds Max version string, or ``None`` when unavailable."""
    try:
        from dcc_mcp_3dsmax._version_probe import get_3dsmax_version_string  # noqa: PLC0415

        version = get_3dsmax_version_string()
    except Exception as exc:  # noqa: BLE001
        logger.debug("MaxContextSnapshot: version probe failed: %s", exc)
        return None
    version = (version or "").strip()
    if not version or version.lower() == "unknown":
        return None
    return version


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _derive_display_name(scene: Optional[str], version: Optional[str]) -> Optional[str]:
    """Produce a human-readable instance label for gateway disambiguation."""
    if scene:
        try:
            basename = os.path.basename(scene) or scene
        except Exception:  # noqa: BLE001
            basename = scene
        if version:
            return "3ds Max {} — {}".format(version, basename)
        return "3ds Max — {}".format(basename)
    if version:
        return "3ds Max {}".format(version)
    return None
