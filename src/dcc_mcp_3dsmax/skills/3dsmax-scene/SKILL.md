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
    search-hint: "3ds Max new open save save-as merge scene status dirty nodes cameras selection visibility parenting transforms properties rename create clone instance reference orientation freeze external max file inspect merge search batch scene patch atomic preflight undo hierarchy subtree children instances instance sets dependencies refs dependents delta query by class by property snapshot"
    tags: "3dsmax, scene, lifecycle, open, save, merge, external max file, inspect, search, nodes, cameras, selection, visibility, transforms, properties, rename, clone, orientation, hierarchy, instances, dependencies, query"
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
    produces: ["scene_status", "scene_file", "scene_info", "node_list", "selection_state", "bounding_box", "visibility_state", "object_properties", "orientation_report", "max_file_info", "max_file_matches", "node_tree", "instance_groups", "dependency_graph", "scene_summary", "scene_delta"]
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

`scene_patch` is the batch write path: it applies up to 256 mechanical node
edits (set a property, rename, set position, set visibility) in one call.
Every edit is resolved and validated before the first write, so a rejected
batch leaves the scene untouched, and the accepted edits are written inside a
single host undo hold — one call leaves one undo entry, and an edit that fails
to verify cancels the hold so the whole batch is rolled back. Use `dry_run` to
check a batch first. Hosts that cannot open an undo hold refuse the batch
unless `allow_ungrouped=true`, in which case the result reports
`undo.grouped=false` and each edit may leave its own undo entry.

Node-targeted tools accept explicit node names or stable object handles and
return structured not-found or ambiguous-match errors instead of guessing.

`list_scene_nodes` answers with a flat list that carries a parent name, which
is enough to ask who a node's parent is but not what sits under it.
`get_hierarchy` answers the second question: it walks the parent links and
returns a nested tree. Omit the target to walk every parentless node. The tree
is never quietly short: the node limit, the depth cap, a parent that is not in
the scene node list, and a cycle in the parent links are each counted and named
in `warnings` and in `truncated`, `parent_outside_scene`, and `cycle_members`.
A cycle has no parentless member, so one representative per cycle is seeded as
a root to break it rather than letting the cycle vanish from the tree.

`get_instances` reports nodes that derive from one shared object, so editing
one edits all of them - the difference between moving one prop and moving
forty. `InstanceMgr.GetInstances` is used when the host exposes it; otherwise
nodes are grouped by the shared base-object handle, and the result says which
method was used. A host that can answer neither way is **refused**, because
"no instances" and "I cannot tell" are different answers and only one of them
is safe to act on. Nodes that could not be classified are returned in
`unresolved` with a reason, never dropped.

`get_dependencies` reads the dependency graph through the MAXScript `refs`
interface: `direct_dependents` from `refs.dependents`, `dependent_nodes` from
`refs.dependentnodes` followed recursively, and `depends_on` from
`refs.dependsOn`. A shared material, an instanced base object, and a parenting
link are all dependency edges and none of them are visible in a node list. A
host without a working `refs` interface is refused for the same reason
`get_instances` is; a host where only some `refs` calls work returns the
sections that worked and names the rest in `warnings`.

`query_scene` is the single entry point for the six read-side scene questions,
selected by `mode`. It exists so an agent does not have to guess which of six
separate actions answers its question and then pay for the wrong guess:

| mode | answers |
|---|---|
| `overview` | node count, visible and hidden counts, selection count, and a per-class histogram - counts only, no node list |
| `filter` | the node list, narrowed by a case-insensitive name substring |
| `class` | the node list, narrowed by class name, `exact` by default or `contains` |
| `property` | one property read per node, narrowed by value when `property_value` is given |
| `selection` | the nodes currently selected |
| `delta` | what was added, removed, renamed, or changed against a snapshot the caller captured earlier |

Every mode takes the same `name_filter`, `include_hidden`, and `limit`
arguments. `overview` and `delta` answer with counts instead of a node list, so
they stay cheap on a large scene.

`delta` is stateless: it requires a `baseline` snapshot and refuses to guess
one. Run `query_scene` with `include_snapshot` true and pass the returned
`snapshot` back as `baseline`. Nodes are matched by `object_id` when the
baseline carries one and by name otherwise, which is what makes a rename
visible instead of looking like a removal plus an addition. Pass `property_name`
to compare one property as well; a baseline that carries no value for it is
reported in `warnings` instead of being read as "nothing changed".

In `property` mode a node the host refused to read is listed in `skipped` with
the reason, not treated as a node that lacks the property. Private property
names (any name starting with an underscore) are refused outright. Every mode
returns a `warnings` list, and a question this host could not answer always ends
up in it rather than disappearing from the result.
