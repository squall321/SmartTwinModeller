"""FEM / CAE bridge skills.

Pack W1-B — FEM/CAE bridge. Provides skills that turn the CAD body into
finite-element ready artefacts:

  * export_mesh_for_fem      — write Abaqus INP / Gmsh MSH / STL surface mesh.
  * boundary_condition_tag   — attach a BC record to a face.
  * material_property_tag    — attach a material record to body or face.

The companion inspect skills (``modal_frequency_estimate`` and
``stress_concentration_predict``) live under ``phone_designer.skills.inspect``
so they sit alongside the other read-only inspectors.

The tags are stored as plain Python attributes on the body:
    body._pd_bcs       — list[dict]  appended by boundary_condition_tag
    body._pd_material  — dict        set by material_property_tag
"""
