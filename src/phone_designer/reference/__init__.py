"""Reference 처리 — STEP topology 분석 + reverse engineering.

[[lat.md/reference]] 의 본격 구현. Phase 3 핵심.

진입점:
    read_xde_step(path) → list[PartInfo]                # XDE 어셈블리 + 네이밍
    classify_parts(parts) → dict[category, list[PartInfo]]  # naming → category
    TopologyAnalyzer().analyze(shape) → FeatureCatalog
    feature_to_plan(catalog, housing_bbox) → Plan
"""
from phone_designer.reference.step_reader import (
    PartInfo,
    classify_parts,
    read_xde_step,
)
from phone_designer.reference.topology_analyzer import (
    ChamferFeature,
    FaceInfo,
    FeatureCatalog,
    FilletFeature,
    HoleFeature,
    PocketFeature,
    PlateauFeature,
    TopologyAnalyzer,
)
from phone_designer.reference.feature_to_plan import feature_to_plan

__all__ = [
    "PartInfo",
    "classify_parts",
    "read_xde_step",
    "FaceInfo",
    "FilletFeature",
    "ChamferFeature",
    "HoleFeature",
    "PocketFeature",
    "PlateauFeature",
    "FeatureCatalog",
    "TopologyAnalyzer",
    "feature_to_plan",
]
