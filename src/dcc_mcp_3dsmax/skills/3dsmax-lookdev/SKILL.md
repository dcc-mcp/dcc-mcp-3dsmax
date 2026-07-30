---
name: 3dsmax-lookdev
description: >-
  Domain skill - configure OCIO color management, HDR environment lighting, preview materials, and
  assign renderer-specific materials (Arnold, V-Ray, Scanline) in 3ds Max.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max lookdev HDR environment lighting material preview arnold v-ray scanline"
    tags: "3dsmax, lookdev, hdr, environment, lighting, material, arnold, vray"
    tools: tools.yaml
    intent: "Configure HDR environment lighting, preview materials, and assign renderer-specific materials."
    search_aliases: ["lookdev", "look development", "look dev"]
    recall_context:
      app_type: "3dsmax"
      domain: "lookdev"
      workflow_stage: "authoring"
      task_category: "mutate"
    preconditions:
      - type: software
        name: "3ds Max"
        version: ">=2024"
    side_effects:
      creates: false
      modifies: true
      deletes: false
      exports: false
      imports: false
      file_output: false
      render: false
      targets: ["scene_node", "environment", "material"]
    produces: ["environment_lighting", "material_assignment"]
---

# 3ds Max Look Development Skill

Configure environment lighting from HDR/EXR images, set up a three-point light
rig, preview materials on test geometry, and assign materials with renderer
detection (Arnold, V-Ray, Scanline).

## Tools

- **`get_color_management` / `set_color_management`** — Inspect or configure the
  3ds Max 2024+ scene OCIO config and scene-linear rendering color space.
- **`setup_hdr_lighting`** — Load an HDR/EXR image as the environment map,
  configure rotation and intensity, and optionally create a three-point light
  rig.
- **`set_hdri_rotation`** — Rotate the active environment for a lighting
  turntable without rebuilding the rig.
- **`preview_material`** — Create a test sphere or quad, apply a named scene
  material, and report the viewport-visible result.
- **`assign_renderer_material`** — Detect the active renderer
  (Arnold/V-Ray/Scanline), create or find a suitable material class, apply it
  to named nodes, and return a summary.
