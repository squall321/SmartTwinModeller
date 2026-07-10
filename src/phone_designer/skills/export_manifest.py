"""Manifest export — 모든 등록된 skill 의 메타데이터 → manifest.json.

실행:
    python -m phone_designer.skills.export_manifest [--out manifest.json]

본 모듈을 import 하면 자동으로 phone_designer.skills 의 모든 skill 모듈을 import 해서
registry 에 등록한다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Side-effect imports — 각 모듈이 @skill 데코레이터로 자동 등록
import phone_designer.skills.compose.intersect  # noqa: F401
import phone_designer.skills.compose.subtract  # noqa: F401
import phone_designer.skills.compose.tag_face  # noqa: F401
import phone_designer.skills.compose.union  # noqa: F401
import phone_designer.skills.transform.move_body  # noqa: F401
import phone_designer.skills.transform.mirror_body  # noqa: F401
import phone_designer.skills.transform.scale_body  # noqa: F401
import phone_designer.skills.transform.split_body  # noqa: F401
import phone_designer.skills.transform.pattern_seed_body  # noqa: F401
import phone_designer.skills.transform.path_array_orientation  # noqa: F401
import phone_designer.skills.transform.deform_body  # noqa: F401
import phone_designer.skills.create.box  # noqa: F401
import phone_designer.skills.create.extrude_profile_world  # noqa: F401
import phone_designer.skills.create.sketch_extrude  # noqa: F401
import phone_designer.skills.create.sketch_revolve  # noqa: F401
import phone_designer.skills.create.sketch_sweep  # noqa: F401
import phone_designer.skills.create.sketch_loft  # noqa: F401
import phone_designer.skills.create.create_open_surface  # noqa: F401
import phone_designer.skills.create.helix_sweep  # noqa: F401
import phone_designer.skills.create.generate_from_spec  # noqa: F401
import phone_designer.skills.create.place_freeform_solid  # noqa: F401
import phone_designer.skills.create.place_generating_op_solid  # noqa: F401
import phone_designer.skills.create.disc_with_dome  # noqa: F401
import phone_designer.skills.create.import_step  # noqa: F401
import phone_designer.skills.create.offset_sketch  # noqa: F401
import phone_designer.skills.create.rounded_slab  # noqa: F401
import phone_designer.skills.modify_boss.boss_with_hole  # noqa: F401
import phone_designer.skills.modify_boss.crown_shaft_hole  # noqa: F401
import phone_designer.skills.modify_boss.extrude_boss_blended  # noqa: F401
import phone_designer.skills.modify_boss.loft_boss_between_sketches  # noqa: F401
import phone_designer.skills.modify_boss.lug_pair  # noqa: F401
import phone_designer.skills.modify_boss.mounting_pad  # noqa: F401
import phone_designer.skills.modify_boss.o_ring_groove  # noqa: F401
import phone_designer.skills.modify_boss.revolve_boss  # noqa: F401
import phone_designer.skills.modify_boss.rib  # noqa: F401
import phone_designer.skills.modify_boss.snap_hook  # noqa: F401
import phone_designer.skills.modify_boss.swept_boss_along_curve  # noqa: F401
import phone_designer.skills.modify_boss.text_emboss  # noqa: F401
import phone_designer.skills.modify_antenna.antenna_slit  # noqa: F401
import phone_designer.skills.modify_antenna.polymer_inlay  # noqa: F401
import phone_designer.skills.modify_curvature.chamfer_asymmetric  # noqa: F401
import phone_designer.skills.modify_curvature.chamfer_predicate  # noqa: F401
import phone_designer.skills.modify_curvature.face_face_fillet  # noqa: F401
import phone_designer.skills.modify_curvature.fillet_predicate  # noqa: F401
import phone_designer.skills.modify_curvature.loft_side_profile  # noqa: F401
import phone_designer.skills.modify_curvature.surface_offset  # noqa: F401
import phone_designer.skills.modify_curvature.swept_relief  # noqa: F401
import phone_designer.skills.modify_curvature.variable_radius_fillet  # noqa: F401
import phone_designer.skills.modify_finish.deburring  # noqa: F401
import phone_designer.skills.modify_finish.final_fillet  # noqa: F401
import phone_designer.skills.modify_finish.sanding_pass  # noqa: F401
import phone_designer.skills.modify_finish.surface_finish_tag  # noqa: F401
import phone_designer.skills.modify_pocket.extrude_plateau  # noqa: F401
import phone_designer.skills.modify_pocket.extrude_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.extrude_pocket_blended  # noqa: F401
import phone_designer.skills.modify_pocket.extrude_pocket_world  # noqa: F401
import phone_designer.skills.modify_pocket.extrude_through  # noqa: F401
import phone_designer.skills.modify_pocket.loft_pocket_between_sketches  # noqa: F401
import phone_designer.skills.modify_pocket.grille_pattern  # noqa: F401
import phone_designer.skills.modify_pocket.helical_thread_external  # noqa: F401
import phone_designer.skills.modify_pocket.helical_thread_internal  # noqa: F401
import phone_designer.skills.modify_pocket.hole  # noqa: F401
import phone_designer.skills.modify_pocket.hole_array  # noqa: F401
import phone_designer.skills.modify_pocket.revolve_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.swept_pocket_along_curve  # noqa: F401
import phone_designer.skills.modify_pocket.text_engrave  # noqa: F401
import phone_designer.skills.modify_pattern.circular_pattern  # noqa: F401
import phone_designer.skills.modify_pattern.linear_pattern  # noqa: F401
import phone_designer.skills.modify_pattern.mirror_about_two_planes  # noqa: F401
import phone_designer.skills.modify_pattern.mirror_feature  # noqa: F401
import phone_designer.skills.modify_pattern.nested_pattern  # noqa: F401
import phone_designer.skills.modify_pattern.path_pattern  # noqa: F401
import phone_designer.skills.modify_pattern.variable_pattern  # noqa: F401
import phone_designer.skills.inspect.cross_section  # noqa: F401
import phone_designer.skills.inspect.find_features  # noqa: F401
import phone_designer.skills.inspect.inspect_geometry  # noqa: F401
import phone_designer.skills.inspect.measure  # noqa: F401
import phone_designer.skills.inspect.selector_preview  # noqa: F401
import phone_designer.skills.inspect.silhouette  # noqa: F401
import phone_designer.skills.inspect.symmetry_axes  # noqa: F401
import phone_designer.skills.assembly.add_component  # noqa: F401
import phone_designer.skills.assembly.bom_extract  # noqa: F401
import phone_designer.skills.assembly.fastener_array  # noqa: F401
import phone_designer.skills.assembly.interference_check  # noqa: F401
import phone_designer.skills.assembly.mate_at_distance  # noqa: F401
import phone_designer.skills.assembly.mate_axis  # noqa: F401
import phone_designer.skills.assembly.mate_concentric  # noqa: F401
import phone_designer.skills.assembly.mate_planar  # noqa: F401
import phone_designer.skills.assembly.move_component  # noqa: F401
import phone_designer.skills.create.bspline_surface  # noqa: F401
import phone_designer.skills.create.coil_spring_rectangular  # noqa: F401
import phone_designer.skills.create.cone  # noqa: F401
import phone_designer.skills.create.cylinder  # noqa: F401
import phone_designer.skills.create.gear_external_involute  # noqa: F401
import phone_designer.skills.create.gear_internal_involute  # noqa: F401
import phone_designer.skills.create.gyroid_lattice  # noqa: F401
import phone_designer.skills.create.helical_spring  # noqa: F401
import phone_designer.skills.create.prism_n_sided  # noqa: F401
import phone_designer.skills.create.sphere  # noqa: F401
import phone_designer.skills.create.torus  # noqa: F401
import phone_designer.skills.create.voronoi_lattice  # noqa: F401
import phone_designer.skills.create.wedge  # noqa: F401
import phone_designer.skills.inspect.curvature_map  # noqa: F401
import phone_designer.skills.inspect.gdt_circularity  # noqa: F401
import phone_designer.skills.inspect.gdt_cylindricity  # noqa: F401
import phone_designer.skills.inspect.gdt_flatness  # noqa: F401
import phone_designer.skills.inspect.gdt_parallelism  # noqa: F401
import phone_designer.skills.inspect.gdt_perpendicularity  # noqa: F401
import phone_designer.skills.inspect.gdt_position  # noqa: F401
import phone_designer.skills.inspect.hole_alignment_check  # noqa: F401
import phone_designer.skills.inspect.joint_check  # noqa: F401
import phone_designer.skills.inspect.detect_sheet_metal  # noqa: F401
import phone_designer.skills.inspect.estimate_cost  # noqa: F401
import phone_designer.skills.inspect.mass_properties  # noqa: F401
import phone_designer.skills.inspect.oriented_bounding_box  # noqa: F401
import phone_designer.skills.inspect.section_to_sketch  # noqa: F401
import phone_designer.skills.inspect.curvature_comb  # noqa: F401
import phone_designer.skills.inspect.cosmetic_thread  # noqa: F401
import phone_designer.skills.inspect.quote_package  # noqa: F401
import phone_designer.skills.inspect.sketch_relation_check  # noqa: F401
import phone_designer.skills.inspect.hlr_view  # noqa: F401
import phone_designer.skills.inspect.drawing_sheet  # noqa: F401
import phone_designer.skills.inspect.render_preview  # noqa: F401
import phone_designer.skills.inspect.measure_assembly_fit  # noqa: F401
import phone_designer.skills.inspect.recommend_process  # noqa: F401
import phone_designer.skills.inspect.mesh_quality  # noqa: F401
import phone_designer.skills.inspect.recognize_fits  # noqa: F401
import phone_designer.skills.inspect.section_multi_plane  # noqa: F401
import phone_designer.skills.inspect.surface_area_by_region  # noqa: F401
from phone_designer.skills.inspect import datum_plane_assign as _i_datum_plane_assign  # noqa: F401
from phone_designer.skills.inspect import edge_to_edge_distance as _i_edge_to_edge_distance  # noqa: F401
from phone_designer.skills.inspect import hole_to_hole_distance as _i_hole_to_hole_distance  # noqa: F401
from phone_designer.skills.inspect import section_at_centroid as _i_section_at_centroid  # noqa: F401
from phone_designer.skills.inspect import tolerance_stack as _i_tolerance_stack  # noqa: F401
import phone_designer.skills.io.brep_to_mesh  # noqa: F401
import phone_designer.skills.io.mesh_simplify  # noqa: F401
import phone_designer.skills.io.mesh_to_brep  # noqa: F401
import phone_designer.skills.io.dxf_export  # noqa: F401
import phone_designer.skills.io.mesh_export  # noqa: F401
import phone_designer.skills.io.mesh_import  # noqa: F401
import phone_designer.skills.io.point_cloud_import  # noqa: F401
import phone_designer.skills.io.gltf_export  # noqa: F401
import phone_designer.skills.io.stl_export  # noqa: F401
import phone_designer.skills.io.stl_import  # noqa: F401
import phone_designer.skills.modify_boss.battery_dock_pad  # noqa: F401
import phone_designer.skills.modify_boss.cable_clip  # noqa: F401
import phone_designer.skills.modify_boss.embossed_pattern  # noqa: F401
import phone_designer.skills.modify_boss.gusset  # noqa: F401
import phone_designer.skills.modify_boss.heat_stake_boss  # noqa: F401
import phone_designer.skills.modify_boss.heatsink_fin  # noqa: F401
import phone_designer.skills.modify_boss.hinge_pin_boss  # noqa: F401
import phone_designer.skills.modify_boss.standoff  # noqa: F401
import phone_designer.skills.modify_curvature.chamfer_conic  # noqa: F401
import phone_designer.skills.modify_curvature.chamfer_distance_angle  # noqa: F401
import phone_designer.skills.modify_curvature.chamfer_two_distance  # noqa: F401
import phone_designer.skills.modify_curvature.shell_variable_thickness  # noqa: F401
import phone_designer.skills.modify_curvature.fill_surface_patch  # noqa: F401
import phone_designer.skills.modify_curvature.trim_surface  # noqa: F401
import phone_designer.skills.modify_curvature.move_face  # noqa: F401
import phone_designer.skills.modify_curvature.replace_face  # noqa: F401
import phone_designer.skills.modify_curvature.surface_blend_g1  # noqa: F401
import phone_designer.skills.modify_curvature.surface_blend_g2  # noqa: F401
import phone_designer.skills.modify_curvature.surface_extend_tangent  # noqa: F401
import phone_designer.skills.modify_curvature.surface_split_with_curve  # noqa: F401
import phone_designer.skills.modify_curvature.surface_thicken_variable  # noqa: F401
import phone_designer.skills.modify_curvature.variable_radius_fillet_with_law  # noqa: F401
import phone_designer.skills.modify_curvature.vertex_chamfer  # noqa: F401
import phone_designer.skills.modify_mold.core_cavity_split  # noqa: F401
import phone_designer.skills.modify_mold.draft_apply_auto  # noqa: F401
import phone_designer.skills.modify_mold.ejector_pin_clearance  # noqa: F401
import phone_designer.skills.modify_mold.parting_surface  # noqa: F401
import phone_designer.skills.modify_pocket.breathing_hole_array  # noqa: F401
import phone_designer.skills.modify_pocket.cable_routing_channel  # noqa: F401
import phone_designer.skills.modify_pocket.extrude_pocket_to_curved_floor  # noqa: F401
import phone_designer.skills.modify_pocket.honeycomb_pattern  # noqa: F401
import phone_designer.skills.modify_pocket.knurl_pattern  # noqa: F401
import phone_designer.skills.modify_pocket.swept_pocket_variable_section  # noqa: F401
import phone_designer.skills.modify_pocket.wrap_sketch_on_curved_surface  # noqa: F401
import phone_designer.skills.modify_pocket.bearing_bore  # noqa: F401
import phone_designer.skills.modify_pocket.captive_nut_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.clearance_hole  # noqa: F401
import phone_designer.skills.modify_pocket.counterbore_hole  # noqa: F401
import phone_designer.skills.modify_pocket.countersink_hole  # noqa: F401
import phone_designer.skills.modify_pocket.dowel_pin_hole  # noqa: F401
import phone_designer.skills.modify_pocket.heatset_insert_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.helicoil_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.hex_socket_recess  # noqa: F401
import phone_designer.skills.modify_pocket.keyseat_hub  # noqa: F401
import phone_designer.skills.modify_pocket.keyseat_shaft  # noqa: F401
import phone_designer.skills.modify_pocket.nut_trap_hex  # noqa: F401
import phone_designer.skills.modify_pocket.nut_trap_square  # noqa: F401
import phone_designer.skills.modify_pocket.o_ring_groove_axial_spec  # noqa: F401
import phone_designer.skills.modify_pocket.o_ring_groove_radial_spec  # noqa: F401
import phone_designer.skills.modify_pocket.phillips_recess  # noqa: F401
import phone_designer.skills.modify_pocket.snap_ring_groove_external  # noqa: F401
import phone_designer.skills.modify_pocket.snap_ring_groove_internal  # noqa: F401
import phone_designer.skills.modify_pocket.tap_drill_hole  # noqa: F401
import phone_designer.skills.modify_pocket.torx_recess  # noqa: F401
import phone_designer.skills.modify_sheet.bend_edge  # noqa: F401
import phone_designer.skills.modify_sheet.bend_relief  # noqa: F401
import phone_designer.skills.modify_sheet.dimple  # noqa: F401
import phone_designer.skills.modify_sheet.flange  # noqa: F401
import phone_designer.skills.modify_sheet.hem  # noqa: F401
import phone_designer.skills.modify_sheet.jog  # noqa: F401
import phone_designer.skills.modify_sheet.louver  # noqa: F401
import phone_designer.skills.modify_sheet.sheet_base  # noqa: F401
import phone_designer.skills.modify_sheet.tab_slot  # noqa: F401
import phone_designer.skills.modify_sheet.unfold  # noqa: F401
import phone_designer.skills.inspect.cosmetic_side_classify  # noqa: F401
import phone_designer.skills.inspect.inspect_undercut_zones  # noqa: F401
import phone_designer.skills.inspect.inspect_wall_thickness  # noqa: F401
import phone_designer.skills.modify_boss.barrel_pivot_socket_pair  # noqa: F401
import phone_designer.skills.modify_boss.spring_bar_lug_pair  # noqa: F401
import phone_designer.skills.modify_pattern.hvac_blade_array  # noqa: F401
import phone_designer.skills.modify_pocket.bearing_seat  # noqa: F401
import phone_designer.skills.modify_pocket.connector_cutout_from_catalog  # noqa: F401
import phone_designer.skills.modify_pocket.crown_seal_gland  # noqa: F401
import phone_designer.skills.modify_pocket.display_bezel_step_with_adhesive_groove  # noqa: F401
import phone_designer.skills.modify_pocket.lip_seal_cavity  # noqa: F401
import phone_designer.skills.modify_pocket.o_ring_groove_path  # noqa: F401
import phone_designer.skills.modify_pocket.retaining_ring_groove  # noqa: F401
import phone_designer.skills.modify_pocket.sim_tray_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.speaker_acoustic_chamber  # noqa: F401
import phone_designer.skills.modify_pocket.vent_membrane_pocket  # noqa: F401
from phone_designer.skills.modify_pocket.connector_cutout_from_catalog import ConnectorCutoutFromCatalog
import phone_designer.skills.modify_pocket.foam_seal_groove_racetrack  # noqa: F401
from phone_designer.skills.modify_pocket import vent_membrane_pocket as _vent_membrane_pocket
import phone_designer.skills.modify_pocket.helmholtz_port_tube  # noqa: F401
import phone_designer.skills.modify_pattern.acoustic_grille_pattern  # noqa: F401
from phone_designer.skills.modify_pocket.magnet_pocket_axial import MagnetPocketAxial
import phone_designer.skills.modify_pocket.wireless_charging_coil_seat  # noqa: F401
import phone_designer.skills.modify_pocket.coin_cell_cavity  # noqa: F401
import phone_designer.skills.inspect.inspect_wall_thickness_v2  # noqa: F401
import phone_designer.skills.inspect.inspect_undercut_zones_v2  # noqa: F401
import phone_designer.skills.inspect.inspect_sink_mark_risk  # noqa: F401
import phone_designer.skills.compose.class_a_surface_tag  # noqa: F401
import phone_designer.skills.modify_pocket.display_bezel_step  # noqa: F401
import phone_designer.skills.modify_pocket.camera_ring_stepped  # noqa: F401
import phone_designer.skills.modify_pocket.side_button_aperture  # noqa: F401
import phone_designer.skills.modify_pocket.sapphire_glass_seat  # noqa: F401
from phone_designer.skills.modify_boss.christmas_tree_fastener_post import ChristmasTreeFastenerPost
import phone_designer.skills.modify_pocket.membrane_keypad_recess  # noqa: F401
import phone_designer.skills.modify_finish.cleanability_radius_enforce  # noqa: F401
import phone_designer.skills.compose.biocompat_region_tag  # noqa: F401
import phone_designer.skills.modify_pocket.captive_screw_tether_pocket  # noqa: F401
from phone_designer.skills.modify_pocket.light_pipe_channel import LightPipeChannel
from phone_designer.skills.modify_pocket.hinge_detent_cam import HingeDetentCam
import phone_designer.skills.modify_boss.magsafe_magnet_ring  # noqa: F401
import phone_designer.skills.modify_pattern.motor_flange_bolt_pattern  # noqa: F401
from phone_designer.skills.modify_pattern.speaker_grille_curved_array import SpeakerGrilleCurvedArray  # noqa: F401
import phone_designer.skills.modify_pocket.stylus_tip_clearance  # noqa: F401
import phone_designer.skills.modify_boss.button_cap_rounded_actuation  # noqa: F401
import phone_designer.skills.inspect.dry_run_skill  # noqa: F401
import phone_designer.skills.inspect.selector_robustness_score  # noqa: F401
import phone_designer.skills.inspect.tag_inventory  # noqa: F401
import phone_designer.skills.inspect.find_skill_by_intent  # noqa: F401
import phone_designer.skills.inspect.skill_schema_introspect  # noqa: F401
import phone_designer.skills.inspect.highlight_line_view  # noqa: F401
import phone_designer.skills.inspect.zebra_stripe_view  # noqa: F401
import phone_designer.skills.inspect.continuity_audit  # noqa: F401
import phone_designer.skills.inspect.enforce_min_tool_radius  # noqa: F401
import phone_designer.skills.inspect.tool_reachability  # noqa: F401
import phone_designer.skills.inspect.pocket_aspect_ratio_check  # noqa: F401
from phone_designer.skills.create.gear_helical_involute import GearHelicalInvolute
from phone_designer.skills.create.worm_thread import WormThread

# Reverse-engineering boost — repair / inspect / reverse_engineer / io
import phone_designer.skills.repair.fill_small_holes  # noqa: F401
import phone_designer.skills.repair.delete_face_defeature  # noqa: F401
import phone_designer.skills.repair.shape_heal  # noqa: F401
import phone_designer.skills.repair.repair_dfm  # noqa: F401
import phone_designer.skills.reverse_engineer.cost_min_variant_search  # noqa: F401
import phone_designer.skills.repair.sew_faces_to_shell  # noqa: F401
import phone_designer.skills.repair.close_shell_to_solid  # noqa: F401
import phone_designer.skills.repair.simplify_to_canonical  # noqa: F401
import phone_designer.skills.repair.remove_micro_features  # noqa: F401
import phone_designer.skills.repair.split_into_components  # noqa: F401
from phone_designer.skills.inspect.face_adjacency_graph import FaceAdjacencyGraph
from phone_designer.skills.inspect.edge_concavity_classify import EdgeConcavityClassify
from phone_designer.skills.inspect.vertex_connectivity import VertexConnectivity
import phone_designer.skills.inspect.fit_plane  # noqa: F401
import phone_designer.skills.inspect.fit_cylinder  # noqa: F401
import phone_designer.skills.inspect.fit_sphere  # noqa: F401
import phone_designer.skills.inspect.fit_cone  # noqa: F401
import phone_designer.skills.inspect.fit_torus  # noqa: F401
import phone_designer.skills.inspect.classify_pockets  # noqa: F401
import phone_designer.skills.inspect.classify_holes  # noqa: F401
import phone_designer.skills.inspect.detect_bosses  # noqa: F401
import phone_designer.skills.inspect.detect_shell_holes  # noqa: F401
import phone_designer.skills.inspect.detect_ribs  # noqa: F401
import phone_designer.skills.inspect.detect_standoffs  # noqa: F401
import phone_designer.skills.inspect.detect_lugs  # noqa: F401
import phone_designer.skills.inspect.detect_mirror_symmetry  # noqa: F401
import phone_designer.skills.inspect.detect_rotational_symmetry  # noqa: F401
import phone_designer.skills.inspect.detect_linear_array  # noqa: F401
import phone_designer.skills.inspect.detect_circular_array  # noqa: F401
import phone_designer.skills.inspect.match_standard_hole  # noqa: F401
import phone_designer.skills.inspect.match_standard_bearing  # noqa: F401
import phone_designer.skills.inspect.match_standard_oring  # noqa: F401
import phone_designer.skills.inspect.identify_fastener_recess  # noqa: F401
import phone_designer.skills.inspect.base_step_kind_chooser  # noqa: F401
import phone_designer.skills.inspect.body_orientation_check  # noqa: F401
import phone_designer.skills.inspect.topology_health  # noqa: F401
import phone_designer.skills.inspect.geometry_deviation  # noqa: F401
import phone_designer.skills.inspect.validate_rebuilt_body  # noqa: F401
import phone_designer.skills.inspect.fit_bspline_surface_patch  # noqa: F401
import phone_designer.skills.inspect.recover_freeform_solid  # noqa: F401
import phone_designer.skills.inspect.classify_generating_op  # noqa: F401
import phone_designer.skills.inspect.inspect_draft_angles  # noqa: F401
import phone_designer.skills.inspect.classify_edge_blends  # noqa: F401
import phone_designer.skills.inspect.measure_thread  # noqa: F401
import phone_designer.skills.inspect.extract_quality_report  # noqa: F401
import phone_designer.skills.inspect.dfm_verdict  # noqa: F401
import phone_designer.skills.inspect.emit_quality_report  # noqa: F401
import phone_designer.skills.inspect.extract_outer_silhouette_profile  # noqa: F401
import phone_designer.skills.inspect.register_bodies  # noqa: F401
import phone_designer.skills.reverse_engineer.extract_feature_catalog  # noqa: F401
import phone_designer.skills.reverse_engineer.feature_fidelity_diff  # noqa: F401
import phone_designer.skills.reverse_engineer.plan_from_feature_catalog  # noqa: F401
import phone_designer.skills.reverse_engineer.vary_feature_catalog  # noqa: F401
import phone_designer.skills.reverse_engineer.plan_from_scaled_catalog  # noqa: F401
import phone_designer.skills.reverse_engineer.normalize_varied_catalog  # noqa: F401
import phone_designer.skills.reverse_engineer.feature_change_classify  # noqa: F401
import phone_designer.skills.reverse_engineer.identify_key_dimensions  # noqa: F401
import phone_designer.skills.reverse_engineer.compare_parts  # noqa: F401
import phone_designer.skills.reverse_engineer.analyze_part  # noqa: F401
import phone_designer.skills.reverse_engineer.analyze_assembly  # noqa: F401
import phone_designer.skills.reverse_engineer.plan_reexecute  # noqa: F401
import phone_designer.skills.reverse_engineer.plan_edit  # noqa: F401
import phone_designer.skills.reverse_engineer.mesh_segment_regions  # noqa: F401
import phone_designer.skills.reverse_engineer.fit_region_surfaces  # noqa: F401
import phone_designer.skills.reverse_engineer.scan_to_brep  # noqa: F401
import phone_designer.skills.reverse_engineer.emit_parametric_script  # noqa: F401
import phone_designer.skills.reverse_engineer.recover_design_relations  # noqa: F401
import phone_designer.skills.reverse_engineer.apply_variant_drivers  # noqa: F401
import phone_designer.skills.reverse_engineer.generate_variant_family  # noqa: F401
import phone_designer.skills.reverse_engineer.snap_to_standards  # noqa: F401
import phone_designer.skills.reverse_engineer.solve_driver_range  # noqa: F401
import phone_designer.skills.reverse_engineer.assembly_reverse_engineer  # noqa: F401
from phone_designer.skills.io.iges_import import IgesImport
from phone_designer.skills.io.iges_export import IgesExport
from phone_designer.skills.io.brep_import import BrepImport
from phone_designer.skills.io.brep_export import BrepExport
from phone_designer.skills.io.step_export_v2 import StepExportV2
import phone_designer.skills.inspect.auto_dimension  # noqa: F401
import phone_designer.skills.inspect.cross_section_at_features  # noqa: F401
import phone_designer.skills.inspect.auto_datum_planes  # noqa: F401
import phone_designer.skills.inspect.principal_axes  # noqa: F401

# Round 5 — 3D-printing DFM + mold depth + GD&T ext + assembly depth + catalog backbone + LLM control + viz tessellation
from phone_designer.skills.inspect.overhang_check import OverhangCheck
from phone_designer.skills.inspect.support_volume_estimate import SupportVolumeEstimate
from phone_designer.skills.inspect.bridge_length_check import BridgeLengthCheck
from phone_designer.skills.inspect.orientation_optimize import OrientationOptimize
from phone_designer.skills.modify_3dprint.add_support_brim import AddSupportBrim
import phone_designer.skills.modify_mold.cooling_channel_path  # noqa: F401
import phone_designer.skills.inspect.gating_position_candidates  # noqa: F401
import phone_designer.skills.modify_mold.runner_diameter_calc  # noqa: F401
import phone_designer.skills.modify_mold.slide_action_geometry  # noqa: F401
import phone_designer.skills.modify_mold.ejector_pin_pattern  # noqa: F401
import phone_designer.skills.inspect.datum_target_assign  # noqa: F401
import phone_designer.skills.inspect.basic_dimension_attach  # noqa: F401
import phone_designer.skills.inspect.projected_tolerance_zone  # noqa: F401
import phone_designer.skills.inspect.mmc_lmc_modifier  # noqa: F401
import phone_designer.skills.inspect.runout_check  # noqa: F401
import phone_designer.skills.inspect.feature_control_frame_compose  # noqa: F401
import phone_designer.skills.assembly.exploded_view  # noqa: F401
import phone_designer.skills.assembly.sub_assembly_tag  # noqa: F401
import phone_designer.skills.assembly.auto_place_fasteners  # noqa: F401
import phone_designer.skills.assembly.bolt_pattern_recognize  # noqa: F401
import phone_designer.skills.assembly.check_clearance_full  # noqa: F401
from phone_designer.skills.inspect import catalog_lookup
from phone_designer.skills.inspect import catalog_search
import phone_designer.skills.inspect.suggest_selector_from_phrase  # noqa: F401
import phone_designer.skills.inspect.predict_post_conditions  # noqa: F401
import phone_designer.skills.inspect.explain_skill_failure  # noqa: F401
import phone_designer.skills.inspect.find_similar_skills  # noqa: F401
import phone_designer.skills.inspect.plan_diff  # noqa: F401
import phone_designer.skills.inspect.face_tessellate  # noqa: F401
import phone_designer.skills.inspect.edge_polyline_extract  # noqa: F401
import phone_designer.skills.inspect.curvature_at_point  # noqa: F401
import phone_designer.skills.inspect.normal_map_compute  # noqa: F401
import phone_designer.skills.inspect.color_by_property  # noqa: F401

# Round 6 — product domains (3D-print extras + FEM/CAE bridge + surface finishes + biometric + phone-deep + audio-deep)
from phone_designer.skills.modify_3dprint.raft_add import RaftAdd  # noqa: F401
from phone_designer.skills.modify_3dprint.support_tree_path import SupportTreePath  # noqa: F401
from phone_designer.skills.modify_3dprint.infill_region_tag import InfillRegionTag  # noqa: F401
from phone_designer.skills.inspect.slicer_hints import SlicerHints  # noqa: F401
import phone_designer.skills.fem_cae.export_mesh_for_fem  # noqa: F401
import phone_designer.skills.fem_cae.boundary_condition_tag  # noqa: F401
import phone_designer.skills.fem_cae.material_property_tag  # noqa: F401
import phone_designer.skills.inspect.modal_frequency_estimate  # noqa: F401
import phone_designer.skills.inspect.stress_concentration_predict  # noqa: F401
import phone_designer.skills.modify_finish.apply_anodize  # noqa: F401
import phone_designer.skills.modify_finish.apply_paint  # noqa: F401
import phone_designer.skills.modify_finish.apply_plating  # noqa: F401
import phone_designer.skills.modify_finish.apply_texture_region  # noqa: F401
import phone_designer.skills.modify_pocket.emi_gasket_groove  # noqa: F401
import phone_designer.skills.modify_pocket.capacitive_touch_pad  # noqa: F401
import phone_designer.skills.modify_pocket.ecg_electrode_pad  # noqa: F401
import phone_designer.skills.modify_pocket.heart_rate_optical_dome  # noqa: F401
import phone_designer.skills.modify_boss.haptic_motor_mount  # noqa: F401
import phone_designer.skills.modify_pocket.temperature_sensor_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.face_id_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.nfc_coil_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.antenna_isolation_slit  # noqa: F401
import phone_designer.skills.modify_pocket.esim_chip_pocket  # noqa: F401
import phone_designer.skills.modify_boss.side_button_dome_mount  # noqa: F401
import phone_designer.skills.modify_curvature.waveguide_horn_profile  # noqa: F401
import phone_designer.skills.modify_pocket.bass_reflex_port_flared  # noqa: F401
import phone_designer.skills.modify_pocket.anechoic_chamber_liner_mount  # noqa: F401
import phone_designer.skills.modify_pocket.mems_mic_boot_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.speaker_baffle_window  # noqa: F401

# Round 7 — PMI/AP242 + CAE depth + UI inspector + optical features
import phone_designer.skills.pmi.pmi_dimension_callout  # noqa: F401
import phone_designer.skills.pmi.pmi_surface_texture  # noqa: F401
import phone_designer.skills.pmi.pmi_weld_symbol  # noqa: F401
import phone_designer.skills.pmi.export_step_ap242_pmi  # noqa: F401
import phone_designer.skills.pmi.pmi_inspect_summary  # noqa: F401
import phone_designer.skills.pmi.read_step_pmi  # noqa: F401
import phone_designer.skills.fem_cae.mesh_refinement_zones  # noqa: F401
import phone_designer.skills.fem_cae.thermal_bc_tag  # noqa: F401
import phone_designer.skills.fem_cae.contact_pair  # noqa: F401
import phone_designer.skills.fem_cae.modal_analysis_setup  # noqa: F401
import phone_designer.skills.fem_cae.load_case_compose  # noqa: F401
import phone_designer.skills.fem_cae.export_abaqus_inp_v2  # noqa: F401
import phone_designer.skills.inspect.launch_ui_panel  # noqa: F401
import phone_designer.skills.modify_pocket.led_smd_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.lens_seat_pocket  # noqa: F401
import phone_designer.skills.modify_pocket.light_pipe_routing_channel  # noqa: F401
import phone_designer.skills.modify_curvature.optical_window_dome  # noqa: F401
import phone_designer.skills.inspect.ray_trace_smoke  # noqa: F401

# Stragglers from walk
import phone_designer.skills.io.mesh_decimate  # noqa: F401

from phone_designer.skills._registry import registry
from phone_designer.skills._selectors import selector_json_schemas


def build_manifest() -> dict:
    """skill manifest + selector schemas 결합."""
    m = registry.to_manifest()
    m["selectors"] = selector_json_schemas()
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out", type=Path, default=Path("manifest.json"),
        help="출력 manifest.json 경로 (default: ./manifest.json)",
    )
    ap.add_argument(
        "--stdout", action="store_true",
        help="파일 대신 stdout 으로 출력",
    )
    args = ap.parse_args()

    import json
    m = build_manifest()
    text = json.dumps(m, ensure_ascii=False, indent=2)

    if args.stdout:
        print(text)
    else:
        args.out.write_text(text, encoding="utf-8")
        print(f"manifest exported: {args.out}", file=sys.stderr)
        print(f"  skills:    {len(m['skills'])}", file=sys.stderr)
        print(f"  selectors: {len(m['selectors'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
