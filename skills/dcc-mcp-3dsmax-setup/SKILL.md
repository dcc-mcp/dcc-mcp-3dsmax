---
name: dcc-mcp-3dsmax-setup
description: |-
  Set up dcc-mcp-3dsmax for an agent or operator: install 3ds Max Python
  dependencies with 3dsmaxpy, generate MCP host configuration, guide the user
  through starting the runtime inside 3ds Max, and run a first live-tool smoke
  prompt.
license: MIT
allowed-tools: Bash Read
metadata:
  dcc-mcp:
    dcc: 3dsmax
    layer: operator
    stage: bootstrap
    version: 1.1.0
    tags:
    - 3dsmax
    - mcp
    - setup
    - 3dsmaxpy
    - sidecar
---
# dcc-mcp-3dsmax setup

Use this skill when a user wants an agent to prepare a machine so any MCP
host can use `dcc-mcp-3dsmax` with Autodesk 3ds Max.

This is an operator skill, not a 3ds Max runtime skill. Do not load it through
the 3ds Max MCP server. Run it from the repository checkout or copy its steps
into another agent's instructions.

3ds Max is Windows-only, so discovery and paths below assume Windows.

If the user asks an agent to install `dcc-mcp/dcc-mcp-3dsmax`, first clone or
locate the repository and resolve its absolute path as `REPO_ROOT`. Read
`<REPO_ROOT>\install.md` and this skill at
`<REPO_ROOT>\skills\dcc-mcp-3dsmax-setup\SKILL.md`, then follow the workflow
below.

## Goal

End with:

- `dcc-mcp-3dsmax` and its pip dependencies installed into the target 3ds Max
  `3dsmaxpy` environment.
- A 3ds Max startup hook installed into `userStartupScripts` when the directory
  can be resolved, so opening or restarting 3ds Max starts the runtime
  automatically.
- An MCP host config snippet that points to the 3ds Max gateway.
- A generated fallback startup script under `.dcc-mcp/agent-setup/` for
  machines where the startup directory cannot be auto-detected.
- A live smoke prompt that proves the agent can discover and call 3ds Max tools.

## Fast Path

Install the public lifecycle CLI, then plan with explicit paths before any
mutation:

```powershell
python -m pip install --upgrade dcc-mcp-3dsmax
dcc-mcp-3dsmax install --json --dry-run `
  --dcc-path "C:\Program Files\Autodesk\3ds Max 2025" `
  --python "C:\Program Files\Autodesk\3ds Max 2025\Python\python.exe"
```

Inspect the one-object Install SOP v1 report. With user consent, execute the
same plan using `--yes`, preserve the receipt, follow a restart next step if
present, and run `dcc-mcp-3dsmax verify --json` with the same paths. Only
`verify.directly_usable=true` proves the typed sidecar probe succeeded.

The standard CLI:

1. exposes `install`, `status`, `verify`, `upgrade`, and `uninstall`;
2. records the selected host and interpreter in the plan and receipt;
3. stages the startup hook and receipt before commit and restores their prior
   bytes when commit fails;
4. fails closed on unreceipted or modified state;
5. captures startup failures before MCP exists and performs bounded typed
   readiness verification.

For checkout development only, the legacy setup helper can still generate MCP
configuration and a smoke prompt. It is not the receipt-owning public CLI:

```bash
python skills/dcc-mcp-3dsmax-setup/scripts/setup_dcc_mcp_3dsmax.py --source local
```

If discovery fails, ask for the full host and interpreter paths. For a custom
profile, also pass the startup directory:

```powershell
dcc-mcp-3dsmax install --json --dry-run `
  --dcc-path "C:\Program Files\Autodesk\3ds Max 2025" `
  --python "C:\Program Files\Autodesk\3ds Max 2025\3dsmaxpy.exe" `
  --startup-dir "C:\Users\<you>\AppData\Local\Autodesk\3dsMax\2025 - 64bit\ENU\scripts\startup"
```

## MCP Configuration

The shared gateway is the preferred default. Configure the MCP host with:

```json
{
  "mcpServers": {
    "3dsmax": {
      "url": "http://127.0.0.1:9765/mcp"
    }
  }
}
```

Each 3ds Max instance also listens on its own random localhost port; connect
hosts to the gateway URL rather than the ephemeral per-instance port.

When editing an existing MCP config, preserve unrelated servers. Merge only the
`3dsmax` server entry unless the user asks for a different server name.

## User Hand-Off: Start the Runtime in 3ds Max

After setup, tell the user to open or restart 3ds Max. The installed startup
hook should start the runtime automatically, install the `DCC MCP` menu, and
register the instance with the shared gateway.

If startup-dir detection failed or `--no-startup-hook` was used, tell the user
to run the generated startup script once from the MAXScript Listener:

```maxscript
python.ExecuteFile @"C:\path\to\dcc-mcp-3dsmax\.dcc-mcp\agent-setup\dcc_mcp_3dsmax_startup.ms"
```

Once the runtime has started once, the installed `DCC MCP` menu offers a
`Start Server` command. Both call `dcc_mcp_3dsmax.main()` and register the
instance with the gateway at `http://127.0.0.1:9765/mcp`.

## First Live Smoke Prompt

Ask the MCP host to run this prompt after 3ds Max is open and the runtime is
started:

```text
Use the 3ds Max MCP server. First call dcc_capability_manifest with loaded_only=false.
Then load the 3dsmax-modeling skill, create a sphere named mcp_setup_smoke_sphere
with radius 50, and tell me the MCP URL and created node name.
Use typed tools where available and avoid execute_python unless no typed tool fits.
```

Expected behavior:

- The agent discovers capabilities without dumping every schema.
- The agent loads `3dsmax-modeling`.
- The agent calls `3dsmax_modeling__create_sphere`.
- The new node appears in the 3ds Max scene.

## Troubleshooting

- `3dsmaxpy` not found: ask for the exact 3ds Max version and the full
  `3dsmaxpy.exe` path (e.g. `C:\Program Files\Autodesk\3ds Max 2025\3dsmaxpy.exe`).
- Pip bootstrap fails: run `3dsmaxpy -m ensurepip --upgrade`, then repeat install.
- MCP connection refused: 3ds Max is not running, the runtime is not started, or
  the host is not pointing at the gateway URL `http://127.0.0.1:9765/mcp`.
- Tool missing: call `dcc_capability_manifest` or `search_skills`, then
  `load_skill("<skill-name>")`.
- Runtime started but hangs: check the MAXScript Listener output, firewall /
  localhost rules, and whether a blocking 3ds Max dialog is open.
