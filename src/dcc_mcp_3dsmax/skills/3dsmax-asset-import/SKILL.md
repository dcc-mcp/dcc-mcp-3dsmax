---
name: 3dsmax-asset-import
description: >-
  Domain skill - import 3D assets (FBX, OBJ, 3DS) into the current 3ds Max
  scene via the AssetDescriptor contract from dcc-mcp-core. Use when the user
  wants to bring external geometry files into the scene with structured asset
  tracking.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max asset import fbx obj 3ds geometry scene"
    tags: "3dsmax, asset, import, fbx, obj, geometry"
    tools: tools.yaml
    intent: "Import 3D assets (FBX, OBJ, 3DS) into the current 3ds Max scene."
    search_aliases: ["asset import", "import"]
    recall_context:
      app_type: "3dsmax"
      domain: "asset_import"
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
      exports: false
      imports: true
      file_output: false
      render: false
      targets: ["scene_node", "file"]
    produces: ["scene_node", "asset_import"]
---

# 3ds Max Asset Import Skill

Import 3D assets into the current scene using dcc-mcp-core's `AssetDescriptor`
contract. Supported formats: FBX, OBJ, and 3DS.

Each import operation snapshots the node set before and after the import,
reports what was created, and renames imported nodes with a consistent prefix.

The `import_to_scene` tool accepts either a dict payload (matching the
AssetDescriptor schema) or a descriptor object and returns the created node
identities with a combined bounding box.
