"""ProcessRegistry — catalogs/processes/*.yaml 자동 로드.

[[lat.md/manufacturing#처리-공정]] 구현.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ProcessDefinition:
    """1 공정 정의 (YAML 1개)."""
    code: str
    name: str
    description: str = ""
    rules: dict[str, Any] = field(default_factory=dict)
    applicable_to_skills: list[str] = field(default_factory=list)
    not_applicable_to: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> "ProcessDefinition":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            code=data["code"],
            name=data.get("name", data["code"]),
            description=data.get("description", ""),
            rules=data.get("rules", {}),
            applicable_to_skills=list(data.get("applicable_to_skills", [])),
            not_applicable_to=list(data.get("not_applicable_to", [])),
        )

    def is_applicable(self, skill_name: str) -> bool:
        if skill_name in self.not_applicable_to:
            return False
        if not self.applicable_to_skills:
            return True   # 빈 list 면 모두 허용
        return skill_name in self.applicable_to_skills


class ProcessRegistry:
    """모든 ProcessDefinition 의 catalog."""

    def __init__(self):
        self._defs: dict[str, ProcessDefinition] = {}
        self._loaded = False

    def load_from_dir(self, dir_path: Path | str) -> None:
        d = Path(dir_path)
        if not d.is_dir():
            return
        for f in sorted(d.glob("*.yaml")):
            try:
                proc = ProcessDefinition.from_yaml(f)
                self._defs[proc.code] = proc
            except Exception as exc:
                # log + skip
                print(f"[warn] process YAML 로드 실패 {f.name}: {exc}")
        self._loaded = True

    def ensure_loaded(self) -> None:
        """기본 catalogs/processes/ 로드 (idempotent)."""
        if not self._loaded:
            default_dir = Path(__file__).resolve().parents[3] / "catalogs" / "processes"
            self.load_from_dir(default_dir)

    def get(self, code: str) -> ProcessDefinition:
        self.ensure_loaded()
        if code not in self._defs:
            raise KeyError(f"unknown process: {code}. 가용: {list(self._defs.keys())}")
        return self._defs[code]

    def all(self) -> list[ProcessDefinition]:
        self.ensure_loaded()
        return list(self._defs.values())

    def codes(self) -> list[str]:
        self.ensure_loaded()
        return sorted(self._defs.keys())


# Singleton
registry = ProcessRegistry()
