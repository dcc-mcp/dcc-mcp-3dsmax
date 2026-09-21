---
name: 3dsmax-mesh-ops
description: >-
  Domain skill - inspect and mutate 3ds Max mesh topology, cleanup, smoothing
  groups, modifier stacks, proxy meshes, explicit normals, and native boolean
  solids through atomic host-native operations.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.1.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max mesh cleanup topology normals smoothing groups modifiers triangulate attach detach proxy subdivision add remove modifier stack collapse make unique modifier properties viewport render enable boolean union intersection subtraction cut operand extract"
    tags: "3dsmax, mesh, topology, cleanup, normals, smoothing, modifiers, modifier_stack, boolean"
    tools: tools.yaml
    intent: "Inspect and mutate 3ds Max mesh topology, cleanup, smoothing groups, modifiers, and normals."
    search_aliases: ["mesh_operations", "mesh-ops", "boolean"]
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
    produces: ["mesh_topology", "smoothing_group", "modifier_stack", "modifier_parameters", "proxy_mesh", "boolean_state"]
---

# 3ds Max Mesh Operations Skill

Inspect mesh topology and apply focused mesh cleanup, subdivision, proxy, attach,
detach, smoothing group, and normal operations through `pymxs`. Also provides
general modifier stack CRUD: add, remove, enable/disable (viewport and render
granularity), set properties, collapse, and make-unique.

Mutating tools require explicit node names, stable object handles, or an
explicit `use_selection=true` argument. They return changed-node summaries so
agents can report what changed without relying on opaque macros.

## Boolean solids

`boolean_operation` drives the native ProBoolean or Boolean / Boolean2 compound
object. `create` registers `base_node` as the first operand followed by
`operands`, and sets the mode (`union`, `intersection`, `subtraction`, `cut`).
Because the operands stay live, `set_operand`, `extract_operand`,
`remove_operand`, and `add_operands` re-adjust an existing boolean without
rebuilding it, and `set_operation` switches the mode in place.

The two classes are reached through **class-specific adapters**, because they do
not agree on anything:

| | ProBoolean | Boolean / Boolean2 |
| --- | --- | --- |
| Reached through | the `ProBoolean` interface struct | methods on the object |
| Operand add | `SetOperandB` | `setOperandB` |
| Mode set / get | `SetBoolOp` / `GetBoolOp` | `setBoolOp` / `getBoolOp` |
| union / intersection / subtraction | 0 / 1 / 2 | 1 / 2 / 3 |
| cut | **unsupported** (3 is Merge there) | 5 |

Sharing one code path would silently produce the wrong solid, so `cut` is
rejected when only ProBoolean is available rather than being mapped onto Merge.

Both the mode and the registered operand count are read back. A mode the host
coerced - or that it accepts but will not report - an operand that did not
register, and an operand count the host does not expose all fail the call.
Every operand mutation is verified against the count read **before** the call:
an add must grow it by one, a removal must shrink it by one, and an extraction
must leave it unchanged. A failed `create` removes the node it made and reports
whether that removal was actually confirmed, so a rollback is never claimed
unless the deletion is verified.

## No silent success

Write paths never report success for a value the host did not accept. Every
property write is read back and compared, every add/remove is verified against
the stack length, and a host that exposes no usable entry point returns an
error. When a host runs an operation that cannot be confirmed programmatically
(`make_modifier_unique`), the result carries an explicit `warning` instead of a
bare success.
