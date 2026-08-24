---
name: 3dsmax-scene
description: >-
  Domain skill - open, save, merge, inspect, and manage scenes and objects in
  the current Autodesk 3ds Max session. Use for typed scene lifecycle, nodes,
  cameras, selection, visibility, parenting, grouping, pivots, transforms,
  scene metadata, or object cleanup.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: scene
    search-hint: "3ds Max new open save save-as merge scene status dirty nodes cameras selection visibility parenting transforms"
    tags: "3dsmax, scene, lifecycle, open, save, merge, nodes, cameras, selection, visibility, transforms"
    tools: tools.yaml
    intent: "Run verified scene lifecycle operations and manage 3ds Max scene objects."
    search_aliases: ["scene", "scene io", "open max", "save max", "merge max"]
    recall_context:
      app_type: "3dsmax"
      domain: "scene"
      workflow_stage: "authoring"
      task_category: "mutate"
    preconditions:
      - type: software
        name: "3ds Max"
        version: ">=2024"
    side_effects:
      creates: true
      modifies: true
      deletes: true
      exports: false
      imports: true
      file_output: true
      render: false
      targets: ["scene", "scene_file", "scene_node", "group", "selection", "pivot"]
    produces: ["scene_status", "scene_file", "scene_info", "node_list", "selection_state", "bounding_box", "visibility_state"]
---

# 3ds Max Scene and Object Skill

Inspect and manage scene-level objects through `pymxs`. Tool contracts live in
`tools.yaml`; every tool declares `affinity: main` because even read-only
queries enter the 3ds Max host API.

Use `get_scene_status` before and after file operations. `new_scene` and
`open_scene` reject dirty scenes unless `force=true`; that flag explicitly
authorizes discarding unsaved changes. Use `save_scene` for the current file,
`save_scene_as` for an explicit path, and `merge_file` for bounded no-prompt
merges. Each lifecycle mutation returns native post-condition readback rather
than treating the native API return value as sufficient proof.

File operations run on the 3ds Max main thread. Open, save, save-as, and merge
are monolithic async jobs because the native calls are indivisible: poll the
returned Core job instead of retrying after a transport timeout. No lifecycle
tool opens a file dialog or falls back to UI automation or arbitrary scripts.

Use the remaining tools to inspect nodes and cameras and perform targeted
selection, duplication, deletion, grouping, parenting, visibility, pivot, and
transform operations.

Node-targeted tools accept explicit node names or stable object handles and
return structured not-found or ambiguous-match errors instead of guessing.
