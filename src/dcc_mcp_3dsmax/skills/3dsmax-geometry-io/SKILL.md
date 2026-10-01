---
name: 3dsmax-geometry-io
description: >-
  Domain skill - validate geometry files and run host-native geometry import
  and export operations in Autodesk 3ds Max. Use for FBX/OBJ/3DS import checks,
  FBX import, FBX export, OBJ export, and structured import/export stats.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max geometry import export FBX OBJ 3DS file validation selected scene"
    tags: "3dsmax, geometry, io, import, export, fbx, obj"
    tools: tools.yaml
    intent: "Import and export geometry files (FBX, OBJ, 3DS) in 3ds Max with validation."
    search_aliases: ["io", "geometry-io"]
    recall_context:
      app_type: "3dsmax"
      domain: "io"
      workflow_stage: "authoring"
      task_category: "mutate"
    preconditions:
      - type: software
        name: "3ds Max"
        version: ">=2024"
    side_effects:
      creates: true
      modifies: false
      deletes: false
      exports: true
      imports: true
      file_output: true
      render: false
      targets: ["file:fbx", "file:obj", "file:3ds", "scene_node"]
    produces: ["file:fbx", "file:obj", "scene_node"]
---

# 3ds Max Geometry I/O Skill

Validate geometry paths and run host-native import/export operations through
`pymxs`. Tool contracts live in `tools.yaml`; import/export tools declare
`affinity: main` because they call the 3ds Max file I/O APIs.

Use the validation tool before import to check supported formats and file
existence. Use the FBX and OBJ tools for explicit export behavior, including
selected-only versus whole-scene export and overwrite handling. Import tools
return created node identities, warnings, and recoverable failure details.

On import failure, inspect the returned created nodes and current scene before
retrying. Native import can also modify existing nodes; the returned list is
not a rollback log. Export success requires a nonempty output file, but does
not prove format validity or freshness when overwriting an existing file.

## FBX import timeline

The 3ds Max FBX importer keeps the scene's own time settings instead of taking
the ones stored in the file, so a 24 fps clip imported into a 30 fps scene is
resampled and frame N no longer matches frame N in the source. FBX imports
therefore read the file's `GlobalSettings` first and align the scene frame rate
and animation range before importing, so frame numbers stay 1:1 with the
file; the result carries `data.timeline` with the source settings, the scene
timeline before and after, and whether the two match.

`timeline_mode` controls the alignment: `source` (default) adopts the file's
settings, `union` widens the animation range instead of shrinking it, and
`off` leaves the scene alone and only reports the mismatch. Alignment is
skipped when animation is not imported, when the file declares no animation, or
when the frame rate cannot be read; in each case a warning says the timeline
was left at its current rate instead of resampling silently.

The header is read with a small standard-library parser rather than the FBX
SDK, so malformed offsets are rejected instead of followed: this runs on the
main thread before `importFile`, where a bad offset must not stall the host.
ASCII FBX is only scanned within the first 256 KB, which covers every
conventional file because `GlobalSettings` is written near the top; an unusual
file that places it later reports that the time settings could not be read.
