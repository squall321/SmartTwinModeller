"""IO skills — mesh-based import/export (STL etc.).

STEP import lives in `phone_designer.skills.create.import_step` because STEP is
a B-rep boundary representation native to OCCT; STL on the other hand is a
discrete triangle soup and gets its own home here so the I/O layer can grow
(OBJ, PLY, 3MF …) without crowding the parametric `create` namespace.
"""
from phone_designer.skills.io.stl_export import StlExport
from phone_designer.skills.io.stl_import import StlImport

__all__ = ["StlImport", "StlExport"]
