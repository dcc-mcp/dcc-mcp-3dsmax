---
name: 3dsmax-render
description: >-
  Domain skill - capture viewports, render scenes to image files, create
  preview playblasts, configure renderers (Arnold, V-Ray, Scanline), produce
  HDR/EXR output and multi-pass AOV renders, inspect render settings and
  statistics, and adjust common render output options in 3ds Max.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max render viewport capture scene image playblast preview HDR EXR multi-pass AOV render elements arnold vray scanline settings resolution frame range camera quality"
    tags: "3dsmax, render, viewport, capture, playblast, camera, image, hdr, exr, aov, multi-pass, renderer"
    tools: tools.yaml
    intent: "Capture viewports, render scenes, create preview playblasts, configure renderers, and manage 3ds Max render settings."
    search_aliases: ["rendering", "render"]
    recall_context:
      app_type: "3dsmax"
      domain: "rendering"
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
      exports: true
      imports: false
      file_output: true
      render: true
      targets: ["file:image", "file:video", "render_settings", "hdr", "aov"]
    produces: ["file:image", "file:preview", "render_settings", "file:hdr_image", "file:multi_pass_image", "renderer_config"]
---

# 3ds Max Render and Viewport Skill

Capture viewport evidence, render scenes to image files, generate previews,
inspect render settings, and change common render output options through
`pymxs`.

Output-producing tools validate extensions, parent directories, and overwrite
behavior before invoking host operations, then return artifact metadata.
