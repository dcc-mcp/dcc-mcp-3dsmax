# Google Gemini / Vertex AI integration

> Gemini-specific integration notes for `dcc-mcp-3dsmax`.
> For the full project map, see [`AGENTS.md`](../../AGENTS.md).

## What this project does

`dcc-mcp-3dsmax` embeds an MCP Streamable HTTP server directly inside Autodesk
3ds Max. Gemini (via an MCP-compatible client or custom integration) can
discover and invoke the bundled 3ds Max tools over HTTP.

## Integration setup

If your Gemini client supports MCP over HTTP, configure:

```
Endpoint: http://127.0.0.1:9765/mcp
Protocol: MCP Streamable HTTP (2025-03-26 spec)
```

## Gemini-specific tips

- **Code-first workflows:** Gemini excels at generating structured output. Use
  it to plan multi-step 3ds Max workflows.
- **Viewport capture:** feed `capture_viewport` base64 PNGs back to Gemini for
  visual state verification.
- **Batch operations:** use `3dsmax_animation__bake_transform_animation` and
  similar batch tools for efficient processing.
- **Validation chains:** Gemini can validate scene readiness by chaining
  `validate_naming` → `validate_transforms` → `validate_mesh_topology` →
  `run_asset_readiness_checks`.

## Quick test prompts

> "Create a three-point lighting setup in the scene"
> "List all materials and find ones with missing textures"
> "Validate the selected objects for asset readiness"

## See also

- [`AGENTS.md`](../../AGENTS.md) — shared agent navigation map
- [`../integrations/claude.md`](claude.md) — Claude integration notes
- [`llms.txt`](../../llms.txt) — one-page core reference
- [`README.md`](../../README.md) — human-facing installation and overview
