"""OEM CAD 어셈블리 → Component catalog 자동 추출.

Phase 3 의 step_reader (XDE + naming) + classify_parts 활용.
각 부품의 bbox 측정 → Component yaml 생성. mount/clearance/ports 는 사람이 보강.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from phone_designer.components.model import (
    BoundingBox,
    Component,
    ComponentSource,
    Pose,
)


def _bbox_of_shape(shape) -> tuple[float, float, float, float, float, float]:
    """OCCT TopoDS_Shape → AddOptimal bbox (xmin, ymin, zmin, xmax, ymax, zmax)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bb)
    return bb.Get()


def shape_to_bbox(shape) -> tuple[BoundingBox, Pose]:
    """OCCT shape → BoundingBox + Pose (center).

    is_circular: 단순 휴리스틱 — X 와 Y 길이 차이가 작으면 원형 가정.
    """
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox_of_shape(shape)
    L = xmax - xmin
    W = ymax - ymin
    T = zmax - zmin
    is_circular = abs(L - W) < 0.05 * max(L, W)
    bbox = BoundingBox(
        length=round(L, 3),
        width=round(W, 3),
        thickness=round(T, 3),
        is_circular=is_circular,
    )
    pose = Pose(
        x_mm=round((xmin + xmax) / 2, 3),
        y_mm=round((ymin + ymax) / 2, 3),
        z_mm=round((zmin + zmax) / 2, 3),
    )
    return bbox, pose


def extract_components_from_step(
    step_path: Path | str,
    *,
    skip_names: set[str] | None = None,
) -> list[Component]:
    """OEM STEP → Component list.

    각 부품의 name + category (NAMING_RULES 매칭) + bbox + pose 자동.
    mount_interface / ports 는 None — 사람이 보강.
    """
    from phone_designer.reference.step_reader import (
        classify_parts,
        read_xde_step,
    )

    skip_names = skip_names or set()
    parts = read_xde_step(step_path, load_shapes=True)
    cat_map = classify_parts(parts)

    out: list[Component] = []
    for category, plist in cat_map.items():
        for p in plist:
            if p.name in skip_names:
                continue
            if p.shape is None:
                continue
            bbox, pose = shape_to_bbox(p.shape)
            comp = Component(
                name=p.name,
                category=category,
                bbox=bbox,
                pose=pose,
                source=ComponentSource.OEM_CAD,
                mount_interface=None,
                ports=[],
                raw_step_path=str(step_path),
                description=f"Auto-extracted from {Path(step_path).name}",
            )
            out.append(comp)
    return out


def save_extracted_to_catalog(
    components: list[Component],
    out_dir: Path | str,
) -> list[Path]:
    """추출된 Component 들을 yaml 로 저장. 카테고리 별 디렉토리."""
    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)
    written = []
    for c in components:
        cat_dir = base / c.category
        cat_dir.mkdir(exist_ok=True)
        # safe filename
        safe_name = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in c.name)
        out = cat_dir / f"{safe_name}.yaml"
        out.write_text(
            yaml.safe_dump(c.model_dump(mode="json", exclude_none=True),
                            sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        written.append(out)
    return written
