---
name: 3dsmax-camera-lighting
description: >-
  Domain skill - create, inspect, and adjust 3ds Max cameras, lights, and
  simple review lighting rigs.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max camera light lighting three point rig active render camera intensity color shadows"
    tags: "3dsmax, camera, lighting, render-preview"
    tools: tools.yaml
    intent: "Create, inspect, and configure 3ds Max cameras, lights, and review lighting rigs."
    search_aliases: ["camera_lighting", "camera-lighting"]
    recall_context:
      app_type: "3dsmax"
      domain: "camera_lighting"
      workflow_stage: "authoring"
      task_category: "mutate"
    preconditions:
      - type: software
        name: "3ds Max"
        version: ">=2024"
    side_effects:
      creates: true
      modifies: true
      deletes: false
      exports: false
      imports: false
      file_output: false
      render: false
      targets: ["scene_node", "camera", "light"]
    produces: ["scene_node:camera", "scene_node:light", "lighting_rig"]
---

# 3ds Max Camera And Lighting Tools

Create cameras and basic lights, inspect camera/light properties, set the
active render camera, and build a simple three-point review light rig. Tools
validate camera and light targets before scene mutation and keep renderer
options generic.

## V-Ray lights

`create_vray_light` builds V-Ray lights of any supported shape in one
transaction (up to 32 lights per call):

| Control | Native property candidates | Values |
|---|---|---|
| shape | `type`, `shape` | `rectangle`/`plane` = 0, `environment`/`dome` = 1, `sphere` = 2, `mesh` = 3, `disk`/`disc` = 4 |
| units | `units` | `renderer` = 0, `lm` = 1, `cd_m2` = 2, `w` = 3, `radiance` = 4 |
| multiplier | `multiplier`, `intensity` | float |
| color | `color` | RGB 0-255 |
| shadows | `castShadows` | bool |
| normalize color | `normalizeColor` | bool |
| targeted | `targeted` | bool |
| local size | `U_size`/`V_size` (then `sizeU`/`sizeV`, `size0`/`size1`) | float |
| dome texture | `texmap` (then `dome_tex`) + `VRayBitmap` | `map_type`, `gamma`, `color_space`, `horizontal_rotation` |

Every requested control is read back from the host. A control that is missing,
rejected, or silently ignored fails the whole call and rolls every light created
in that call back.
