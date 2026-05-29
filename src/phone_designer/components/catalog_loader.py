"""catalogs/components/**/*.yaml 자동 로드."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from phone_designer.components.model import Component, ComponentSource


def _default_catalog_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "catalogs" / "components"


def load_component(path: Path | str) -> Component:
    """1 yaml 파일 → Component."""
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return Component.model_validate(data)


def load_catalog(dir_path: Path | str | None = None) -> list[Component]:
    """디렉토리의 모든 yaml 로드. 재귀."""
    d = Path(dir_path) if dir_path else _default_catalog_dir()
    if not d.is_dir():
        return []
    out: list[Component] = []
    for f in sorted(d.rglob("*.yaml")):
        if f.name.startswith("_") or f.name == "README.md":
            continue
        try:
            out.append(load_component(f))
        except Exception as exc:
            print(f"[warn] component yaml 로드 실패 {f.name}: {exc}")
    return out


def discover_catalogs() -> dict[str, list[Component]]:
    """카테고리 별로 Component 분류."""
    out: dict[str, list[Component]] = {}
    for c in load_catalog():
        out.setdefault(c.category, []).append(c)
    return out
