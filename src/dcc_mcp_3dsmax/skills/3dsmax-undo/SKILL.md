---
name: 3dsmax-undo
description: >-
  Domain skill - reverse 3ds Max operations through the host undo and redo
  stack, and report which undo entry points the running host exposes.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max undo redo revert history step reverse last operation destructive tool rollback verify scene fingerprint"
    tags: "3dsmax, undo, redo, history, rollback, revert, safety"
    tools: tools.yaml
    intent: "Reverse 3ds Max operations through the host undo and redo stack."
    search_aliases: ["undo", "undo-ops", "history"]
    recall_context:
      app_type: "3dsmax"
      domain: "history"
      workflow_stage: "authoring"
      task_category: "mutate"
    preconditions:
      - type: software
        name: "3ds Max"
        version: ">=2024"
    side_effects:
      creates: false
      modifies: true
      deletes: true
      exports: false
      imports: false
      file_output: false
      render: false
      targets: ["scene_node", "history"]
    produces: ["undo_state"]
---

# 3ds Max Undo Skill

Reverse 3ds Max operations after the fact. This skill is the safety net for the
adapter's destructive tools: it exposes the host undo and redo stack as typed
MCP tools instead of leaving agents to reach for `execute_maxscript`.

## No silent success

3ds Max exposes no API for the depth of the history stack, so "the host accepted
the command" is not the same as "something was undone". Every step is therefore
verified:

1. A **scene fingerprint** (node identities, transforms, modifier counts,
   material bindings, current time, selection) is taken before the step.
2. One undo/redo command is executed through a single, capability-detected
   channel (`max undo` / `max redo`, falling back to the SDK hold manager).
3. The fingerprint is taken again. If it is unchanged, the step counts as a
   no-op and the loop stops.

A request where **zero** steps changed the scene fails, because the usual cause
is an empty history stack. Callers that expect some operations to leave no
observable trace can pass `allow_no_op: true` to receive an explicit warning
instead. A request where only *some* steps changed the scene succeeds with an
explicit warning and reports `applied` versus `requested`.

## Undo metadata on destructive tools

Every tool declared `destructive: true` in this adapter carries an `undo:`
block stating whether it is reversible and at what granularity. See
`docs/UNDO.md` for the vocabulary and the per-tool table.

- `single_call` - one `undo_last` call reverses the whole tool call.
- `per_node` - the host records one entry per node, so pass `count` equal to
  the number of nodes the tool reported changing.
- `script_defined` - arbitrary script; the adapter cannot verify coverage.
- `none` - not reversible through the host stack (file I/O, scene reset).

## Future work

`undo_step()` in `dcc_mcp_3dsmax._undo_utils` is the adapter-side equivalent of
the MAXScript `undo "label" ( ... )` wrapper. It is not wired into any shipped
tool yet; an atomic batch tool (`scene_patch`) will use it so N edits collapse
into one undo entry, which this skill's one-step-per-entry semantics already
handle correctly.
