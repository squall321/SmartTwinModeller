"""Sheet-metal modification skills.

Atomic sheet primitives used to model bent / hemmed / tabbed sheet stock —
all built on raw OCCT (BRepPrimAPI / BRepBuilderAPI). These skills assume a
flat starting sheet aligned with the world XY plane (z = 0..thickness), which
is what `sheet_base` produces.

Skills:
    SheetBase   — flat sheet (rectangle x thickness)
    BendEdge    — bend a half of the sheet about a straight edge (angle, radius)
    Flange      — add a flat extension via a bend at the sheet edge
    Hem         — 180-deg fold-back (with optional gap between layers)
    TabSlot     — small tab protrusion or matching slot pocket on a sheet edge
"""
from phone_designer.skills.modify_sheet.sheet_base import SheetBase
from phone_designer.skills.modify_sheet.bend_edge import BendEdge
from phone_designer.skills.modify_sheet.flange import Flange
from phone_designer.skills.modify_sheet.hem import Hem
from phone_designer.skills.modify_sheet.tab_slot import Tab, Slot

__all__ = [
    "SheetBase",
    "BendEdge",
    "Flange",
    "Hem",
    "Tab",
    "Slot",
]
