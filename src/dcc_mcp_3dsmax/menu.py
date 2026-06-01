"""3ds Max menu and shutdown callback integration."""

from __future__ import annotations

from typing import Any

MENU_TITLE = "DCC MCP"
MACRO_CATEGORY = "DCC MCP"
SHUTDOWN_CALLBACK_ID = "dcc_mcp_3dsmax_shutdown"
MENU_CONTEXT_ID = "0x3D5A9765"


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
    return r'''
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
buttonText:"Print Status"
tooltip:"Print dcc-mcp-3dsmax sidecar endpoints"
(
    on execute do python.Execute "import os; print('dcc-mcp-3dsmax bridge:', os.environ.get('DCC_MCP_3DSMAX_BRIDGE_PORT', 'not running')); print('dcc-mcp-3dsmax qt bridge:', os.environ.get('DCC_MCP_3DSMAX_QT_BRIDGE_PORT', 'not running')); print('dcc-mcp gateway: http://127.0.0.1:9765/mcp')"
)

if menuMan.findMenu "DCC MCP" == undefined do
(
    menuMan.registerMenuContext 0x3D5A9765
    local mainMenuBar = menuMan.getMainMenuBar()
    local dccMenu = menuMan.createMenu "DCC MCP"
    dccMenu.addItem (menuMan.createActionItem "DccMcp3dsmax_StartSidecar" "DCC MCP") -1
    dccMenu.addItem (menuMan.createActionItem "DccMcp3dsmax_StopSidecar" "DCC MCP") -1
    dccMenu.addItem (menuMan.createSeparatorItem()) -1
    dccMenu.addItem (menuMan.createActionItem "DccMcp3dsmax_OpenAdmin" "DCC MCP") -1
    dccMenu.addItem (menuMan.createActionItem "DccMcp3dsmax_PrintStatus" "DCC MCP") -1
    local dccMenuItem = menuMan.createSubMenuItem "DCC MCP" dccMenu
    local insertIndex = mainMenuBar.numItems() - 1
    mainMenuBar.addItem dccMenuItem insertIndex
    menuMan.updateMenuBar()
    print "dcc-mcp-3dsmax menu installed"
)
'''


def remove_menu() -> bool:
    """Remove the DCC MCP menu from 3ds Max main menu bar. Idempotent — safe to
    call even when the menu, macros, or context do not exist."""
    rt = _runtime()
    if rt is None:
        return False
    rt.execute(_remove_menu_script())
    return True


def _remove_menu_script() -> str:
    return r'''
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

try (macros.delete "DccMcp3dsmax_StartSidecar") catch()
try (macros.delete "DccMcp3dsmax_StopSidecar") catch()
try (macros.delete "DccMcp3dsmax_OpenAdmin") catch()
try (macros.delete "DccMcp3dsmax_PrintStatus") catch()

try (menuMan.updateMenuBar()) catch()

try (
    callbacks.removeScripts id:#dcc_mcp_3dsmax_shutdown
) catch()

print "dcc-mcp-3dsmax menu removed"
'''


def _shutdown_callback_script() -> str:
    return r'''
callbacks.removeScripts id:#dcc_mcp_3dsmax_shutdown
callbacks.addScript #preSystemShutdown "python.Execute \"import dcc_mcp_3dsmax; dcc_mcp_3dsmax.stop_sidecar_bridge()\"" id:#dcc_mcp_3dsmax_shutdown persistent:false
'''
