# Undo semantics for 3ds Max tools

Tools that write to the scene have to answer two questions up front: *can this
be reversed?* and *how many undo entries does one call create?* This document is
the contract, and it is enforced by `tests/test_undo_skill.py`.

## Who has to declare an `undo` block

The requirement is the same in the docs, in the tests and in the tool tables:

| Kind of tool | `undo` block | Enforced by |
| --- | --- | --- |
| `destructive: true` | **required** | `test_every_destructive_tool_declares_undo_semantics` |
| Multi-node write path - writes `scene_nodes` and takes a plural node selection (array `node_names` / `handles`) | **required** | `test_multi_node_write_paths_declare_undo_semantics` |
| Any other write | optional, but must use the vocabulary when declared | `test_every_declared_undo_block_uses_the_vocabulary` |

The multi-node rule exists because an agent cannot infer the undo count from the
tool list for those calls: one invocation can change N nodes and the host may or
may not group them. A single-node write such as `parent_node` and a
settings-level write such as `set_render_resolution` are easy to reason about
without metadata, so they are not required to declare one.

The multi-node rule is enforced for the skills listed in `UNDO_SCOPE_SKILLS` in
`tests/test_undo_skill.py` - currently `3dsmax-scene`, the primary entry point
for scene writes. Extend that tuple as further skills are brought onto the
contract; destructive tools in every skill are already covered regardless.

Every tool that declares `undo` metadata must also appear in the tables in this
file (`test_undo_metadata_matches_known_granularities`).

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

The fingerprint samples at most 4000 nodes. On a larger scene the result
therefore carries a `the scene fingerprint sampled only the first N nodes`
warning, and that warning is attached to **every** outcome - including the
empty-stack failure and the `allow_no_op` success - because a real undo that
only moved nodes outside the sample is exactly the case that would otherwise be
reported as a bare no-op.

When that warning is present, an `applied: 0` result means **unverified**, not
"nothing happened", so do not stop on it. Re-read what the call changed and keep
undoing while it still differs; stop when the re-read matches the state you
want, or when a result arrives **without** the sampling warning, which means the
verification was complete and the history stack really is exhausted.

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
| `batch_call` | One call writes N nodes. The host usually groups the batch into a single undo entry, but the grouping cannot be queried: undo once, re-read the nodes, then repeat while the scene still differs. |
| `script_defined` | Arbitrary script. Undo coverage depends on the script body and cannot be verified by the adapter. |
| `none` | Not reversible through the host stack. `supported` must be `false`; do not rely on `undo_last` to reverse the call. |

`supported: false` always goes with `granularity: none`, and every other
granularity goes with `supported: true`.

## Write paths that are not destructive

`destructive: true` means *this call can destroy data the caller cannot
recreate*. It says nothing about undo coverage, so a non-destructive write can
still be reversible through the host undo stack. The multi-node write paths in
`3dsmax-scene` therefore carry an `undo` block, using the same vocabulary, and
their `destructive` / `risk` classification is unchanged.

The `3dsmax-modeling` tools below are not destructive, but each one performs
several host writes - one `addKnot` per spline point, one write per edited knot
field, one `addShape` per loft cross-section - with no undo hold opened. The
host may group them into one entry or keep them separate, and the adapter
cannot query which, so they are declared `batch_call`.

| Tool | Reversible | Granularity | Notes |
| --- | --- | --- | --- |
| `3dsmax-scene__set_object_property` | yes | `single_call` | One property write on one node. |
| `3dsmax-scene__create_object` | yes | `single_call` | Creates one node. |
| `3dsmax-scene__set_selection` | no | `none` | Selection is editor state. Re-select explicitly. |
| `3dsmax-scene__merge_file` | yes | `batch_call` | One call merges N nodes from a file. |
| `3dsmax-scene__merge_from_file` | yes | `batch_call` | One call merges N nodes from an external .max file. |
| `3dsmax-scene__duplicate_nodes` | yes | `batch_call` | One call duplicates N nodes. |
| `3dsmax-scene__group_nodes` | yes | `batch_call` | One call groups N nodes. |
| `3dsmax-scene__batch_rename_objects` | yes | `batch_call` | One call renames N nodes. |
| `3dsmax-scene__transform_object` | yes | `batch_call` | One call transforms N nodes. |
| `3dsmax-scene__clone_objects` | yes | `batch_call` | One call clones N nodes. |
| `3dsmax-scene__set_visibility` | yes | `batch_call` | One call sets visibility on N nodes. |
| `3dsmax-scene__center_pivots` | yes | `batch_call` | One call centres pivots on N nodes. |
| `3dsmax-scene__freeze_transforms` | yes | `batch_call` | One call freezes N nodes. |
| `3dsmax-modeling__draw_spline` | yes | `batch_call` | One addKnot per point; grouping is not queryable. |
| `3dsmax-modeling__edit_curve` | yes | `batch_call` | One write per edited knot field; grouping is not queryable. |
| `3dsmax-modeling__curve_model` | yes | `batch_call` | Spline construction, optional sweep, and stored parameters. |
| `3dsmax-modeling__loft_mesh` | yes | `batch_call` | One addShape per cross-section, plus the surface parameters. |

### Undo counts for a batch write

3ds Max exposes no API for the depth of the history stack and no API for how it
grouped a batch of writes onto it. A call that touched N nodes may therefore be
one host entry *or* N entries, and the adapter cannot tell which. The procedure
is the same either way:

1. Call `undo_last` with `count=1`.
2. Re-read what the tool reported - the `renamed` list for
   `batch_rename_objects`, or the affected nodes for `transform_object` and
   `clone_objects`.
3. If the batch is only partly reverted, call `undo_last` again. Stop when a
   step reports `applied: 0`, which means the history stack is exhausted.

Do not jump straight to `undo_last(count=N)`: when the host *did* group the
batch, those extra steps consume undo entries that belong to earlier work.

## Destructive tools

| Tool | Reversible | Granularity | Notes |
| --- | --- | --- | --- |
| `3dsmax-animation__delete_keyframes` | yes | `per_node` | One entry per node in `changes`. |
| `3dsmax-display__delete_layer` | yes | `single_call` | Includes member nodes removed with `delete_nodes=true`. |
| `3dsmax-display__delete_custom_property` | yes | `per_node` | Pass `changed_property_count` as `count`. |
| `3dsmax-mesh-ops__remove_modifier` | yes | `per_node` | One entry per node in the response. |
| `3dsmax-mesh-ops__collapse_modifier_stack` | yes | `per_node` | Recoverable only through the host stack, and only until the session ends. |
| `3dsmax-mesh-ops__boolean_operation` | yes | `batch_call` | Node creation, mode write, and one registration per operand. `remove_operand` drops an operand. |
| `3dsmax-rigging__remove_deformer_modifier` | yes | `per_node` | One entry per node in the response. |
| `3dsmax-scene__new_scene` | no | `none` | File > New clears the history stack. |
| `3dsmax-scene__open_scene` | no | `none` | File > Open clears the history stack. |
| `3dsmax-scene__save_scene` | no | `none` | File writes are outside the host undo stack. |
| `3dsmax-scene__save_scene_as` | no | `none` | File writes are outside the host undo stack. |
| `3dsmax-scene__delete_nodes` | yes | `single_call` | One host call; the per-node fallback leaves one entry per node. |
| `3dsmax-scene__scene_patch` | yes | `single_call` | Every edit in the batch is written inside one host undo hold, so one `undo_last` reverses the whole call. `undo.grouped` is `false` only when the host could not open a hold. |
| `3dsmax-scripting__execute_python` | depends | `script_defined` | Prefer typed tools. |
| `3dsmax-scripting__execute_maxscript` | depends | `script_defined` | Prefer typed tools. |
| `3dsmax-uv-atlas__delete_uv_channel` | yes | `per_node` | One entry per node in the response. |

## Single-step grouping for atomic batches

`3dsmax-scene__scene_patch` needs N edits to collapse into one undo entry —
the MAXScript equivalent of `undo "label" ( ... )`.
`dcc_mcp_3dsmax._undo_utils.undo_step()` provides that on the Python side: it
opens a hold with `theHold.Begin()`, and closes it with `theHold.Accept(label)`
on success or `theHold.Cancel()` on an exception. Because the whole batch runs
inside that hold, one call leaves one host entry and therefore one `undo_last`
step — the same contract as a `single_call` tool.

Grouping is a capability, not a given. `undo_step()` reports instead of
guessing: the yielded dict carries `engaged` and `reason`, and the caller must
surface a non-engaged hold rather than assume the batch was grouped.
`scene_patch` refuses to apply an ungrouped batch unless `allow_ungrouped` is
true, so an agent never ends up with a silently half-reversible batch.

Every other tool still keeps its own host undo entries, which is the
conservative behaviour: the semantics above are unchanged for them, and a
`scene_patch` failure that cancels the hold only ever rolls back its own edits.
