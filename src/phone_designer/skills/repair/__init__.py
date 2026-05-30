"""Shape repair / healing skills.

Wraps OCCT's ShapeFix / ShapeUpgrade / BRepBuilderAPI sewing+solidify family
so plans can recover from defective B-rep input (tiny gaps, flipped normals,
open shells, micro features, redundant face splits). All skills here preserve
the body — body_present post-condition only — but the geometry may legitimately
mutate (face count drops after `simplify_to_canonical`, edges merged, etc.).
"""

from phone_designer.skills.repair.close_shell_to_solid import CloseShellToSolid
from phone_designer.skills.repair.remove_micro_features import RemoveMicroFeatures
from phone_designer.skills.repair.sew_faces_to_shell import SewFacesToShell
from phone_designer.skills.repair.shape_heal import ShapeHeal
from phone_designer.skills.repair.simplify_to_canonical import SimplifyToCanonical

__all__ = [
    "ShapeHeal",
    "SewFacesToShell",
    "CloseShellToSolid",
    "SimplifyToCanonical",
    "RemoveMicroFeatures",
]
