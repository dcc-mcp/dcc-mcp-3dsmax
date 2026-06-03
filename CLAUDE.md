# CLAUDE.md — Claude Desktop / Anthropic API Integration Guide

> Claude-specific integration notes for `dcc-mcp-3dsmax`.
> For the full project map, see [AGENTS.md](AGENTS.md).

---

## What This Project Does

`dcc-mcp-3dsmax` embeds an MCP Streamable HTTP server directly inside Autodesk 3ds Max. Claude Desktop (or any Anthropic API client using MCP) can call ~130 3ds Max tools over HTTP — scene manipulation, modeling, materials, animation, rendering, and more.

---

## Claude Desktop Configuration

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "3dsmax": {
      "url": "http://127.0.0.1:9765/mcp"
    }
  }
}
```

**File locations:**
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

Restart Claude Desktop after editing.

---

## Progressive Loading

By default, only `3dsmax-scene` skills are fully loaded. **All other skills appear as stubs.** When Claude needs a tool from an unloaded skill:

1. Call `load_skill("3dsmax-modeling")` to expand the skill.
2. Then call the typed tool (e.g., `3dsmax_modeling__create_box`).

This keeps the initial `tools/list` small and fast for Claude to parse.

---

## Claude-Specific Tips

- **Viewport feedback:** Call `capture_viewport` after geometry changes. The base64 PNG lets Claude "see" the current state.
- **Code execution:** Prefer `search_skills` → `load_skill` → typed tools with `inputSchema`. Use `execute_python` only as last resort.
- **Security:** Operators can block arbitrary scripts with `DCC_MCP_3DSMAX_DISABLE_ARBITRARY_SCRIPT=1`.
- **Animation:** Use `3dsmax_animation__set_keyframe` for keyframing, `bake_transform_animation` for baking.

---

## Quick Test Prompts

> "Create a box in 3ds Max with dimensions 50x50x50"
> "List all cameras in the scene"
> "Capture the viewport so I can see the current state"
> "Import an FBX file and clean up the mesh"

---

## See Also

- [AGENTS.md](AGENTS.md) — Shared agent navigation map
- [llms.txt](llms.txt) — One-page core reference
- [README.md](README.md) — Human-facing installation and overview
