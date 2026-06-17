"""3ds Max resource publishing wiring.

Core 0.15.0+ ships ``McpHttpServer.resources()`` → :class:`ResourceHandle`
with ``set_scene`` / ``register_producer`` / ``notify_updated``.
``scene://current`` is a built-in resource URI that returns
``status: no_scene_published`` until the embedding adapter calls
``set_scene(...)``.  This module is that adapter for 3ds Max — a trimmed port
of the Maya ``_resources`` module.

Public surface:

* :class:`MaxResourceBinder` — publishes a 3ds Max scene snapshot through
  ``scene://current`` with trailing-edge throttling, plus an optional
  ``maxscript://help/<command>`` producer.
* :func:`install_resources(server)` — one-shot helper invoked from
  :meth:`MaxMcpServer.register_builtin_actions`.

Every call into ``server._server.resources()`` lives in this file; skill
scripts and plugin code go through the binder, never the raw handle.

Opt-out: ``DCC_MCP_3DSMAX_RESOURCES=0``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Disable scene-snapshot publishing entirely with this env var.
ENV_RESOURCES = "DCC_MCP_3DSMAX_RESOURCES"

#: Throttle for ``scene://current`` republishing.
DEFAULT_SCENE_THROTTLE_SECS: float = 0.5

#: 3ds Max-specific dynamic resource URI we register a producer for.
SCHEME_MAXSCRIPT = "maxscript://"


# ---------------------------------------------------------------------------
# Env-var resolution
# ---------------------------------------------------------------------------


def resolve_enabled(flag: Optional[bool] = None) -> bool:
    """Resolve whether resource wiring should run.

    Priority: explicit ``flag`` argument > :data:`ENV_RESOURCES` env var
    (``"0"`` disables) > ``True``.
    """
    if flag is not None:
        return bool(flag)
    raw = os.environ.get(ENV_RESOURCES)
    if raw is None:
        return True
    return raw.strip() != "0"


# ---------------------------------------------------------------------------
# Producer callables — pure functions, lazy pymxs import
# ---------------------------------------------------------------------------


def _read_text(text: str, mime: str = "text/plain") -> Dict[str, Any]:
    """Build the ``{"mimeType", "text"}`` reply expected by core."""
    return {"mimeType": mime, "text": text}


def _pymxs_runtime():
    """Lazy ``pymxs.runtime`` import; returns ``None`` outside 3ds Max."""
    try:
        import pymxs  # noqa: PLC0415

        return pymxs.runtime
    except Exception:  # noqa: BLE001
        return None


def _parse_path_uri(uri: str, *, scheme: str) -> Optional[List[str]]:
    """Strip *scheme* prefix and split the rest on ``/``."""
    if not uri.startswith(scheme):
        return None
    tail = uri[len(scheme) :]
    return [p for p in tail.split("/") if p]


def _maxscript_help_producer(uri: str) -> Dict[str, Any]:
    """Producer for ``maxscript://help/<command>`` URIs.

    Returns the MAXScript ``apropos`` / ``help`` text for *command*.  When
    ``pymxs`` is unavailable returns a ``status: max_unavailable`` JSON
    envelope so the agent can degrade gracefully.
    """
    parsed = _parse_path_uri(uri, scheme=SCHEME_MAXSCRIPT)
    if not parsed:
        return _read_text(
            json.dumps({"status": "invalid_uri", "uri": uri, "hint": "use maxscript://help/<command>"}),
            mime="application/json",
        )

    section = parsed[0]
    target = parsed[1] if len(parsed) > 1 else ""
    if section != "help" or not target:
        return _read_text(
            json.dumps({"status": "invalid_uri", "uri": uri, "hint": "use maxscript://help/<command>"}),
            mime="application/json",
        )

    rt = _pymxs_runtime()
    if rt is None:
        return _read_text(
            json.dumps({"status": "max_unavailable", "uri": uri}),
            mime="application/json",
        )
    try:
        # ``apropos`` writes to the Listener; ``execute`` lets us capture a
        # best-effort string description of the symbol.
        text = rt.execute('try(apropos "{}") catch(undefined)'.format(target))
    except Exception as exc:  # noqa: BLE001
        return _read_text(
            json.dumps({"status": "command_not_found", "command": target, "error": str(exc)}),
            mime="application/json",
        )
    return _read_text(str(text) if text is not None else "(no help text)")


# ---------------------------------------------------------------------------
# Throttled scene-snapshot publisher
# ---------------------------------------------------------------------------


SnapshotProvider = Callable[[], Dict[str, Any]]
EventInstaller = Callable[[Callable[[], None], tuple], List[int]]
BusyChecker = Callable[[], bool]

#: Scene-event callback ids whose firing triggers a republish.  The default
#: installer is a no-op (3ds Max callback registration requires MAXScript
#: bridging that is fragile across versions); it stays injectable so tests can
#: drive the throttling state machine without a live 3ds Max.
DEFAULT_SCENE_EVENTS: tuple = (
    "filePostOpen",
    "filePostSave",
    "systemPostNew",
    "selectionSetChanged",
)


def _default_event_installer(callback: Callable[[], None], events: tuple) -> List[int]:  # noqa: ARG001
    """Best-effort scene-event hook installer (no-op outside 3ds Max).

    3ds Max exposes ``rt.callbacks.addScript`` but it expects a MAXScript
    callable, so safely bridging a Python callback is version-fragile.  We
    return ``[]`` by default; the initial snapshot is still published at
    :meth:`MaxResourceBinder.bind` and callers can force a refresh via
    :meth:`MaxResourceBinder.publish_scene`.
    """
    return []


# ---------------------------------------------------------------------------
# 3ds Max-side binder
# ---------------------------------------------------------------------------


class MaxResourceBinder:
    """Compose every ``server._server.resources()`` call for 3ds Max."""

    def __init__(
        self,
        *,
        snapshot_provider: Optional[SnapshotProvider] = None,
        event_installer: Optional[EventInstaller] = None,
        busy_checker: Optional[BusyChecker] = None,
        throttle_secs: float = DEFAULT_SCENE_THROTTLE_SECS,
        events: tuple = DEFAULT_SCENE_EVENTS,
    ) -> None:
        self.snapshot_provider: Optional[SnapshotProvider] = snapshot_provider
        self.event_installer: EventInstaller = event_installer or _default_event_installer
        self.busy_checker: Optional[BusyChecker] = busy_checker
        self.throttle_secs: float = max(0.0, float(throttle_secs))
        self.events: tuple = events

        self.bound_server: Any = None
        self.handle: Any = None
        self.registered_producers: List[str] = []
        self.scene_event_ids: List[int] = []
        self.scene_publish_count: int = 0

        self._lock = threading.Lock()
        self._pending_publish: bool = False
        self._last_publish_at: float = 0.0
        self._publish_timer: Optional[threading.Timer] = None
        self._unbound: bool = False

    # ── Public API ──────────────────────────────────────────────────────

    def bind(self, server: Any) -> bool:
        """Bind the binder to *server*.

        Resolves ``server._server.resources()``, registers the
        ``maxscript://`` producer, and publishes an initial scene snapshot.
        Calling :meth:`bind` twice is a no-op when the second call targets the
        same server.  Returns ``True`` when the handle was obtained.
        """
        if self.bound_server is server:
            return True
        self.bound_server = server
        self._unbound = False

        try:
            self.handle = server._server.resources()
        except Exception as exc:  # noqa: BLE001
            logger.debug("resources: server.resources() unavailable: %s", exc)
            return False

        self._register_producer(SCHEME_MAXSCRIPT, _maxscript_help_producer)

        if self.snapshot_provider is not None:
            self._publish_scene_now()
        return True

    def install_scene_events(self) -> List[int]:
        """Hook scene events so mutations republish ``scene://current``."""
        if self.bound_server is None:
            return []
        if self.scene_event_ids:
            return list(self.scene_event_ids)
        ids = self.event_installer(self._on_scene_event, self.events)
        self.scene_event_ids = list(ids)
        return list(self.scene_event_ids)

    def unbind(self) -> None:
        """Stop pending publishes.  Idempotent."""
        if self._unbound:
            return
        self._unbound = True

        with self._lock:
            timer = self._publish_timer
            self._publish_timer = None
            self._pending_publish = False
        if timer is not None:
            try:
                timer.cancel()
            except Exception:  # noqa: BLE001
                pass
        self.scene_event_ids = []

    def publish_scene(self, payload: Optional[Dict[str, Any]] = None) -> None:
        """Publish a scene snapshot now, bypassing throttling."""
        if self.handle is None:
            return
        if payload is None:
            if self.snapshot_provider is None:
                return
            try:
                payload = self.snapshot_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("resources: snapshot provider raised: %s", exc)
                return
        try:
            self.handle.set_scene(payload)
            self.scene_publish_count += 1
            self._last_publish_at = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            logger.debug("resources: set_scene raised: %s", exc)

    # ── Internals ───────────────────────────────────────────────────────

    def _register_producer(self, scheme: str, producer: Callable[[str], Dict[str, Any]]) -> None:
        if self.handle is None:
            return
        try:
            self.handle.register_producer(scheme, producer)
        except Exception as exc:  # noqa: BLE001
            logger.debug("resources: register_producer(%s) raised: %s", scheme, exc)
            return
        self.registered_producers.append(scheme)

    def _is_executor_busy(self) -> bool:
        if self.busy_checker is None:
            return False
        try:
            return bool(self.busy_checker())
        except Exception as exc:  # noqa: BLE001
            logger.debug("resources: busy checker raised: %s", exc)
            return False

    def _on_scene_event(self) -> None:
        """Scene-event callback: schedule a throttled scene republish."""
        if self._unbound or self._is_executor_busy():
            return
        with self._lock:
            now = time.monotonic()
            since = now - self._last_publish_at
            if since >= self.throttle_secs:
                schedule_now = True
                self._pending_publish = False
            else:
                schedule_now = False
                if not self._pending_publish:
                    delay = self.throttle_secs - since
                    self._pending_publish = True
                    self._publish_timer = threading.Timer(delay, self._on_throttle_fire)
                    self._publish_timer.daemon = True
                    self._publish_timer.start()
        if schedule_now:
            self._publish_scene_now()

    def _on_throttle_fire(self) -> None:
        """Trailing-edge throttle handler — runs on a Timer thread."""
        if self._unbound or self._is_executor_busy():
            return
        with self._lock:
            self._pending_publish = False
            self._publish_timer = None
        self._publish_scene_now()

    def _publish_scene_now(self) -> None:
        """Resolve the current snapshot and publish via :meth:`publish_scene`."""
        self.publish_scene()
        self._sync_gateway_scene_metadata()

    def _sync_gateway_scene_metadata(self) -> None:
        """Push scene path / version into the gateway registry, best-effort."""
        server = self.bound_server
        if server is None:
            return
        publish = getattr(server, "publish_capability_snapshot", None)
        if publish is None:
            return
        try:
            publish(reason="scene_resource")
        except Exception as exc:  # noqa: BLE001
            logger.debug("resources: publish_capability_snapshot failed: %s", exc)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def install_resources(
    server: Any,
    *,
    enabled: Optional[bool] = None,
    snapshot_provider: Optional[SnapshotProvider] = None,
    install_scene_events: bool = True,
    busy_checker: Optional[BusyChecker] = None,
    throttle_secs: float = DEFAULT_SCENE_THROTTLE_SECS,
) -> Optional[MaxResourceBinder]:
    """One-shot helper called from :meth:`MaxMcpServer.register_builtin_actions`.

    Returns the :class:`MaxResourceBinder` when installation succeeded, or
    ``None`` when resources were disabled (``DCC_MCP_3DSMAX_RESOURCES=0``) or
    the inner Rust ``McpHttpServer.resources()`` raised.
    """
    if not resolve_enabled(enabled):
        logger.debug("resources: disabled via env var")
        return None
    binder = MaxResourceBinder(
        snapshot_provider=snapshot_provider,
        busy_checker=busy_checker,
        throttle_secs=throttle_secs,
    )
    if not binder.bind(server):
        return None
    if install_scene_events:
        binder.install_scene_events()
    return binder
