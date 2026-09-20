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
    search-hint: "3ds Max new open save save-as merge scene status dirty nodes cameras selection visibility parenting transforms properties rename create clone instance reference orientation freeze external max file inspect merge search batch"
    tags: "3dsmax, scene, lifecycle, open, save, merge, external max file, inspect, search, nodes, cameras, selection, visibility, transforms, properties, rename, clone, orientation"
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
    produces: ["scene_status", "scene_file", "scene_info", "node_list", "selection_state", "bounding_box", "visibility_state", "object_properties", "orientation_report", "max_file_info", "max_file_matches"]
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

Use `get_object_properties` and `set_object_property` for generic property
access instead of falling back to `execute_python` or `execute_maxscript`.
`set_object_property` writes one property and confirms it by readback, so a
value the host rejected is reported as a failure rather than a success. Use
`batch_rename_objects` for bulk naming, `create_object` for classes beyond
the four modeling primitives, `transform_object` for rotate and scale in
world or local space, and `clone_objects` when a copy, instance, or reference
relationship matters. `set_visibility` also covers freeze and unfreeze.
`analyze_node_orientation` reports pivot, local axis drift, and the world
matrix for orientation checks.

`create_object` calls a runtime symbol, so it always requires that symbol to
be provable as a creatable 3ds Max class: `superClassOf` succeeds for classes
and fails for bare global functions. This requirement is unconditional and
independent of `DCC_MCP_3DSMAX_DISABLE_ARBITRARY_SCRIPT` — that variable
neither tightens nor loosens the check, and is named in the rejection message
only so operators can locate the refusal. A denylist of destructive global
functions remains as defense in depth. When the runtime exposes no
creatable-class predicate, no symbol can be proven, so the tool refuses every
call rather than leaving an ungated symbol-call path. A symbol that is proven
to be a creatable class stays available regardless of the variable, because
the variable gates arbitrary script execution, not typed class construction.

External `.max` files are inspected without opening them. `inspect_max_file`
reports the objects and metadata of one file, `batch_file_info` does the same
for a batch of up to 20 files, and `search_max_files` finds objects by name
pattern across a batch of up to 20 files. Both batch tools read one path at a
time on the 3ds Max main thread, so the per-call limit is deliberately small
and a batch above 10 paths answers with a warning that asks for a smaller
batch; split a larger inventory into several calls instead of issuing one long
request. All three fail closed: a file that is missing, unreadable, or not a
scene file is reported as an explicit per-file error with a stable reason,
never as an empty object list. Host functions used to read external files are
probed rather than assumed, so a host that lacks a reader reports
`external_scene_reader_unavailable` instead of returning no objects.

`merge_from_file` merges objects selected by exact name or by name pattern
from an external `.max` file. The selection is resolved against the source
file before anything is merged, so a name or pattern that matches nothing
fails the call, and names that could not be resolved are always returned in
`unresolved_object_names`; pass `require_all=true` to make them fail the call.

Both merge tools probe `getLastMergedNodes` **before** they merge. A host
without that readback is rejected with `merge_readback_unavailable` and no
scene change at all, because "merged but unconfirmed" is the one state where a
retry would duplicate objects. When the host does accept the merge but the
readback does not confirm it, the response reports `scene_modified` and tells
the caller to undo once before retrying.
It shares the verified readback and the fixed no-prompt conflict policies of
`merge_file`, so use `merge_file` when the exact source node names are already
known and `merge_from_file` when the selection has to be resolved by pattern.
One call merges N nodes: undo once and re-check the node list, then repeat
while merged nodes are still present, because 3ds Max may or may not group
them into a single entry.

Node-targeted tools accept explicit node names or stable object handles and
return structured not-found or ambiguous-match errors instead of guessing.
