---
name: 3dsmax-modeling
description: >-
  Domain skill - create basic primitives and bounded native Lathe geometry on
  the 3ds Max main thread. Use when adding boxes, spheres, cylinders, planes,
  or a rotational form from a typed profile. Not for mesh editing,
  import/export, or material assignment.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.1.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max create box sphere cylinder plane primitive lathe profile revolve geometry modeling position"
    tags: "3dsmax, modeling, geometry, primitives, lathe"
    tools: tools.yaml
    intent: "Create basic 3ds Max primitives and native Lathe profile geometry."
    search_aliases: ["modeling", "lathe", "revolve profile"]
    recall_context:
      app_type: "3dsmax"
      domain: "modeling"
      workflow_stage: "authoring"
      task_category: "mutate"
    preconditions:
      - type: software
        name: "3ds Max"
        version: ">=2024"
    side_effects:
      creates: true
      modifies: false
      deletes: false
      exports: false
      imports: false
      file_output: false
      render: false
      targets: ["scene_node", "primitive"]
    produces: ["scene_node:primitive", "scene_node:box", "scene_node:sphere", "scene_node:cylinder", "scene_node:plane", "scene_node:lathe"]
---

# 3ds Max Modeling Tools

Create basic primitive and Lathe geometry in the current 3ds Max scene. All
tools touch the live scene through `pymxs`, so they declare `affinity: main`.

`lathe_profile` accepts two to 256 `[radius, height]` pairs. Radius must be
non-negative; both values must be finite. It creates an XZ-plane spline and
uses the native Lathe axis. The action reads back the spline knot count and
modifier settings, and removes the new node if those postconditions differ.

Tool contracts live in `tools.yaml`. Scripts keep host API access behind
adapter helpers so metadata discovery remains safe outside 3ds Max.
