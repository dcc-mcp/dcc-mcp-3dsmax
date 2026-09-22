---
name: 3dsmax-render
description: >-
  Domain skill - capture viewports, render scenes to image files, create
  preview playblasts, configure renderers (Arnold, V-Ray, Scanline), produce
  HDR/EXR output and multi-pass AOV renders, arm render completion signals,
  control the V-Ray interactive production rendering (IPR) preview, inspect
  render settings and statistics, and adjust common render output options in
  3ds Max.
license: MIT
compatibility: "dcc-mcp-core 0.17+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max render viewport capture scene image playblast preview HDR EXR multi-pass AOV render elements arnold vray scanline settings resolution frame range camera quality completion signal callback automation IPR interactive production rendering"
    tags: "3dsmax, render, viewport, capture, playblast, camera, image, hdr, exr, aov, multi-pass, renderer, automation, callback, ipr"
    tools: tools.yaml
    intent: "Capture viewports, render scenes, create preview playblasts, configure renderers, arm render completion signals, control the V-Ray IPR preview, and manage 3ds Max render settings."
    search_aliases: ["rendering", "render", "render automation", "vray ipr"]
    recall_context:
      app_type: "3dsmax"
      domain: "rendering"
      workflow_stage: "authoring"
      task_category: "mutate"
    preconditions:
      - type: software
        name: "3ds Max"
        version: ">=2024"
    side_effects:
      creates: false
      modifies: true
      deletes: false
      exports: true
      imports: false
      file_output: true
      render: true
      targets: ["file:image", "file:video", "render_settings", "hdr", "aov", "render_callbacks", "vray_ipr"]
    produces: ["file:image", "file:preview", "render_settings", "file:hdr_image", "file:multi_pass_image", "renderer_config", "render_signal", "ipr_state"]
---

# 3ds Max Render and Viewport Skill

Capture viewport evidence, render scenes to image files, generate previews,
inspect render settings, and change common render output options through
`pymxs`.

Output-producing tools validate extensions, parent directories, and overwrite
behavior before invoking host operations, then return artifact metadata.
Render settings and completed render results declare the native frame-buffer
`view_transform`; hosts without the 3ds Max 2024+ display/view API return an
explicit unsupported reason instead of assuming an sRGB transform.

## Verified writes

Every render setting write is read back and classified per setting. A response
carries `applied`, `unverified`, `errors`, `warnings`, and the full
`setting_results` rows:

- `applied` - the host kept the requested value.
- `unverified` - the host took the write but the value cannot be read back, so
  the tool reports a warning instead of claiming success detail it cannot prove.
- `errors` - the host refused the write or kept a different value; the call
  fails instead of reporting a change that did not happen.

`set_render_output_options`, `set_frame_range`, `set_render_resolution`,
`set_render_camera`, and `set_render_quality_preset` fail on any `errors` row.
`render_scene`, `render_hdr`, and `render_multi_pass` stop before rendering when
a setting they depend on could not be written, and `render_hdr` also verifies
the requested `bit_depth` / `compression` so an HDR render can never report a
bit depth the host ignored. Quality-preset sub-knobs such as `sampling` are
derived from the preset rather than requested by the caller: they are reported
under `unverified` when a host rejects them, and never fail the preset itself.

## Render automations and V-Ray IPR

`render_automations` arms the host to report when the *next* render finishes:
it registers a post-render callback whose MAXScript body writes a JSON signal
file. The default is `wait=false`: the call arms the signal and returns the
file for the caller to poll. `wait=true` blocks the calling thread, and on a
host that runs skill scripts on the 3ds Max main thread the `#postRender`
callback cannot be processed during the wait, so polling is the safe default.

Values written into the signal file are JSON-encoded first and MAXScript-escaped
second, so Windows output paths survive both un-escaping steps and the record
parses as JSON.

- an unsupported action is a failure, never an ignored keyword
- a host that refuses the callback is a failure, never an armed-looking success
- a host with no callback read-back contract is reported as `unverified`
- a wait that expires returns `status="timeout"` with the signal still armed,
  so an agent can never mistake a timeout for a finished render

`vray_ipr` starts, stops, refreshes, and queries the V-Ray interactive
production rendering preview. Every action is checked against the host preview
state, and a host that reports no state is reported as unverified.
