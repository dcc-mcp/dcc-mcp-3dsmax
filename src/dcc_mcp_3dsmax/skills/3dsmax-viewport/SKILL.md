---
name: 3dsmax-viewport
description: >-
  Domain skill - capture visual evidence from Autodesk 3ds Max: viewport
  screenshots, desktop and frame buffer (V-Ray / Corona / FStorm) captures,
  multi-view contact sheets, and a dedicated agent viewport with verified
  shading and display options that never moves the user's view. Use when the
  user asks for a screenshot, visual proof, or a README image. Not for
  rendering final frames.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: scene
    search-hint: "3ds Max viewport screenshot capture visual proof readme image frame buffer vfb vray corona fstorm multi view contact sheet agent viewport shading layout"
    tags: "3dsmax, viewport, screenshot, capture, visual, frame buffer, vfb, vray, corona, fstorm, multi-view, agent viewport, shading"
    tools: tools.yaml
    intent: "Capture visual evidence from the 3ds Max viewport, desktop, and frame buffers, and drive the dedicated agent viewport."
    search_aliases: ["viewport", "frame buffer", "vfb", "agent viewport"]
    recall_context:
      app_type: "3dsmax"
      domain: "viewport"
      workflow_stage: "authoring"
      task_category: "query"
    preconditions:
      - type: software
        name: "3ds Max"
        version: ">=2024"
    side_effects:
      creates: true
      modifies: true
      deletes: false
      exports: true
      imports: false
      file_output: true
      render: false
      targets: ["file:image", "viewport"]
    produces: ["file:image", "viewport_screenshot", "viewport_captures", "viewport_state"]
---

# 3ds Max Viewport Skill

Capture visual evidence from Autodesk 3ds Max and drive a viewport of the
agent's own. Tool contracts live in `tools.yaml`.

| Tool | What it does |
|---|---|
| `capture_viewport` | Capture the active viewport to an image file. |
| `capture_screen` | Capture the desktop, cropped to the V-Ray / Corona / FStorm / 3ds Max frame buffer. |
| `capture_multi_view` | Capture several standard views into separate images plus an optional contact sheet. |
| `agent_viewport` | Create, inspect, or close a dedicated floating viewport for agent work. |
| `set_viewport` | Set shading, layout, camera, and display toggles with verified writes. |

## No silent success

Every write in this skill is read back before it is reported:

* a crop the host cannot honour is a failure, never a full-desktop capture
  reported as a frame buffer capture
* a viewport option the host refuses is a failure with the rejected value
* a host that cannot report its state is reported as `unverified` in
  `data.warnings`, never as a success

## Leaving the user's view alone

`agent_viewport` and `capture_multi_view` both snapshot the active view before
they touch it. `capture_multi_view` refuses to switch views at all when the
host cannot report the view back, because an unverifiable restore is not a
restore. `set_viewport` defaults to `target=agent` and fails instead of
silently falling back to the user's viewport.
