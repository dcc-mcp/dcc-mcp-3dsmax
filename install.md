# dcc-mcp-3dsmax Agent Install

Use this page when an agent is asked to install or configure
`dcc-mcp/dcc-mcp-3dsmax` for Autodesk 3ds Max.

3ds Max is Windows-only, so all paths and discovery below assume Windows.

## Agent Instruction

If the user asks an agent to install `dcc-mcp/dcc-mcp-3dsmax`, do this:

1. Clone or locate the repository and resolve its absolute path as `REPO_ROOT`.
2. Read `<REPO_ROOT>\install.md` and
   `<REPO_ROOT>\skills\dcc-mcp-3dsmax-setup\SKILL.md`.
3. Run the setup script from `REPO_ROOT`.
4. Install or verify the generated 3ds Max startup hook.
5. Configure the MCP host with the generated Streamable HTTP JSON.
6. Ask the user to open or restart 3ds Max, then run the smoke prompt to prove
   the connection works.

## One Command

From the absolute repository root (`REPO_ROOT`):

```bash
python skills/dcc-mcp-3dsmax-setup/scripts/setup_dcc_mcp_3dsmax.py
```

For an end-user install from PyPI instead of this checkout:

```bash
python skills/dcc-mcp-3dsmax-setup/scripts/setup_dcc_mcp_3dsmax.py --source pypi
```

If `3dsmaxpy.exe` is not auto-detected:

```bash
python skills/dcc-mcp-3dsmax-setup/scripts/setup_dcc_mcp_3dsmax.py --maxpy "C:\Program Files\Autodesk\3ds Max 2025\3dsmaxpy.exe"
```

The script installs `dcc_mcp_3dsmax_startup.ms` into 3ds Max's
`userStartupScripts` directory when it can resolve that path. If your studio
uses a custom profile location, pass it explicitly:

```bash
python skills/dcc-mcp-3dsmax-setup/scripts/setup_dcc_mcp_3dsmax.py --startup-dir "C:\Users\<you>\AppData\Local\Autodesk\3dsMax\2025 - 64bit\ENU\scripts\startup"
```

## 3ds Max Load Step

After the script finishes, open or restart 3ds Max. The installed startup hook
starts the runtime automatically and installs the `DCC MCP` menu.

If startup hook installation was skipped or the startup directory could not be
resolved, run the generated startup script from the MAXScript Listener:

```maxscript
python.ExecuteFile @"C:\path\to\dcc-mcp-3dsmax\.dcc-mcp\agent-setup\dcc_mcp_3dsmax_startup.ms"
```

Once the runtime has started once, the installed menu can restart it:

```text
DCC MCP > Start Server
```

The runtime registers the 3ds Max instance with the stable gateway. Both the
startup script and the menu call `dcc_mcp_3dsmax.main()`.

The shared gateway exposes MCP at:

```text
http://127.0.0.1:9765/mcp
```

Each 3ds Max instance also listens on its own random localhost port. Connect
MCP hosts to the gateway URL; the direct per-instance port is ephemeral.

## MCP Config

Use this JSON for Cursor, Claude Desktop, or any MCP Streamable HTTP host:

```json
{
  "mcpServers": {
    "3dsmax": {
      "url": "http://127.0.0.1:9765/mcp"
    }
  }
}
```

The setup script also writes a config snippet and a smoke prompt under:

```text
.dcc-mcp/agent-setup/
```
