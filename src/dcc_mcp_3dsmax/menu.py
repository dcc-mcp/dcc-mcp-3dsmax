"""3ds Max menu and shutdown callback integration."""

from __future__ import annotations

import uuid
from typing import Any, Optional

MENU_TITLE = "DCC MCP"
MACRO_CATEGORY = "DCC MCP"
SHUTDOWN_CALLBACK_ID = "dcc_mcp_3dsmax_shutdown"
MENU_CONTEXT_ID = "0x3D5A9765"

# Module-level cached instance UUID so Copy Instance ID is stable
# across calls and matches the server when it is running.
_instance_uuid: Optional[str] = None


def _get_instance_uuid() -> str:
    """Return the cached instance UUID, generating one if needed.

    Tries to read the live instance_id from a running
    :class:`~dcc_mcp_3dsmax.server.MaxMcpServer` so the clipboard value
    matches what the gateway and ``dcc_diagnostics__get_instance_info``
    report.  Falls back to a freshly generated ``uuid4`` hex string when
    the server has not been started yet.
    """
    global _instance_uuid  # noqa: PLW0603
    if _instance_uuid is not None:
        return _instance_uuid

    # Prefer the running server's instance_id.
    try:
        from dcc_mcp_3dsmax.server import get_server  # noqa: PLC0415

        server = get_server()
        if server is not None:
            instance_id = getattr(server._config, "instance_id", None)  # noqa: SLF001
            if instance_id:
                _instance_uuid = str(instance_id)
                return _instance_uuid
    except Exception:
        pass

    _instance_uuid = uuid.uuid4().hex
    return _instance_uuid


def _clipboard_set(text: str) -> bool:
    """Copy *text* to the system clipboard via PySide2 or PySide6.

    Returns ``True`` on success, ``False`` when no Qt application is
    available.
    """
    for module_name in ("PySide2", "PySide6"):
        try:
            qt_widgets = __import__(
                module_name + ".QtWidgets", fromlist=["QtWidgets"]
            )
            app = qt_widgets.QApplication.instance()
            if app is not None:
                app.clipboard().setText(text)
                return True
        except ImportError:
            continue
    return False


def copy_instance_id() -> None:
    """Copy the dcc-mcp instance UUID to the system clipboard.

    Uses PySide2/Qt clipboard when available; falls back to printing
    the UUID to the MAXScript Listener.
    """
    instance_id = _get_instance_uuid()
    if _clipboard_set(instance_id):
        print("dcc-mcp instance ID copied to clipboard: {}".format(instance_id))
    else:
        print("dcc-mcp instance ID: {}".format(instance_id))
        print("(clipboard copy unavailable — no Qt application found)")


def about_dcc_mcp() -> None:
    """Print dcc-mcp-3dsmax version, instance UUID, and host information."""
    from dcc_mcp_3dsmax.__version__ import __version__  # noqa: PLC0415

    instance_id = _get_instance_uuid()
    print("dcc-mcp-3dsmax v{}".format(__version__))
    print("Instance ID: {}".format(instance_id))

    try:
        from dcc_mcp_3dsmax._version_probe import get_3dsmax_version_string  # noqa: PLC0415

        max_ver = get_3dsmax_version_string()
        print("3ds Max version: {}".format(max_ver))
    except Exception:
        pass

    import sys  # noqa: PLC0415

    print("Python: {}".format(sys.version))


def print_status() -> None:
    """Print dcc-mcp-3dsmax sidecar and bridge status via HTTP health checks."""
    import json
    import os
    import socket
    import urllib.error
    import urllib.request

    gateway_base = "http://127.0.0.1:9765"
    print("dcc-mcp instance ID: {}".format(_get_instance_uuid()))
    print("dcc-mcp gateway: {}/mcp".format(gateway_base))
    print(f"dcc-mcp admin:   {gateway_base}/admin?panel=instances")

    # Bridge health check
    bridge_port = os.environ.get("DCC_MCP_3DSMAX_BRIDGE_PORT")
    if bridge_port:
        try:
            url = f"http://127.0.0.1:{bridge_port}/health"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            print(
                f"bridge health: OK (port={data.get('port', bridge_port)}, "
                f"busy={data.get('busy')}, queue={data.get('queue_size')})"
            )
        except Exception as exc:
            print(f"bridge: not reachable on port {bridge_port} ({exc})")
    else:
        print("bridge: not started")

    # Qt bridge health check
    qt_port = os.environ.get("DCC_MCP_3DSMAX_QT_BRIDGE_PORT")
    if qt_port:
        try:
            url = f"http://127.0.0.1:{qt_port}/health"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            print(
                f"qt bridge health: OK (port={data.get('port', qt_port)}, "
                f"busy={data.get('busy')}, queue={data.get('queue_size')})"
            )
        except Exception:
            # Fallback: TCP connect check
            try:
                sock = socket.create_connection(("127.0.0.1", int(qt_port)), timeout=3)
                sock.close()
                print(f"qt bridge: TCP reachable on port {qt_port} (no /health)")
            except Exception as exc2:
                print(f"qt bridge: not reachable on port {qt_port} ({exc2})")
    else:
        print("qt bridge: not started")


def install_menu() -> bool:
    """Install the DCC MCP menu into the 3ds Max main menu bar."""
    rt = _runtime()
    if rt is None:
        return False
    rt.execute(_menu_script())
    return True


def install_shutdown_callback() -> bool:
    """Stop the sidecar before 3ds Max enters shutdown."""
    rt = _runtime()
    if rt is None:
        return False
    rt.execute(_shutdown_callback_script())
    return True


def _runtime() -> Any:
    try:
        import pymxs  # noqa: PLC0415

        return pymxs.runtime
    except ImportError:
        return None


def _menu_script() -> str:
    return r"""
macroScript DccMcp3dsmax_CopyInstanceId
category:"DCC MCP"
buttonText:"Copy Instance ID"
tooltip:"Copy dcc-mcp instance UUID to clipboard"
(
    on execute do python.Execute "import dcc_mcp_3dsmax; dcc_mcp_3dsmax.copy_instance_id()"
)

macroScript DccMcp3dsmax_StartSidecar
category:"DCC MCP"
buttonText:"Start Server"
tooltip:"Start dcc-mcp-3dsmax server"
(
    on execute do python.Execute "import dcc_mcp_3dsmax; dcc_mcp_3dsmax.main()"
)

macroScript DccMcp3dsmax_StopSidecar
category:"DCC MCP"
buttonText:"Stop Server"
tooltip:"Stop dcc-mcp-3dsmax server"
(
    on execute do python.Execute "import dcc_mcp_3dsmax; dcc_mcp_3dsmax.stop_sidecar_bridge()"
)

macroScript DccMcp3dsmax_OpenAdmin
category:"DCC MCP"
buttonText:"Open Gateway Admin"
tooltip:"Open the DCC MCP gateway admin panel"
(
    on execute do shellLaunch "http://127.0.0.1:9765/admin?panel=instances" ""
)

macroScript DccMcp3dsmax_PrintStatus
category:"DCC MCP"
buttonText:"Server Info"
tooltip:"Print dcc-mcp-3dsmax sidecar and bridge status"
(
    on execute do python.Execute "import dcc_mcp_3dsmax; dcc_mcp_3dsmax.print_status()"
)

macroScript DccMcp3dsmax_AboutDccMcp
category:"DCC MCP"
buttonText:"About DCC MCP"
tooltip:"Show dcc-mcp-3dsmax version and instance information"
(
    on execute do python.Execute "import dcc_mcp_3dsmax; dcc_mcp_3dsmax.about_dcc_mcp()"
)

if menuMan.findMenu "DCC MCP" == undefined do
(
    menuMan.registerMenuContext 0x3D5A9765
    local mainMenuBar = menuMan.getMainMenuBar()
    local dccMenu = menuMan.createMenu "DCC MCP"
    dccMenu.addItem (menuMan.createActionItem "DccMcp3dsmax_CopyInstanceId" "DCC MCP") -1
    dccMenu.addItem (menuMan.createSeparatorItem()) -1
    dccMenu.addItem (menuMan.createActionItem "DccMcp3dsmax_StartSidecar" "DCC MCP") -1
    dccMenu.addItem (menuMan.createActionItem "DccMcp3dsmax_StopSidecar" "DCC MCP") -1
    dccMenu.addItem (menuMan.createSeparatorItem()) -1
    dccMenu.addItem (menuMan.createActionItem "DccMcp3dsmax_OpenAdmin" "DCC MCP") -1
    dccMenu.addItem (menuMan.createActionItem "DccMcp3dsmax_PrintStatus" "DCC MCP") -1
    dccMenu.addItem (menuMan.createSeparatorItem()) -1
    dccMenu.addItem (menuMan.createActionItem "DccMcp3dsmax_AboutDccMcp" "DCC MCP") -1
    local dccMenuItem = menuMan.createSubMenuItem "DCC MCP" dccMenu
    local insertIndex = mainMenuBar.numItems() - 1
    mainMenuBar.addItem dccMenuItem insertIndex
    menuMan.updateMenuBar()
    print "dcc-mcp-3dsmax menu installed"
)
"""


def remove_menu() -> bool:
    """Remove the DCC MCP menu from 3ds Max main menu bar. Idempotent — safe to
    call even when the menu, macros, or context do not exist."""
    rt = _runtime()
    if rt is None:
        return False
    rt.execute(_remove_menu_script())
    return True


def _remove_menu_script() -> str:
    return r"""
local userMacrosDir = getDir #userMacros

-- Idempotent: silently skip when menu / macros / context are already absent.
try (
    if menuMan.findMenu "DCC MCP" != undefined do
    (
        local mainMenuBar = menuMan.getMainMenuBar()
        for i = mainMenuBar.numItems() to 1 by -1 do
        (
            local item = mainMenuBar.getItem i
            if (classOf item) == SubMenuItem and (item.getTitle()) == "DCC MCP" do
            (
                mainMenuBar.removeItem i
            )
        )
    )
) catch()

try (menuMan.unRegisterMenuContext 0x3D5A9765) catch()

try (macros.delete "DccMcp3dsmax_CopyInstanceId") catch()
try (macros.delete "DccMcp3dsmax_AboutDccMcp") catch()
try (macros.delete "DccMcp3dsmax_StartSidecar") catch()
try (macros.delete "DccMcp3dsmax_StopSidecar") catch()
try (macros.delete "DccMcp3dsmax_OpenAdmin") catch()
try (macros.delete "DccMcp3dsmax_PrintStatus") catch()

-- Remove persisted macroScript .mcr files so macros don't survive a restart.
try (deleteFile (userMacrosDir + "\DCC MCP-DccMcp3dsmax_CopyInstanceId.mcr")) catch()
try (deleteFile (userMacrosDir + "\DCC MCP-DccMcp3dsmax_AboutDccMcp.mcr")) catch()
try (deleteFile (userMacrosDir + "\\DCC MCP-DccMcp3dsmax_StartSidecar.mcr")) catch()
try (deleteFile (userMacrosDir + "\\DCC MCP-DccMcp3dsmax_StopSidecar.mcr")) catch()
try (deleteFile (userMacrosDir + "\\DCC MCP-DccMcp3dsmax_OpenAdmin.mcr")) catch()
try (deleteFile (userMacrosDir + "\\DCC MCP-DccMcp3dsmax_PrintStatus.mcr")) catch()

try (menuMan.updateMenuBar()) catch()

try (
    callbacks.removeScripts id:#dcc_mcp_3dsmax_shutdown
) catch()

print "dcc-mcp-3dsmax menu removed"
"""


def _shutdown_callback_script() -> str:
    return r"""
callbacks.removeScripts id:#dcc_mcp_3dsmax_shutdown
callbacks.addScript #preSystemShutdown "python.Execute \"import dcc_mcp_3dsmax; dcc_mcp_3dsmax.stop_sidecar_bridge()\"" id:#dcc_mcp_3dsmax_shutdown persistent:false
"""
