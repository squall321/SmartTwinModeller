"""STEP XDE reader — 어셈블리 트리 + 부품 네이밍 + shape 추출.

[[lat.md/reference#step-어셈블리-분석]] 의 framework 화. runner.py 의 인라인 코드를
정식 모듈로 이전.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class PartInfo:
    """XDE STEP 의 1 부품."""
    index: int                   # GetFreeShapes 의 1-based index
    name: str                    # XDE label 의 TDataStd_Name (or "<unnamed>")
    shape: object | None = None  # TopoDS_Shape (있으면)


# 부품 네이밍 → category 매핑. [[lat.md/reference#부품-분류]] 참조.
NAMING_RULES: list[tuple[str, str]] = [
    (r".*[Dd]isplay.*|.*[Pp]anel.*|.*OLED.*|.*LCD.*",    "display"),
    (r".*[Bb]atter.*",                                    "battery"),
    (r".*[Mm]ain.*[Bb]oard.*|.*PCB.*|.*[Ss]o[Cc].*",     "mainboard"),
    (r".*[Cc]rown.*",                                     "crown"),
    (r".*[Bb]utton.*|.*[Kk]ey.*",                        "button"),
    (r".*[Hh]ousing.*|.*[Cc]ase.*|.*[Ff]rame.*",         "housing"),
    (r".*[Cc]oil.*|.*[Cc]harg.*",                        "wireless_coil"),
    (r".*[Vv]ibr.*|.*[Hh]aptic.*",                       "haptic"),
    (r".*[Aa]ntenna.*",                                   "antenna"),
    (r".*[Ss]peaker.*",                                   "speaker"),
    (r".*[Mm]ic.*",                                       "mic"),
    (r".*[Ss]ensor.*|.*[Hh]eart.*|.*PPG.*",              "sensor"),
    (r".*[Ll]ug.*|.*[Ss]trap.*",                         "lug"),
]


def read_xde_step(path: Path | str, *, load_shapes: bool = True) -> list[PartInfo]:
    """STEP XDE 어셈블리에서 free-shape (top-level) 부품 list 반환.

    Args:
        path: STEP 파일 경로
        load_shapes: True 면 PartInfo.shape 에 TopoDS_Shape 도 채움 (mesh 변환 등 후속).
                      False 면 metadata 만 (빠름).

    Returns:
        list[PartInfo] — index/name/(shape)
    """
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDataStd import TDataStd_Name

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)

    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetLayerMode(True)

    doc = TDocStd_Document(TCollection_ExtendedString("doc"))
    reader.ReadFile(str(p))
    reader.Transfer(doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(labels)

    name_guid = TDataStd_Name.GetID_s()
    parts: list[PartInfo] = []
    for i in range(1, labels.Length() + 1):
        label = labels.Value(i)
        name = "<unnamed>"
        name_attr = TDataStd_Name()
        if label.FindAttribute(name_guid, name_attr):
            ext = name_attr.Get()
            name = ext.ToExtString() if hasattr(ext, "ToExtString") else str(ext)

        shape = None
        if load_shapes:
            shape = shape_tool.GetShape_s(label)

        parts.append(PartInfo(index=i, name=name, shape=shape))

    return parts


def classify_parts(
    parts: Iterable[PartInfo],
    *,
    rules: list[tuple[str, str]] | None = None,
) -> dict[str, list[PartInfo]]:
    """부품 네이밍 → category 매핑.

    매칭 실패 시 `unknown` 카테고리. 사용자가 UI 에서 수동 재분류 가능.
    """
    rules = rules or NAMING_RULES
    out: dict[str, list[PartInfo]] = {}
    for part in parts:
        matched = None
        for pattern, cat in rules:
            if re.match(pattern, part.name):
                matched = cat
                break
        category = matched or "unknown"
        out.setdefault(category, []).append(part)
    return out
