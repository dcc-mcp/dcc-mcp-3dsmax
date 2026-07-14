# Bundled 3ds Max Skills

This index lists the default skill families shipped with `dcc-mcp-3dsmax` and
the atomic tools each family exports. Tool names are shown as they are exposed
through the DCC-MCP skill loader.

| Skill family | Stage | Exported tools |
| --- | --- | --- |
| `3dsmax-scene` | scene | `3dsmax-scene__get_scene_info`, `3dsmax-scene__list_scene_nodes`, `3dsmax-scene__list_cameras`, `3dsmax-scene__get_selection`, `3dsmax-scene__get_bounding_box`, `3dsmax-scene__get_node_visibility`, `3dsmax-scene__get_scene_metadata`, `3dsmax-scene__set_selection`, `3dsmax-scene__duplicate_nodes`, `3dsmax-scene__delete_nodes`, `3dsmax-scene__group_nodes`, `3dsmax-scene__parent_node`, `3dsmax-scene__unparent_node`, `3dsmax-scene__set_visibility`, `3dsmax-scene__center_pivots`, `3dsmax-scene__freeze_transforms` |
| `3dsmax-geometry-io` | authoring | `3dsmax-geometry-io__validate_geometry_file`, `3dsmax-geometry-io__import_fbx`, `3dsmax-geometry-io__import_geometry`, `3dsmax-geometry-io__export_fbx`, `3dsmax-geometry-io__export_obj` |
| `3dsmax-import-to-scene` | authoring | `3dsmax-import-to-scene__import_to_scene` |
| `3dsmax-mesh-ops` | authoring | `3dsmax-mesh-ops__get_mesh_topology`, `3dsmax-mesh-ops__get_selected_mesh_topology`, `3dsmax-mesh-ops__get_smoothing_groups`, `3dsmax-mesh-ops__get_modifier_stack`, `3dsmax-mesh-ops__triangulate_meshes`, `3dsmax-mesh-ops__cleanup_meshes`, `3dsmax-mesh-ops__attach_meshes`, `3dsmax-mesh-ops__detach_selected_faces`, `3dsmax-mesh-ops__apply_subdivision`, `3dsmax-mesh-ops__create_proxy_meshes`, `3dsmax-mesh-ops__set_explicit_normals`, `3dsmax-mesh-ops__clear_explicit_normals`, `3dsmax-mesh-ops__assign_smoothing_group` |
| `3dsmax-uv-atlas` | authoring | `3dsmax-uv-atlas__list_uv_channels`, `3dsmax-uv-atlas__create_uv_channel`, `3dsmax-uv-atlas__delete_uv_channel`, `3dsmax-uv-atlas__copy_uv_channel`, `3dsmax-uv-atlas__get_uv_shell_summary`, `3dsmax-uv-atlas__apply_uv_projection`, `3dsmax-uv-atlas__unwrap_uvs`, `3dsmax-uv-atlas__pack_uvs`, `3dsmax-uv-atlas__detect_uv_overlaps`, `3dsmax-uv-atlas__normalize_uvs`, `3dsmax-uv-atlas__prepare_texture_atlas` |
| `3dsmax-render` | authoring | `3dsmax-render__capture_viewport`, `3dsmax-render__create_preview`, `3dsmax-render__render_scene`, `3dsmax-render__get_render_settings`, `3dsmax-render__get_scene_render_statistics`, `3dsmax-render__set_render_output_options`, `3dsmax-render__set_frame_range`, `3dsmax-render__set_render_resolution`, `3dsmax-render__set_render_camera`, `3dsmax-render__set_render_quality_preset`, `3dsmax-render__render_hdr`, `3dsmax-render__render_multi_pass`, `3dsmax-render__configure_renderer` |
| `3dsmax-asset-import` | authoring | `3dsmax-asset-import__import_to_scene`, `3dsmax-asset-import__create_arnold_usd_procedural` |
| `3dsmax-lookdev` | authoring | `3dsmax-lookdev__setup_hdr_lighting`, `3dsmax-lookdev__preview_material`, `3dsmax-lookdev__assign_renderer_material` |
| `3dsmax-modeling` | authoring | `3dsmax-modeling__create_box`, `3dsmax-modeling__create_sphere`, `3dsmax-modeling__create_cylinder`, `3dsmax-modeling__create_plane` |
| `3dsmax-transform` | authoring | `3dsmax-transform__set_node_position`, `3dsmax-transform__move_nodes` |
| `3dsmax-viewport` | authoring | `3dsmax-viewport__capture_viewport` |
| `3dsmax-materials` | authoring | `3dsmax-materials__create_standard_material`, `3dsmax-materials__apply_material`, `3dsmax-materials__list_scene_materials`, `3dsmax-materials__list_node_material_assignments`, `3dsmax-materials__inspect_material`, `3dsmax-materials__list_bitmap_connections`, `3dsmax-materials__create_physical_material`, `3dsmax-materials__create_pbr_material`, `3dsmax-materials__reset_material`, `3dsmax-materials__set_material_attributes`, `3dsmax-materials__assign_bitmap_texture`, `3dsmax-materials__report_missing_textures` |
| `3dsmax-asset-source` | authoring | `3dsmax-asset-source__search_assets` |
| `3dsmax-animation` | authoring | `3dsmax-animation__set_keyframe`, `3dsmax-animation__play_animation`, `3dsmax-animation__get_time_settings`, `3dsmax-animation__get_animation_controllers`, `3dsmax-animation__list_keyframes`, `3dsmax-animation__set_current_time`, `3dsmax-animation__set_timeline_settings`, `3dsmax-animation__set_transform_keyframe`, `3dsmax-animation__delete_keyframes`, `3dsmax-animation__set_key_interpolation`, `3dsmax-animation__bake_transform_animation`, `3dsmax-animation__export_animation_curves`, `3dsmax-animation__import_animation_curves` |
| `3dsmax-rigging` | authoring | `3dsmax-rigging__create_helper_node`, `3dsmax-rigging__create_bone_node`, `3dsmax-rigging__create_joint_chain`, `3dsmax-rigging__create_path_helper`, `3dsmax-rigging__list_rig_state`, `3dsmax-rigging__apply_deformer_modifier`, `3dsmax-rigging__remove_deformer_modifier`, `3dsmax-rigging__set_constraint_target`, `3dsmax-rigging__get_character_system_availability` |
| `3dsmax-validation` | authoring | `3dsmax-validation__validate_naming`, `3dsmax-validation__validate_transforms`, `3dsmax-validation__validate_pivots`, `3dsmax-validation__validate_mesh_topology`, `3dsmax-validation__validate_smoothing_groups`, `3dsmax-validation__validate_material_assignments`, `3dsmax-validation__validate_texture_paths`, `3dsmax-validation__validate_uv_channels`, `3dsmax-validation__validate_uv_overlaps`, `3dsmax-validation__run_asset_readiness_checks` |
| `3dsmax-display` | authoring | `3dsmax-display__list_layers`, `3dsmax-display__create_layer`, `3dsmax-display__delete_layer`, `3dsmax-display__assign_nodes_to_layer`, `3dsmax-display__list_node_display_state`, `3dsmax-display__set_node_display_state`, `3dsmax-display__list_custom_properties`, `3dsmax-display__get_custom_property`, `3dsmax-display__set_custom_property`, `3dsmax-display__delete_custom_property` |
| `3dsmax-camera-lighting` | authoring | `3dsmax-camera-lighting__list_cameras`, `3dsmax-camera-lighting__list_lights`, `3dsmax-camera-lighting__create_camera`, `3dsmax-camera-lighting__set_active_camera`, `3dsmax-camera-lighting__create_light`, `3dsmax-camera-lighting__set_light_properties`, `3dsmax-camera-lighting__create_three_point_light_rig` |
| `3dsmax-scripting` | authoring | `3dsmax-scripting__execute_python`, `3dsmax-scripting__execute_maxscript`, `3dsmax-scripting__run_python_check`, `3dsmax-scripting__list_runtime_symbols`, `3dsmax-scripting__inspect_runtime_symbol`, `3dsmax-scripting__list_macros`, `3dsmax-scripting__resolve_node_reference`, `3dsmax-scripting__reload_adapter_module` |

Every bundled tool declares:

- a concrete `source_file` inside its skill directory
- explicit `input_schema` and `output_schema` objects
- `execution`, `affinity`, and `timeout_hint_secs`
- MCP safety annotations for read-only, destructive, idempotent, and open-world behavior

The bundled skill contract is enforced by the test suite and does not require
an interactive 3ds Max session.
