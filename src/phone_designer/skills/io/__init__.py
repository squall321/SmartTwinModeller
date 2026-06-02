"""IO skills — mesh-based + format-bridge import/export (STL, IGES, BREP, STEP v2).

STEP import lives in `phone_designer.skills.create.import_step` because STEP is
a B-rep boundary representation native to OCCT; STL on the other hand is a
discrete triangle soup and gets its own home here so the I/O layer can grow
(OBJ, PLY, 3MF …) without crowding the parametric `create` namespace.

Wave 4-B added native format bridges:
  * IGES — legacy neutral CAD interchange (.igs/.iges)
  * BREP — OCCT-native lossless serialization (.brep)
  * STEP v2 — schema-aware (AP203/AP214/AP242) writer with color preservation
"""
from phone_designer.skills.io.brep_export import BrepExport
from phone_designer.skills.io.brep_import import BrepImport
from phone_designer.skills.io.iges_export import IgesExport
from phone_designer.skills.io.iges_import import IgesImport
from phone_designer.skills.io.mesh_decimate import MeshDecimate
from phone_designer.skills.io.step_export_v2 import StepExportV2
from phone_designer.skills.io.stl_export import StlExport
from phone_designer.skills.io.stl_import import StlImport

__all__ = [
    "StlImport", "StlExport",
    "IgesImport", "IgesExport",
    "BrepImport", "BrepExport",
    "StepExportV2",
    "MeshDecimate",
]
