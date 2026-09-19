---
name: 3dsmax-mesh-ops
description: >-
  Domain skill - inspect and mutate 3ds Max mesh topology, cleanup, smoothing
  groups, modifier stacks, proxy meshes, and explicit normals through atomic
  host-native operations.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max mesh cleanup topology normals smoothing groups modifiers triangulate attach detach proxy subdivision add remove modifier stack collapse make unique modifier properties viewport render enable"
    tags: "3dsmax, mesh, topology, cleanup, normals, smoothing, modifiers, modifier_stack"
    tools: tools.yaml
    intent: "Inspect and mutate 3ds Max mesh topology, cleanup, smoothing groups, modifiers, and normals."
    search_aliases: ["mesh_operations", "mesh-ops"]
    recall_context:
      app_type: "3dsmax"
      domain: "mesh_operations"
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
      targets: ["mesh", "scene_node", "modifier", "smoothing_group"]
    produces: ["mesh_topology", "smoothing_group", "modifier_stack", "modifier_parameters", "proxy_mesh"]
---

# 3ds Max Mesh Operations Skill

Inspect mesh topology and apply focused mesh cleanup, subdivision, proxy, attach,
detach, smoothing group, and normal operations through `pymxs`. Also provides
general modifier stack CRUD: add, remove, enable/disable (viewport and render
granularity), set properties, collapse, and make-unique.

Mutating tools require explicit node names, stable object handles, or an
explicit `use_selection=true` argument. They return changed-node summaries so
agents can report what changed without relying on opaque macros.

## No silent success

Write paths never report success for a value the host did not accept. Every
property write is read back and compared, every add/remove is verified against
the stack length, and a host that exposes no usable entry point returns an
error. When a host runs an operation that cannot be confirmed programmatically
(`make_modifier_unique`), the result carries an explicit `warning` instead of a
bare success.
