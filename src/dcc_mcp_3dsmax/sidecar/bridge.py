"""Localhost bridge that runs structured sidecar requests on 3ds Max's UI thread."""

from __future__ import annotations

import builtins
import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Optional

DEFAULT_BRIDGE_PORT = 0
ENV_BRIDGE_PORT = "DCC_MCP_3DSMAX_BRIDGE_PORT"
_PUMP_NAME = "_dcc_mcp_3dsmax_bridge_process_pending"

_server_instance: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_owned_env_port: Optional[str] = None
_request_queue: "queue.Queue[BridgeRequest]" = queue.Queue()
_active_requests = 0
_active_lock = threading.Lock()

try:
    import pymxs  # noqa: PLC0415

    _RT = pymxs.runtime
except ImportError:  # pragma: no cover - exercised only outside 3ds Max
    _RT = None


class BridgeRequest:
    """One queued sidecar request waiting for the 3ds Max main thread."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload
        self.response = ""
        self.done = threading.Event()


def bridge_status() -> dict:
    """Return bridge health data for `/health`."""
    with _active_lock:
        active = _active_requests
    return {
        "status": "ok",
        "port": _current_port(),
        "main_thread_pump": _RT is not None,
        "busy": active > 0,
        "active_requests": active,
        "queue_size": _request_queue.qsize(),
    }


def process_pending_requests() -> int:
    """Drain queued requests. Called by a hidden 3ds Max timer on the UI thread."""
    from dcc_mcp_3dsmax.sidecar._dispatcher import dispatch_payload  # noqa: PLC0415

    executed = 0
    while True:
        try:
            request = _request_queue.get_nowait()
        except queue.Empty:
            break

        global _active_requests
        with _active_lock:
            _active_requests += 1
        try:
            request.response = dispatch_payload(request.payload)
        finally:
            with _active_lock:
                _active_requests -= 1
            request.done.set()
        executed += 1
    return executed


def execute_on_main_thread(payload: Mapping[str, Any], timeout: float = 30.0) -> str:
    """Execute *payload* on the 3ds Max main thread when pymxs is available."""
    if _RT is None:
        from dcc_mcp_3dsmax.sidecar._dispatcher import dispatch_payload  # noqa: PLC0415

        return dispatch_payload(payload)

    request = BridgeRequest(payload)
    _request_queue.put(request)
    if not request.done.wait(timeout):
        return json.dumps(
            {
                "success": False,
                "error": "timeout",
                "message": "Timed out waiting for 3ds Max main thread",
                "request_id": str(payload.get("request_id") or ""),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return request.response


def start_bridge(port: Optional[int] = None) -> ThreadingHTTPServer:
    """Start the localhost sidecar bridge."""
    global _owned_env_port, _server_instance, _server_thread

    if _server_instance is not None:
        return _server_instance

    env_was_set = ENV_BRIDGE_PORT in os.environ
    resolved_port = int(port if port is not None else _current_port())
    _install_main_thread_pump()
    _server_instance = ThreadingHTTPServer(("127.0.0.1", resolved_port), BridgeHandler)
    actual_port = int(_server_instance.server_address[1])
    _server_thread = threading.Thread(
        target=_server_instance.serve_forever,
        daemon=True,
        name="dcc-mcp-3dsmax-bridge",
    )
    _server_thread.start()
    os.environ[ENV_BRIDGE_PORT] = str(actual_port)
    _owned_env_port = None if env_was_set else str(actual_port)
    print("dcc-mcp-3dsmax bridge listening on http://127.0.0.1:{}/dispatch".format(actual_port))
    return _server_instance


def stop_bridge() -> None:
    """Stop the localhost sidecar bridge."""
    global _owned_env_port, _server_instance, _server_thread
    if _server_instance is None:
        return
    _server_instance.shutdown()
    _server_instance.server_close()
    _server_instance = None
    _server_thread = None
    if _owned_env_port is not None and os.environ.get(ENV_BRIDGE_PORT) == _owned_env_port:
        os.environ.pop(ENV_BRIDGE_PORT, None)
    _owned_env_port = None
    _uninstall_main_thread_pump()


class BridgeHandler(BaseHTTPRequestHandler):
    """HTTP handler for sidecar bridge requests."""

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, bridge_status())
            return
        self._send_json(404, {"success": False, "error": "not-found"})

    def do_POST(self) -> None:
        if self.path not in {"/dispatch", "/execute"}:
            self._send_json(404, {"success": False, "error": "not-found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("payload must be a JSON object")
        except Exception as exc:  # noqa: BLE001
            self._send_json(400, {"success": False, "error": "bad-request", "message": str(exc)})
            return

        response = execute_on_main_thread(body)
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            parsed = {"success": False, "error": "bad-response", "message": response}
        self._send_json(200, parsed)

    def _send_json(self, status: int, body: Mapping[str, Any]) -> None:
        data = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _install_main_thread_pump() -> None:
    if _RT is None:
        return
    setattr(builtins, _PUMP_NAME, process_pending_requests)
    _RT.execute(
        r"""
global dccMcp3dsmaxBridgePumpRollout
try (destroyDialog dccMcp3dsmaxBridgePumpRollout) catch()
rollout dccMcp3dsmaxBridgePumpRollout "dcc-mcp-3dsmax Bridge Pump" width:1 height:1
(
    timer bridgeTimer interval:50 active:true
    on bridgeTimer tick do
    (
        python.Execute "import builtins; builtins._dcc_mcp_3dsmax_bridge_process_pending()"
    )
)
createDialog dccMcp3dsmaxBridgePumpRollout 1 1 pos:[-32000,-32000] style:#(#style_toolwindow)
"""
    )


def _uninstall_main_thread_pump() -> None:
    if _RT is None:
        return
    try:
        _RT.execute("try (destroyDialog dccMcp3dsmaxBridgePumpRollout) catch()")
    except Exception:
        pass
    if hasattr(builtins, _PUMP_NAME):
        delattr(builtins, _PUMP_NAME)


def _current_port() -> int:
    return int(os.environ.get(ENV_BRIDGE_PORT, str(DEFAULT_BRIDGE_PORT)))


if __name__ == "__main__":
    start_bridge()
