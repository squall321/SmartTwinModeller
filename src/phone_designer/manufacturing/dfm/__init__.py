"""DFM v0 — ray-march wall thickness + draft + undercut.

[[lat.md/manufacturing#dfm-v0]] 의 spec.
"""
from phone_designer.manufacturing.dfm.report import (
    DFMReport,
    DFMViolation,
    DFMSeverity,
)
from phone_designer.manufacturing.dfm.wall_thickness import wall_thickness_raymarch
from phone_designer.manufacturing.dfm.draft import draft_violations
from phone_designer.manufacturing.dfm.undercut import undercut_violations
from phone_designer.manufacturing.dfm.runner import run_dfm

__all__ = [
    "DFMReport",
    "DFMViolation",
    "DFMSeverity",
    "wall_thickness_raymarch",
    "draft_violations",
    "undercut_violations",
    "run_dfm",
]
