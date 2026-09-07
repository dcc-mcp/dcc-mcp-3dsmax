# Agent capability audit

Baseline: remote `main` at `f01f956`, inspected 2026-09-07. This is a
domain-level engineering backlog, not an assertion of exhaustive Autodesk API
coverage. A declared tool, a passing fake-runtime test, and a real host result
are separate evidence levels. No real 3ds Max acceptance was performed in this
audit: inventory contained no registered 3ds Max instance.

| Domain | Implemented surface | Missing or unverified | Next acceptance |
| --- | --- | --- | --- |
| Scene and objects | Scene lifecycle, identities, hierarchy, selection, metadata, visibility, transforms | General object parameter discovery and typed edit/readback workflow; duplicate-name and stale-handle host probes | Create, resolve by handle, edit, read back, delete only owned nodes |
| Runtime discovery | Runtime symbols, macros, symbol inspection, node resolution | Runtime symbols do not prove callable plugin capabilities or safe parameter schemas | Discover actual installed classes and unavailable plugin reasons |
| Modeling | Primitives, lathe, topology, cleanup, modifiers, smoothing, normals, UV tools | Broader editable-poly operations, topology-changing selection recovery; issue #153 remains open | Representative hard-surface asset with topology and normal checks |
| Materials and lookdev | Standard/Physical/PBR material tools, textures, color management, HDR lighting | Renderer/version-specific slots and color transforms; turntable/framing orchestration | Native slot readback, missing-map check and rendered swatch |
| Animation and rigging | Batch keys, curve IO, constraints, skin weights, pose IO | Host controller round trips, unsupported character-system variants; #155 remains open despite existing code | Curve/pose/skin round trip on actual supported controllers |
| Geometry IO | FBX/OBJ/3DS imports, FBX/OBJ exports, created-node identities | Alembic/USD typed geometry imports; per-plugin discovery; settings restoration; overwrite freshness and format validity | Nonempty exports plus reimport and native content comparison |
| Asset import | Core AssetDescriptor and ImportToSceneResult route, asset-source and legacy asset-import families | Multiple import entry points need coherent capability reporting; USD procedural is not editable USD geometry import | Same descriptor, explicit variant selection and structured warnings |
| Long tasks | Core-owned queue, persistence/recovery configuration and cancellation checkpoint | Geometry/render tools declare synchronous execution; native importer/render preemption not demonstrated | Poll exact job ID, cancel queued work and cooperative work; document noninterruptible native calls |
| Failure recovery | Created-node readback on import success and false result; this batch adds exception readback and rejects absent/empty export files | Existing-node changes cannot be reconstructed from newly created nodes; no automatic rollback; partial failure must not be blindly replayed | Importer adds a node then fails; observe partial state without deleting it |
| Rendering and capture | Scene/HDR/multipass render, renderer configuration, viewport capture | Display transform, artifact durability, renderer licenses and native completion need host validation | Valid image decode, expected dimensions, output color comparison and terminal job state |
| Host/bootstrap | Install SOP, sidecar and shared Core dispatch contracts | Installed executable does not prove an available license or ready host | Cold start, inventory readiness, search/describe/call/readback |

## Official API references and limits

- [Autodesk scene file API](https://help.autodesk.com/cloudhelp/2024/ENU/MAXScript-Help/files/MAXScript-Tools-and-Interaction/File-Access/3ds-Max-Scene-Files-Access/GUID-624D3D05-B15D-4A97-9F15-DA35CDB0DDD2.html)
  exposes `importFile` with an optional importer class. File extension support
  must not be equated with a loaded importer or successful content transfer.
- [Autodesk Alembic importer](https://help.autodesk.com/cloudhelp/2023/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/Import-and-Export-Filters/GUID-504B022E-C059-49EF-A54D-A1EDC88D1B4A.html)
  documents version-dependent settings and axis control. Do not inherit FBX
  options for Alembic.
- [Autodesk USD import](https://help.autodesk.com/cloudhelp/2025/ENU/3dsMax-USD/files/USD-for-3ds-Max/Import-and-export-USD-data-in-Max/GUID-0524FF19-5C9C-4066-A016-0F71C4482E2B.html)
  describes a separate USD importer and extension SDK. Probe the installed USD
  plugin/version; do not substitute an Arnold procedural for editable import.

## Open issue snapshot

Open at baseline: #174 (capture durability), #171 (Python 3.7 payload), #156
(rendering), #155 (animation/rigging), #153 (modeling), #152 (install SOP), #151
(13 evaluation findings), #149 (sidecar feedback). Several have related code
and tests already; keep them open until each original reproduction is retested
against the relevant runtime. Issue status alone is not a capability verdict.

## SpeedTree acceptance matrix

Producer input manifest and owner handoff are still pending. Preserve source
hashes, exporter version, export settings, texture dependencies, units, up axis,
LOD naming and wind representation. No format below has passed host acceptance.

| Input | Current route | Required evidence / boundary |
| --- | --- | --- |
| FBX | Typed import_fbx | Created handles, mesh counts, bounds, material IDs, texture paths, units/axes; verify wind animation and LOD nodes explicitly |
| OBJ + MTL | Typed import_geometry | Geometry, UVs and material/texture dependencies; do not infer animated wind or semantic LOD from OBJ geometry |
| Alembic | Missing typed geometry route | Probe native importer; compare frame samples and axes; baked deformation is not editable SpeedTree wind controls |
| USD | Missing typed editable geometry route | Probe USD plugin/version and import API; verify prim conversion, materials and time samples; procedural rendering is a different result |

First batch strengthens result/error readback without adding a parallel Core
job implementation. Subsequent batches should address plugin/parameter
discovery, explicit Alembic/USD import contracts, then long-task acceptance.
