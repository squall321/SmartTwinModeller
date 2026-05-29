"""Component 카탈로그 + 배치 + 충돌 (Phase 6)."""
from phone_designer.components.model import (
    BoundingBox,
    ClearanceSpec,
    Component,
    ComponentSource,
    MountInterface,
    Pose,
    Port,
)
from phone_designer.components.catalog_loader import (
    discover_catalogs,
    load_catalog,
    load_component,
)
from phone_designer.components.arrangement import ComponentArrangement
from phone_designer.components.collision import (
    has_collision,
    collision_report,
)

__all__ = [
    "BoundingBox", "ClearanceSpec", "Component", "ComponentSource",
    "MountInterface", "Pose", "Port",
    "discover_catalogs", "load_catalog", "load_component",
    "ComponentArrangement",
    "has_collision", "collision_report",
]
