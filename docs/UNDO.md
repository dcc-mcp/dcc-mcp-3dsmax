# Undo semantics for 3ds Max tools

The adapter exposes destructive tools, so every one of them has to answer two
questions up front: *can this be reversed?* and *how many undo entries does one
call create?* This document is the contract. It is enforced by
`tests/test_undo_skill.py`, which fails when a `destructive: true` tool declares
no `undo` block or uses a granularity outside the vocabulary below.

## The tools

| Tool | Purpose |
| --- | --- |
| `3dsmax-undo__undo_last` | Reverse `count` host undo entries, verifying each step. |
| `3dsmax-undo__redo_last` | Replay `count` undone entries, verifying each step. |
| `3dsmax-undo__get_undo_status` | Report the undo/redo entry points this host exposes plus the current scene fingerprint. |

All three run with `affinity: main` and `enforce_thread_affinity: true`, like
every other tool that touches the scene.

## No silent success

3ds Max exposes no API for the depth of the history stack, so "the host accepted
the command" is not proof that anything was undone. Each step is therefore
verified:

1. A **scene fingerprint** is taken before the step. It covers node identities,
   transforms, modifier counts, material bindings, the current time, and the
   selection.
2. One command is executed through a single, capability-detected channel.
3. The fingerprint is taken again. An unchanged fingerprint means the step was a
   no-op and the loop stops.

The result is explicit in every case:

| Outcome | `success` | Reporting |
| --- | --- | --- |
| Every requested step changed the scene | `true` | `applied == requested`, `completed: true` |
| Some steps changed the scene | `true` | `applied < requested`, `completed: false`, plus a warning |
| No step changed the scene | `false` | `applied: 0`; pass `allow_no_op: true` to get a warning instead |
| The host raised on the command | `false` | The host error text is returned |

Channels are probed, not assumed. `max undo` / `max redo` are the documented
user-level commands and take priority; the SDK hold manager
(`theHold.Restore()` / `theHold.Redo()`) is the fallback. Only one channel fires
per call, so a single request can never trigger two mechanisms at the same
history position. When neither exists the call fails with
`this 3ds Max host exposes no undo entry point`.

## Granularity vocabulary

Every tool declaration carries an `undo` block:

```yaml
undo:
  supported: true
  granularity: per_node
  notes: >-
    One host undo entry per node in the response; pass that count to `undo_last`.
```

| Value | Meaning |
| --- | --- |
| `single_call` | One host undo entry per tool call. One `undo_last` reverses it. |
| `per_node` | One host undo entry per node touched. Call `undo_last` with `count` equal to the number of nodes the tool reported. |
| `script_defined` | Arbitrary script. Undo coverage depends on the script body and cannot be verified by the adapter. |
| `none` | Not reversible through the host stack. `supported` must be `false`. |

`supported: false` always goes with `granularity: none`.

## Destructive tools

| Tool | Reversible | Granularity | Notes |
| --- | --- | --- | --- |
| `3dsmax-animation__delete_keyframes` | yes | `per_node` | One entry per node in `changes`. |
| `3dsmax-display__delete_layer` | yes | `single_call` | Includes member nodes removed with `delete_nodes=true`. |
| `3dsmax-display__delete_custom_property` | yes | `per_node` | Pass `changed_property_count` as `count`. |
| `3dsmax-mesh-ops__remove_modifier` | yes | `per_node` | One entry per node in the response. |
| `3dsmax-mesh-ops__collapse_modifier_stack` | yes | `per_node` | Recoverable only through the host stack, and only until the session ends. |
| `3dsmax-rigging__remove_deformer_modifier` | yes | `per_node` | One entry per node in the response. |
| `3dsmax-scene__new_scene` | no | `none` | File > New clears the history stack. |
| `3dsmax-scene__open_scene` | no | `none` | File > Open clears the history stack. |
| `3dsmax-scene__save_scene` | no | `none` | File writes are outside the host undo stack. |
| `3dsmax-scene__save_scene_as` | no | `none` | File writes are outside the host undo stack. |
| `3dsmax-scene__delete_nodes` | yes | `single_call` | One host call; the per-node fallback leaves one entry per node. |
| `3dsmax-scripting__execute_python` | depends | `script_defined` | Prefer typed tools. |
| `3dsmax-scripting__execute_maxscript` | depends | `script_defined` | Prefer typed tools. |
| `3dsmax-uv-atlas__delete_uv_channel` | yes | `per_node` | One entry per node in the response. |

## Single-step grouping for future atomic batches

A future atomic batch tool (`scene_patch`) needs N edits to collapse into one
undo entry — the MAXScript equivalent of `undo "label" ( ... )`.
`dcc_mcp_3dsmax._undo_utils.undo_step()` provides that on the Python side: it
opens a hold with `theHold.Begin()`, and closes it with `theHold.Accept(label)`
on success or `theHold.Cancel()` on an exception.

It is **not** wired into any shipped tool yet. That is deliberate: every
existing tool keeps its own host undo entries, which is the conservative
behaviour, and the semantics above stay correct either way. When the batch tool
lands, one of its calls will produce one host entry and therefore one
`undo_last` step — the same contract as a `single_call` tool, so nothing here
has to change.

`undo_step()` reports instead of guessing: the yielded dict carries `engaged`
and `reason`, and a caller must surface a non-engaged hold rather than assume
the batch was grouped.
