---
name: 3dsmax-modeling
description: >-
  Domain skill - create basic primitives, bounded native Lathe geometry,
  splines and curve models, and lofted surfaces on the 3ds Max main thread.
  Use when adding boxes, spheres, cylinders, planes, a rotational form from a
  typed profile, a spline drawn from world-space points, a named parametric
  curve model, or a loft from matching cross-sections. Not for mesh editing,
  boolean operations, import/export, or material assignment.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.2.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max create box sphere cylinder plane primitive lathe profile revolve spline curve knot handle sweep loft geometry modeling position"
    tags: "3dsmax, modeling, geometry, primitives, lathe, spline, curve, loft"
    tools: tools.yaml
    intent: "Create 3ds Max primitives, native Lathe profiles, splines, curve models, and lofts."
    search_aliases: ["modeling", "lathe", "revolve profile", "spline", "curve", "loft", "sweep"]
    recall_context:
      app_type: "3dsmax"
      domain: "modeling"
      workflow_stage: "authoring"
      task_category: "mutate"
    preconditions:
      - type: software
        name: "3ds Max"
        version: ">=2024"
    side_effects:
      creates: true
      modifies: true
      deletes: false
      exports: false
      imports: false
      file_output: false
      render: false
      targets: ["scene_node", "primitive", "scene_node:shape", "scene_node:loft", "scene_property"]
    produces: ["scene_node:primitive", "scene_node:box", "scene_node:sphere", "scene_node:cylinder", "scene_node:plane", "scene_node:lathe", "scene_node:shape", "scene_node:loft", "spline_knots", "curve_params", "loft_params"]
---

# 3ds Max Modeling Tools

Create primitive, Lathe, spline, curve-model, and loft geometry in the current
3ds Max scene. All tools touch the live scene through `pymxs`, so they declare
`affinity: main`.

`lathe_profile` accepts two to 256 `[radius, height]` pairs. Radius must be
non-negative; both values must be finite. It creates an XZ-plane spline and
uses the native Lathe axis. The action reads back the spline knot count and
modifier settings, and removes the new node if those postconditions differ.

## Splines and curves

`draw_spline` builds a spline shape from an ordered list of world-space XYZ
triples. It supports `create`, `append`, and `replace` modes over one spline
index. Points are converted into the shape's object space, committed with
`updateShape`, and read back in world space; a point that does not come back
fails the call and removes the node the call created.

`inspect_curve` reads every spline of a shape as world-space knots with their
in/out handle vectors, and returns a token that digests the geometry it read.
`edit_curve` requires that token: if the spline changed since it was issued,
the edit fails before anything is written. After an edit, every requested field
is read back, and a field that did not take effect restores the captured
knot state and fails the call.

`curve_model` adds a named, parametric layer on top: a `rounded_rect`,
`rectangle`, `circle`, or `polyline` profile, optionally swept along a path
spline with the native Sweep modifier. The parameters that produced the
profile are stored on the node, so `read` and `update` work without repeating
them, and `list` enumerates every stored curve model in the scene.

`loft_mesh` lofts two to sixty-four matching cross-section splines,
opitionally along a path spline, into a quad loft. The registered shape count
is read back and must match; surface parameters are applied one by one and
read back, so a parameter the host ignored is returned in
`rejected_surface_params` rather than being reported as applied.

## No silent success

Every write path in this skill verifies itself. A value the host refuses, or
whose readback differs from the request, is reported as a failure with the
expected and actual values - never as a warning on a successful result.
Operations that cannot be confirmed (for example a shape count the host does
not expose) fail rather than report a default.

Tool contracts live in `tools.yaml`. Scripts keep host API access behind
adapter helpers so metadata discovery remains safe outside 3ds Max.
