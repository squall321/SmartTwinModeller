"""Manifest drift 게이트 — @skill 정의 전수 vs registry 등록 집합 (plan V1/P2).

CI (fidelity.yml) 가 직접 호출하는 파일. 핵심 전제: **export_manifest 단독
import 가 모든 skill 을 등록해야 한다** — 프로덕션 executor 가 그 경로만
타기 때문. 그래서 pkgutil.walk_packages 전체 임포트는 의도적으로 금지
(그게 바로 등록 누락을 가려온 ad-hoc 측정 경로다).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "src" / "phone_designer" / "skills"
PLANNER_PATH = SKILLS_ROOT / "reverse_engineer" / "plan_from_feature_catalog.py"


def _defined_skill_names() -> set[str]:
    """skills/**/*.py 를 AST 스캔 — @skill(name="...") 데코레이터의 name 전수 수집."""
    names: set[str] = set()
    for path in sorted(SKILLS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if func_name != "skill":
                    continue
                for kw in dec.keywords:
                    if (
                        kw.arg == "name"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ):
                        names.add(kw.value.value)
    return names


def _registered_skill_names() -> set[str]:
    """export_manifest import 의 side-effect 만으로 채워진 registry 집합."""
    import phone_designer.skills.export_manifest  # noqa: F401 — 등록 트리거

    from phone_designer.skills._registry import registry

    return {spec.name for spec in registry.all()}


def test_all_defined_skills_registered():
    defined = _defined_skill_names()
    registered = _registered_skill_names()
    assert defined, "AST 스캔이 @skill 정의를 하나도 못 찾음 — 스캔 경로/패턴 점검"
    missing = sorted(defined - registered)
    extra = sorted(registered - defined)
    assert defined == registered, (
        f"export_manifest 에 import 누락된 skill {len(missing)}개: {missing}\n"
        f"정의가 없는데 등록된 skill {len(extra)}개: {extra}"
    )


# _new_step(sid, "<skill_name>", {...}) — 두 번째 위치 인자가 skill name 리터럴.
# 첫 인자는 sid 변수 또는 "s_base" 류 리터럴 (콤마 없음). 변수로 넘기는 호출
# (_new_step(sid, skill_name, args)) 은 리터럴이 아니므로 매치되지 않는다.
_NEW_STEP_RE = re.compile(r'_new_step\(\s*[^,()]+,\s*"([A-Za-z0-9_]+)"')


def test_planner_emitted_skills_resolve():
    source = PLANNER_PATH.read_text(encoding="utf-8")
    emitted = set(_NEW_STEP_RE.findall(source))
    assert emitted, "planner 소스에서 _new_step skill 리터럴을 못 찾음 — regex 점검"

    registered = _registered_skill_names()
    unresolved = sorted(emitted - registered)
    assert not unresolved, (
        f"planner 가 emit 하지만 registry 에서 resolve 안 되는 skill: {unresolved}"
    )
