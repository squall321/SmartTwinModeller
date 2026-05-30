"""3D printing DFM-oriented modify skills (FDM/SLA/SLS).

Skills in this category add features that help the part print successfully —
support brims, raft pads, build-plate adhesion features, orientation tweaks,
etc. Inspect-only counterparts (overhang_check, support_volume_estimate, ...)
live under ``phone_designer.skills.inspect``.
"""
from phone_designer.skills.modify_3dprint.add_support_brim import AddSupportBrim

__all__ = ["AddSupportBrim"]
