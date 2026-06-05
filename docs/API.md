# dcc-mcp-3dsmax API Documentation

This document describes the Python API for interacting with 3ds Max through the DCC MCP protocol.

## Table of Contents

1. [Overview](#overview)
2. [Environment Variables](#environment-variables)
3. [Version Detection](#version-detection)
4. [API Helpers](#api-helpers)
5. [Server API](#server-api)
6. [Dispatcher API](#dispatcher-api)

## Overview

`dcc-mcp-3dsmax` provides a Python API for:
- Embedding an MCP server inside 3ds Max
- Executing Python code on the 3ds Max main thread
- Managing skills (discovery, loading, execution)
- Providing helper functions for skill developers

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DCC_MCP_3DSMAX_DISABLE_EXECUTE_PYTHON` | `false` | Disable Python execution |
| `DCC_MCP_3DSMAX_DISABLE_EXECUTE_MAXSCRIPT` | `false` | Disable MAXScript execution |
| `DCC_MCP_3DSMAX_DISABLE_ARBITRARY_SCRIPT` | `false` | Disable arbitrary script execution |
| `DCC_MCP_3DSMAX_ENABLE_GATEWAY_FAILOVER` | `false` | Enable gateway failover |

Functions to resolve environment settings:

```python
from dcc_mcp_3dsmax import (
    resolve_execute_python_disabled,
    resolve_execute_maxscript_disabled,
    resolve_enable_gateway_failover,
)
```

## Version Detection

Functions to detect 3ds Max version:

```python
from dcc_mcp_3dsmax import (
    get_3dsmax_version_string,
    get_3dsmax_version_number,
    is_3dsmax_available,
)
```

### `get_3dsmax_version_string()`

Returns the 3ds Max version as a string, e.g., `"2024"`.

### `get_3dsmax_version_number()`

Returns the 3ds Max version as an integer, e.g., `26000` for 3ds Max 2024.

### `is_3dsmax_available()`

Returns `True` if running inside 3ds Max with `pymxs` available.

## API Helpers

Functions to simplify skill development:

```python
from dcc_mcp_3dsmax.api import (
    max_success,
    max_error,
    max_warning,
    max_from_exception,
    with_max,
    require_param,
    require_any_param,
    get_param,
    missing_param_error,
    is_max_available,
    get_runtime,
    get_selection,
    clear_selection,
    select_nodes,
)
```

### `max_success(message, **kwargs)`

Create a success response dict:

```python
return max_success(
    "Created box",
    node_name="Box001",
    width=100.0,
)
# Returns: {"success": True, "message": "Created box", "data": {...}}
```

### `max_error(error, **kwargs)`

Create an error response dict:

```python
return max_error(
    "Node not found",
    node_name="Nonexistent",
)
# Returns: {"success": False, "error": "Node not found", "data": {...}}
```

### `with_max(func)`

Deorator that ensures `pymxs.runtime` is available:

```python
from dcc_mcp_3dsmax.api import with_max, get_runtime

@with_max
def create_box(width=100.0, height=100.0):
    rt = get_runtime()
    box = rt.Box(width=width, height=height)
    return max_success("Created box", node_name=str(box.name))
```

### `require_param(params, name)`

Require a parameter or raise `MissingParamError`:

```python
from dcc_mcp_3dsmax.api import require_param

def main(node_name=None):
    params = {"node_name": node_name}
    node_name = require_param(params, "node_name")
    # If "node_name" not in params, returns error response automatically
```

### `get_runtime()`

Get the `pymxs.runtime` object (3ds Max Python runtime).

## Server API

Functions to start/stop the MCP server:

```python
from dcc_mcp_3dsmax import (
    start_server,
    stop_server,
    get_server,
    MaxServerOptions,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_PORT,
    SERVER_NAME,
)
```

### `start_server(port=0, options=None)`

Start the MCP server inside 3ds Max:

```python
from dcc_mcp_3dsmax import start_server

# Start on a random instance port and publish through http://127.0.0.1:9765/mcp
handle = start_server()

# Start on custom port
handle = start_server(port=8765)
```

### `stop_server()`

Stop the running MCP server:

```python
from dcc_mcp_3dsmax import stop_server
stop_server()
```

### `MaxServerOptions`

Configuration options for the server:

```python
from dcc_mcp_3dsmax import MaxServerOptions

options = MaxServerOptions(
    enable_metrics=True,
    job_persistence=True,
    workflow_engine=True,
)
handle = start_server(options=options)
```

## Dispatcher API

For advanced users who need to dispatch work to the 3ds Max main thread:

```python
from dcc_mcp_3dsmax.dispatcher import (
    MaxUiDispatcher,
    MaxStandaloneDispatcher,
    MaxDotNetTimerAdapter,
    MaxUiPump,
    create_dispatcher,
    create_pumped_dispatcher,
    check_3dsmax_cancelled,
)
```

### `create_dispatcher(budget_ms=8)`

Create the appropriate dispatcher for the current environment:

```python
from dcc_mcp_3dsmax.dispatcher import create_dispatcher

dispatcher, pump = create_dispatcher()

if pump:
    pump.install()  # Start the idle-event pump
```

### `create_pumped_dispatcher(budget_ms=8)`

Backward-compatible alias for `create_dispatcher()`. Interactive hosts return a
`MaxUiDispatcher` backed by `dcc-mcp-core` 0.18.2 `HostUiDispatcherBase` plus a
`MaxUiPump` wrapper around `HostPumpController`; standalone/headless contexts
return `MaxStandaloneDispatcher` and no pump.

## Runtime Bridge

Start the agent-callable runtime bridge inside 3ds Max:

```python
from dcc_mcp_3dsmax.max_bootstrap import main

main()
```

This starts the embedded adapter runtime, registers bundled 3ds Max tools, and
routes gateway `tools/call` scene edits through the shared core UI dispatcher.
The random-port main-thread HTTP bridge remains available for local diagnostics.
Normal MZP installs do not need `DCC_MCP_3DSMAX_PORT` or
`DCC_MCP_GATEWAY_PORT`.
See `docs/SIDECAR.md` for the full protocol.
