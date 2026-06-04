# 3ds Max Runtime Bridge

The runtime bootstrap runs inside Autodesk 3ds Max. By default it starts the
embedded adapter runtime, registers bundled 3ds Max tools with the shared
gateway, and exposes a local bridge for main-thread scene edits.

## Start Inside 3ds Max

Run:

```maxscript
python.ExecuteFile @"C:\path\to\dcc-mcp-3dsmax\examples\start_sidecar_bridge.py"
```

By default the internal bridge asks the OS for a free localhost port. Startup
prints the HTTP dispatch endpoint, for example:

```text
dcc-mcp-3dsmax bridge listening on http://127.0.0.1:<random-port>/dispatch
```

Override the bridge port with `DCC_MCP_3DSMAX_BRIDGE_PORT` when a fixed bridge
port is needed. Normal MZP installs do not need `DCC_MCP_3DSMAX_PORT` or
`DCC_MCP_GATEWAY_PORT`: the adapter uses an internal ephemeral MCP port and the
public MCP gateway is stable at:

```text
http://127.0.0.1:9765/mcp
```

After startup, `http://127.0.0.1:9765/admin?panel=instances` should list a
`3dsmax` instance on a random localhost port.

The runtime uses the default `dcc-mcp-server` registry. Set
`DCC_MCP_REGISTRY_DIR` externally only when the whole local gateway stack needs
to share a non-default registry directory.

Logs follow the shared `dcc-mcp-core` / gateway defaults, matching the Maya
adapter. The 3ds Max bootstrap does not require adapter-specific log
environment variables.

The process-isolated sidecar path is still available for diagnostics with
`DCC_MCP_3DSMAX_BOOT_MODE=sidecar`. In that mode `DCC_MCP_SERVER_BIN` is the
only executable override above the current bundled payload; stale or missing
overrides fall back to the versioned install. The sidecar command and process
environment are built through `dcc_mcp_core.install_lifecycle`, so registry,
gateway-daemon, and remote-gateway options stay aligned with the core runtime.

## Rez / Pipeline Bootstrap

For managed deployments, the startup script can use package roots from the
launch environment instead of a copied MZP payload. This lets a Rez launcher
keep packages in a central/cache layout and only install a thin startup hook in
3ds Max.

Supported variables:

- `DCC_MCP_3DSMAX_BOOTSTRAP_PATHS`: semicolon-separated Python import roots.
- `DCC_MCP_PYTHONPATHS`: shared semicolon-separated Python import roots.
- `DCC_MCP_3DSMAX_ROOT`: adapter package root.
- `DCC_MCP_CORE_ROOT`: `dcc-mcp-core` package root.
- `DCC_MCP_SERVER_ROOT`: fallback `dcc-mcp-server` package root.
- `DCC_MCP_SERVER_BIN`: explicit `dcc-mcp-server` executable path for sidecar mode.

For each root variable, startup probes `python37/` when running older 3ds Max
Python, then `python/`, `src/`, and the root itself. A package cache such as:

```text
<package-cache>/dcc_mcp_core
<package-cache>/dcc_mcp_3dsmax
<package-cache>/dcc_mcp_server
```

can therefore be exposed directly by the launcher without copying files into
the user's scripts directory.

## 3ds Max Menu

Startup installs a `DCC MCP` menu in the main menu bar with:

- `Start Server`
- `Stop Server`
- `Open Gateway Admin`
- `Print Status`

To install only the menu without starting the runtime:

```maxscript
python.Execute "import dcc_mcp_3dsmax; dcc_mcp_3dsmax.install_menu(); dcc_mcp_3dsmax.install_shutdown_callback()"
```

The shutdown callback uses `#preSystemShutdown` and calls
`dcc_mcp_3dsmax.stop_sidecar_bridge()`, which stops the embedded MCP server, any
external sidecar process, and both localhost bridges.

## Dispatch Payload

Send a registered action name:

```json
{
  "action": "3dsmax-modeling__create_box",
  "args": {
    "width": 100,
    "height": 100,
    "depth": 100
  },
  "request_id": "example-1"
}
```

For lower-level tests, send an explicit script path:

```json
{
  "script_path": "C:\\path\\to\\action_create_box.py",
  "args": {
    "width": 100
  },
  "request_id": "example-2"
}
```

## Health

```text
GET http://127.0.0.1:<random-port>/health
```

Returns bridge state, queue size, whether a 3ds Max main-thread pump is active,
and whether the local bridge is currently busy.

## Threading Model

The HTTP listener runs on a background thread. When `pymxs` is available, the
legacy bridge queues direct `/dispatch` requests and drains them from a hidden
MaxScript timer, keeping those scene edits on the 3ds Max UI thread. Gateway
`tools/call` traffic uses the shared `dcc-mcp-core` 0.17.56
`HostUiDispatcherBase` plus `HostPumpController` path; the adapter only maps
3ds Max's .NET timer to the core timer contract. The `qtserver://` sidecar path
uses the core universal Qt dispatcher and core `SidecarActionDispatcher`.
Outside 3ds Max, direct bridge requests execute inline so unit tests can
exercise the protocol without Autodesk binaries.
