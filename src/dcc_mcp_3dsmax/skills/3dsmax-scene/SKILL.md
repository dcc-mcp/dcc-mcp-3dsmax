---
name: 3dsmax-scene
description: >-
  Domain skill - inspect and manage scene objects in the current Autodesk
  3ds Max session. Use when the user asks about nodes, cameras, selection,
  visibility, parenting, grouping, pivots, transforms, scene metadata, or
  object cleanup.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: scene
    search-hint: "3ds Max scene nodes cameras selection bounding boxes visibility parenting grouping pivots transforms metadata"
    tags: "3dsmax, scene, nodes, cameras, selection, visibility, parenting, grouping, transforms"
    tools: tools.yaml
    intent: "Inspect and manage 3ds Max scene objects, selection, visibility, parenting, grouping, and transforms."
    search_aliases: ["scene", "scene"]
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
      imports: false
      file_output: false
      render: false
      targets: ["scene_node", "group", "selection", "pivot"]
    produces: ["scene_info", "node_list", "selection_state", "bounding_box", "visibility_state"]
---

# 3ds Max Scene and Object Skill

Inspect and manage scene-level objects through `pymxs`. Tool contracts live in
`tools.yaml`; every tool declares `affinity: main` because even read-only
queries enter the 3ds Max host API.

Use the read tools to list nodes and cameras, inspect selection, query
bounding boxes and visibility, and retrieve session metadata. Save explicitly
with `save_scene`. Use the remaining mutation tools for targeted selection,
duplication, deletion, grouping, parenting, visibility changes, pivot centering,
and transform freezing.

Node-targeted tools accept explicit node names or stable object handles and
return structured not-found or ambiguous-match errors instead of guessing.
