# AGENTS.md — dcc-mcp-3dsmax Agent Navigation Map

> Progressive disclosure: this file is a **map**, not an encyclopedia.
> Follow the links for depth. Stay here for breadth.

## Agent Control Path

AI agent runtimes default to the shared gateway through the
`dcc-mcp` skill and `dcc-mcp-cli` REST commands:

```bash
dcc-mcp-cli search --query "<task>" --dcc-type 3dsmax
dcc-mcp-cli describe <tool-slug>
dcc-mcp-cli call <tool-slug> --json '{"key":"value"}'
```

Use `dcc-mcp-cli list` for live instances and `dcc-mcp-cli dcc-types` for
release-catalog support. IDE users may continue to configure the gateway MCP
endpoint; adapter-local Python start APIs are for host bootstrap and tests.

### CLI availability and updates

If `dcc-mcp-cli` is missing, obtain user consent before using the official
install commands in the README Agent workflow. Keep an official build current
with:

```bash
dcc-mcp-cli update check
dcc-mcp-cli update apply
```

`update apply` stages the latest CLI for the next launch; it does not replace
a running server.

---

## 30-Second Summary

`dcc-mcp-3dsmax` embeds a standards-compliant MCP Streamable HTTP server directly inside Autodesk 3ds Max. It exposes ~130 3ds Max operations as MCP tools that any AI agent (Claude, Cursor, Gemini, etc.) can call over HTTP.

**Current version:** 0.1.21 <!-- x-release-please-version -->
**Core dependency:** `dcc-mcp-core>=0.20.0,<1.0.0`
**Python:** 3.7+
**3ds Max:** 2017+ (with pymxs)

---

## Quick Start (3 Lines)

```python
import dcc_mcp_3dsmax
server = dcc_mcp_3dsmax.start_server()
# MCP client connects to http://127.0.0.1:9765/mcp
```

---

## Information Layers — Pick Your Depth

### Layer 1 — You Are a User / Operator
- **README.md** — Installation, features, environment variables, quick start.
- **install.md** — Agent-facing setup instructions.
- **skills/dcc-mcp-3dsmax-setup/SKILL.md** — Automated setup skill.
- **docs/BUNDLED_SKILLS.md** — Complete inventory of all 15 skill families and ~130 tools.
- **docs/SIDECAR.md** — Runtime bridge protocol.
- **examples/** — start_server.py, start_sidecar_bridge.py.

### Layer 2 — You Are a Skill Author
- **docs/SKILL_DEVELOPMENT.md** — Skill package layout, SKILL.md format, action script rules.
- **docs/API.md** — Python API reference for skill developers.
- **src/dcc_mcp_3dsmax/api.py** — Authoring helpers (`max_success`, `with_max`, `require_param`, ...).
- **Key pattern:** Lazy-import `pymxs` inside the function.

```python
from dcc_mcp_3dsmax.api import max_success, max_error, with_max

@with_max
def create_box(width: float = 100.0, height: float = 100.0, depth: float = 100.0) -> dict:
    import pymxs
    rt = pymxs.runtime
    box_obj = rt.Box(width=width, height=height, depth=depth)
    return max_success("Created box", object_name=str(box_obj))
```

### Layer 3 — You Are a Core Developer
- **src/dcc_mcp_3dsmax/server.py** — `MaxMcpServer` composition root.
- **src/dcc_mcp_3dsmax/_env.py** — All `DCC_MCP_3DSMAX_*` env-var resolution.
- **src/dcc_mcp_3dsmax/dispatcher/** — Thread-affinity dispatchers.
- **src/dcc_mcp_3dsmax/context_snapshot.py** — Real-time scene state.
- **src/dcc_mcp_3dsmax/capabilities.py** — DCC capability reporting.
- **tests/** — 24 unit tests.
- **Upstream `dcc-mcp-core` reference:** https://github.com/dcc-mcp/dcc-mcp-core/blob/main/llms.txt

### Layer 4 — You Are an AI Agent
- **llms.txt** — Core API surface, env vars, bundled tools (compact).
- **llms-full.txt** — Exhaustive API reference.
- **Skill discovery workflow:**
  1. `dcc_capability_manifest({loaded_only: false})` for compact index.
  2. `search_skills(query="scene")` → `load_skill("3dsmax-scene")` → typed tool.
  3. Use `3dsmax-scripting__execute_python` only as last resort.

---

## Bundled Skills (15 families, ~130 tools)

| Skill | Stage | Tools |
|-------|-------|-------|
| `3dsmax-scene` | scene | `get_scene_info`, `list_scene_nodes`, `list_cameras`, `get_selection`, `get_bounding_box`, `get_node_visibility`, `get_scene_metadata`, `set_selection`, `duplicate_nodes`, `delete_nodes`, `group_nodes`, `parent_node`, `unparent_node`, `set_visibility`, `center_pivots`, `freeze_transforms` |
| `3dsmax-scripting` | authoring | `execute_python`, `execute_maxscript`, `run_python_check`, `list_runtime_symbols`, `inspect_runtime_symbol`, `list_macros`, `resolve_node_reference`, `reload_adapter_module` |
| `3dsmax-modeling` | authoring | `create_box`, `create_sphere`, `create_cylinder`, `create_plane` |
| `3dsmax-geometry-io` | authoring | `validate_geometry_file`, `import_fbx`, `import_geometry`, `export_fbx`, `export_obj` |
| `3dsmax-mesh-ops` | authoring | `get_mesh_topology`, `get_selected_mesh_topology`, `get_smoothing_groups`, `get_modifier_stack`, `triangulate_meshes`, `cleanup_meshes`, `attach_meshes`, `detach_selected_faces`, `apply_subdivision`, `create_proxy_meshes`, `set_explicit_normals`, `clear_explicit_normals`, `assign_smoothing_group` |
| `3dsmax-uv-atlas` | authoring | `list_uv_channels`, `create_uv_channel`, `delete_uv_channel`, `copy_uv_channel`, `get_uv_shell_summary`, `apply_uv_projection`, `unwrap_uvs`, `pack_uvs`, `detect_uv_overlaps`, `normalize_uvs`, `prepare_texture_atlas` |
| `3dsmax-materials` | authoring | `create_standard_material`, `apply_material`, `list_scene_materials`, `list_node_material_assignments`, `inspect_material`, `list_bitmap_connections`, `create_physical_material`, `create_pbr_material`, `reset_material`, `set_material_attributes`, `assign_bitmap_texture`, `report_missing_textures` |
| `3dsmax-animation` | authoring | `set_keyframe`, `play_animation`, `get_time_settings`, `get_animation_controllers`, `list_keyframes`, `set_current_time`, `set_timeline_settings`, `set_transform_keyframe`, `delete_keyframes`, `set_key_interpolation`, `bake_transform_animation`, `export_animation_curves`, `import_animation_curves` |
| `3dsmax-rigging` | authoring | `create_helper_node`, `create_bone_node`, `create_joint_chain`, `create_path_helper`, `list_rig_state`, `apply_deformer_modifier`, `remove_deformer_modifier`, `set_constraint_target`, `get_character_system_availability` |
| `3dsmax-render` | authoring | `capture_viewport`, `create_preview`, `get_render_settings`, `get_scene_render_statistics`, `set_render_output_options`, `set_frame_range`, `set_render_resolution`, `set_render_camera`, `set_render_quality_preset` |
| `3dsmax-transform` | authoring | `set_node_position`, `move_nodes` |
| `3dsmax-viewport` | authoring | `capture_viewport` |
| `3dsmax-validation` | authoring | `validate_naming`, `validate_transforms`, `validate_pivots`, `validate_mesh_topology`, `validate_smoothing_groups`, `validate_material_assignments`, `validate_texture_paths`, `validate_uv_channels`, `validate_uv_overlaps`, `run_asset_readiness_checks` |
| `3dsmax-display` | authoring | `list_layers`, `create_layer`, `delete_layer`, `assign_nodes_to_layer`, `list_node_display_state`, `set_node_display_state`, `list_custom_properties`, `get_custom_property`, `set_custom_property`, `delete_custom_property` |
| `3dsmax-camera-lighting` | authoring | `list_cameras`, `list_lights`, `create_camera`, `set_active_camera`, `create_light`, `set_light_properties`, `create_three_point_light_rig` |

---

## Key Env Vars

| Variable | Default | Purpose |
|----------|---------|---------|
| `DCC_MCP_3DSMAX_METRICS` | `0` | Enable Prometheus `/metrics` endpoint |
| `DCC_MCP_3DSMAX_JOB_STORAGE` | platform default | SQLite job database path |
| `DCC_MCP_3DSMAX_DISABLE_EXECUTE_PYTHON` | `0` | Disable `execute_python` tool |
| `DCC_MCP_3DSMAX_DISABLE_ARBITRARY_SCRIPT` | `0` | Disable all arbitrary script execution |
| `DCC_MCP_3DSMAX_ENABLE_GATEWAY_FAILOVER` | `1` | Enable gateway failover |
| `DCC_MCP_3DSMAX_QT_UI_INSPECTOR` | `0` | Enable Qt UI inspector tools |
| `DCC_MCP_3DSMAX_SEMANTIC_INDEX` | `0` | Enable semantic skill recall |
| `DCC_MCP_3DSMAX_PROJECT_TOOLS` | `1` | Enable project state tools |
| `DCC_MCP_3DSMAX_RESOURCES` | `1` | Enable `scene://current` resource |
| `DCC_MCP_3DSMAX_SKILL_PATHS` | None | Extra skill search paths |
| `DCC_MCP_GATEWAY_PORT` | `9765` | Gateway election port |

---

## File Index (Agent Quick-Look)

| File | Role |
|------|------|
| `README.md` | Human-facing overview, installation, config |
| `llms.txt` | Condensed AI reference (compact) |
| `llms-full.txt` | Exhaustive API reference |
| `install.md` | Agent-facing install instructions |
| `docs/API.md` | Python API reference |
| `docs/BUNDLED_SKILLS.md` | Complete tool inventory |
| `docs/SIDECAR.md` | Runtime bridge protocol |
| `docs/SKILL_DEVELOPMENT.md` | Skill authoring guide |
| `src/dcc_mcp_3dsmax/__init__.py` | Public API exports |
| `src/dcc_mcp_3dsmax/server.py` | `MaxMcpServer` — lifecycle, discovery |
| `src/dcc_mcp_3dsmax/_env.py` | `DCC_MCP_3DSMAX_*` env-var helpers |
| `src/dcc_mcp_3dsmax/api.py` | Skill authoring helpers |
| `src/dcc_mcp_3dsmax/skills/` | 15 bundled skill packages |
| `skills/dcc-mcp-3dsmax-setup/` | Agent-facing setup skill |
| `.github/workflows/ci.yml` | CI pipeline (lint + test + build) |
| `packaging/assemble_mzp.py` | MZP installer builder |
| `tests/` | 24 unit test files |
| `justfile` | Task runner |



