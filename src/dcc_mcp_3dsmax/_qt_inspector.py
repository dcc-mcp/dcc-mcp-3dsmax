"""Adopt the shared core ``qt-ui-inspector`` skill in 3ds Max.

``dcc-mcp-core`` ships a DCC-agnostic, read-only Qt UI inspector
(``register_qt_ui_inspector``) exposing five tools — ``list_windows``,
``find_widgets``, ``describe_widget``, ``snapshot_tree``, ``wait_for_widget``.
3ds Max embeds a Qt (PySide2 / qtmax) main window, so agents can locate custom
rollouts, dialogs, buttons, tree/table views, etc. **by text / objectName /
class / accessibleName** instead of generating ad-hoc PySide enumeration scripts.

Two 3ds Max-specific concerns are handled here:

* **Main-thread affinity.** ``QApplication.allWidgets()`` / ``topLevelWidgets()``
  must be read on 3ds Max's UI thread.  MCP tool handlers run on a tokio worker
  thread, so each inspector handler is wrapped to marshal onto the 3ds Max main
  thread via the in-process UI dispatcher.  In headless / sidecar / standalone
  contexts (or when already on the main thread) the wrapper runs inline.
* **Clear capability message.** The core tools already return structured
  ``qt-binding-unavailable`` / ``qt-no-application`` envelopes when Qt or a
  running ``QApplication`` is missing.

Operator opt-out: ``DCC_MCP_3DSMAX_QT_UI_INSPECTOR=0``.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Set to a falsey token to skip registering the Qt UI inspector tools.
ENV_QT_UI_INSPECTOR = "DCC_MCP_3DSMAX_QT_UI_INSPECTOR"

_TRUTHY = ("1", "true", "yes", "on")


def resolve_qt_ui_inspector_enabled(env: Any = None) -> bool:
    """Return ``True`` unless ``DCC_MCP_3DSMAX_QT_UI_INSPECTOR`` is falsey."""
    environ = env if env is not None else os.environ
    return str(environ.get(ENV_QT_UI_INSPECTOR, "1")).strip().lower() in _TRUTHY


def _marshal_to_main(dispatcher: Any, fn: Callable[[], Any]) -> Any:
    """Run ``fn`` on 3ds Max's main thread via *dispatcher* when needed.

    Inline when already on the main thread or when no UI dispatcher is
    available (sidecar / standalone / pytest), otherwise submitted as a
    ``main``-affinity callable and the result unwrapped from the dispatcher
    envelope.
    """
    if dispatcher is None or threading.current_thread() is threading.main_thread():
        return fn()
    submit = getattr(dispatcher, "submit_callable", None)
    if not callable(submit):
        return fn()
    try:
        result = submit(
            request_id="qt_ui_inspector_{}".format(uuid.uuid4().hex),
            task=fn,
            affinity="main",
        )
    except Exception as exc:  # noqa: BLE001 — degrade to inline rather than fail
        logger.debug("[3dsmax] qt inspector main-thread marshalling failed, running inline: %s", exc)
        return fn()
    if isinstance(result, dict):
        if not result.get("success", True):
            # Surface the dispatcher error by running inline so the core
            # tool's own structured envelope is preserved when possible.
            return fn()
        return result.get("output")
    return result


def _wrap_main_thread(dispatcher: Any, handler: Callable[[Any], Any]) -> Callable[[Any], Any]:
    def wrapper(params: Any) -> Any:
        return _marshal_to_main(dispatcher, lambda: handler(params))

    return wrapper


class _MainThreadHandlerProxy:
    """Server proxy that wraps every registered handler in main-thread routing.

    ``register_qt_ui_inspector`` uses only ``server.registry`` and
    ``server.register_handler`` — both are forwarded; ``register_handler``
    additionally wraps the handler so the read happens on 3ds Max's UI thread.
    """

    def __init__(self, server: Any, dispatcher: Any = None) -> None:
        self._server = server
        self._dispatcher = dispatcher

    @property
    def registry(self) -> Any:
        return self._server.registry

    def register_handler(self, name: str, handler: Callable[[Any], Any]) -> Any:
        return self._server.register_handler(name, _wrap_main_thread(self._dispatcher, handler))

    def __getattr__(self, item: str) -> Any:  # pragma: no cover - passthrough
        return getattr(self._server, item)


def register_3dsmax_qt_ui_inspector(
    inner_server: Any,
    *,
    dcc_name: str = "3dsmax",
    dispatcher: Any = None,
) -> bool:
    """Register the shared ``qt_ui_inspector__*`` tools on the inner MCP server.

    Returns ``True`` when the core inspector was registered, ``False`` when
    disabled by env var or unavailable in the installed core.
    """
    if not resolve_qt_ui_inspector_enabled():
        logger.info("[%s] qt-ui-inspector disabled via %s", dcc_name, ENV_QT_UI_INSPECTOR)
        return False
    try:
        from dcc_mcp_core.skills.qt_ui_inspector import register_qt_ui_inspector  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — older core without the shared skill
        logger.info("[%s] qt-ui-inspector unavailable in installed dcc-mcp-core: %s", dcc_name, exc)
        return False
    try:
        register_qt_ui_inspector(_MainThreadHandlerProxy(inner_server, dispatcher), dcc_name=dcc_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] qt-ui-inspector registration failed: %s", dcc_name, exc)
        return False
    logger.info("[%s] qt-ui-inspector tools registered (main-thread routed)", dcc_name)
    return True
