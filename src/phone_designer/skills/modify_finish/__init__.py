"""Modify / finish skills."""
from phone_designer.skills.modify_finish.apply_anodize import (
    ApplyAnodize,
    get_anodize_tags,
    set_anodize_tags,
)
from phone_designer.skills.modify_finish.apply_paint import (
    ApplyPaint,
    get_paint_tags,
    set_paint_tags,
)
from phone_designer.skills.modify_finish.apply_plating import (
    ApplyPlating,
    get_plating_tags,
    set_plating_tags,
)
from phone_designer.skills.modify_finish.apply_texture_region import (
    ApplyTextureRegion,
    get_texture_tags,
    set_texture_tags,
)
from phone_designer.skills.modify_finish.deburring import Deburring
from phone_designer.skills.modify_finish.final_fillet import FinalFilletAllSharpEdges
from phone_designer.skills.modify_finish.sanding_pass import SandingPass
from phone_designer.skills.modify_finish.surface_finish_tag import (
    SurfaceFinishTag,
    get_finish_tags,
    set_finish_tags,
)

__all__ = [
    "ApplyAnodize",
    "ApplyPaint",
    "ApplyPlating",
    "ApplyTextureRegion",
    "Deburring",
    "FinalFilletAllSharpEdges",
    "SandingPass",
    "SurfaceFinishTag",
    "get_anodize_tags",
    "set_anodize_tags",
    "get_paint_tags",
    "set_paint_tags",
    "get_plating_tags",
    "set_plating_tags",
    "get_texture_tags",
    "set_texture_tags",
    "get_finish_tags",
    "set_finish_tags",
]
