# GEMINI.md — Google Gemini / Vertex AI Integration Guide

> Gemini-specific integration notes for `dcc-mcp-3dsmax`.
> For the full project map, see [AGENTS.md](AGENTS.md).

---

## What This Project Does

`dcc-mcp-3dsmax` embeds an MCP Streamable HTTP server directly inside Autodesk 3ds Max. Gemini (via an MCP-compatible client or custom integration) can discover and invoke ~130 3ds Max tools over HTTP.

---

## Integration Setup

If your Gemini client supports MCP over HTTP, configure:

```
Endpoint: http://127.0.0.1:9765/mcp
Protocol: MCP Streamable HTTP (2025-03-26 spec)
```

---

## Gemini-Specific Tips

- **Code-first workflows:** Gemini excels at generating structured output. Use it to plan multi-step 3ds Max workflows.
- **Viewport capture:** Feed `capture_viewport` base64 PNGs back to Gemini for visual state verification.
- **Batch operations:** Use `3dsmax_animation__bake_transform_animation` and similar batch tools for efficient processing.
- **Validation chains:** Gemini can validate scene readiness by chaining `validate_naming` → `validate_transforms` → `validate_mesh_topology` → `run_asset_readiness_checks`.

---

## Quick Test Prompts

> "Create a three-point lighting setup in the scene"
> "List all materials and find ones with missing textures"
> "Validate the selected objects for asset readiness"

---

## See Also

- [AGENTS.md](AGENTS.md) — Shared agent navigation map
- [llms.txt](llms.txt) — One-page core reference
- [README.md](README.md) — Human-facing installation and overview
