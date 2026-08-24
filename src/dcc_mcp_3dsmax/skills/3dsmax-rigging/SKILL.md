---
name: 3dsmax-rigging
description: >-
  Domain skill - create and inspect host-native 3ds Max rig helpers, bones,
  constraints, controls, poses, native Skin weights, and common deformers.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max rigging helper bone joint chain constraint control pose skin vertex weights modifier path helper"
    tags: "3dsmax, rigging, bones, constraints, deformers"
    tools: tools.yaml
    intent: "Create and inspect 3ds Max rig helpers, bones, constraints, and deformer modifiers."
    search_aliases: ["rigging", "rigging"]
    recall_context:
      app_type: "3dsmax"
      domain: "rigging"
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
      targets: ["scene_node", "bone", "helper", "constraint", "deformer"]
    produces: ["scene_node:bone", "scene_node:helper", "constraint", "deformer_modifier"]
---

# 3ds Max Rigging Tools

Create lightweight rig helpers and controls, build simple bone chains, inspect
typed rig state, exchange controller poses, and read, replace, copy, export, or
import native Skin weights. Weight and pose mutations preflight the complete
request and require host readback; weight batches roll back on verification
failure. File exchange accepts only explicit absolute `.json` paths and can
verify SHA-256 on import. Export refuses to replace an existing file unless
`overwrite=true` is explicit.

Tools operate on explicit node references or explicit `use_selection=true` and
declare `affinity: main` for 3ds Max scene access. `create_constraint` provides
the shared `point | orient | aim | parent` names while the older
`set_constraint_target` surface remains backward compatible.

Character-system helpers are limited to availability checks so optional biped
or CAT support can fail gracefully when the host installation does not expose
those APIs.

`dcc-mcp/rig-state@1`, `dcc-mcp/pose@1`, and the Skin weight JSON shape are
thin adapter contracts pending the shared Core verification owner tracked by
`dcc-mcp-core#2261`.
