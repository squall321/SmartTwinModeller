"""Skill-coverage ratchet.

Walks every ``@skill(name=...)`` registration under
``src/phone_designer/skills/`` and asserts the name appears somewhere in
the tests tree (any .py/.yaml/.yml/.json/.txt file under ``tests/``,
excluding this file). New skills therefore MUST land with at least one
test that references them by name — module-path imports count, since the
module file name matches the skill name by convention.

UNTESTED_ALLOWLIST is the frozen remainder at ratchet time. It may only
SHRINK: ``test_allowlist_may_only_shrink`` fails when an entry either no
longer exists as a skill (delete the entry) or has gained a test
reference (delete the entry — never add new ones).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO / "src" / "phone_designer" / "skills"
TESTS_DIR = REPO / "tests"
THIS_FILE = Path(__file__).resolve()

_SKILL_CALL_RE = re.compile(r"@skill\s*\(")
_NAME_RE = re.compile(r"name\s*=\s*[\"']([A-Za-z0-9_.]+)[\"']")
_CORPUS_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".txt"}

# Skills with no test reference when the ratchet was introduced
# (2026-06-10, plan item V6). This list may only SHRINK — write a test,
# then remove the entry. NEVER add to it.
UNTESTED_ALLOWLIST = (
    "display_bezel_step_with_adhesive_groove",
    "fill_small_holes",
    "lip_seal_cavity",
    "mesh_decimate",
    "retaining_ring_groove",
)


def _registered_skill_names() -> dict[str, str]:
    """{skill_name: defining file} for every @skill(name=...) in src."""
    names: dict[str, str] = {}
    for py in sorted(SKILLS_DIR.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        for m in _SKILL_CALL_RE.finditer(text):
            # name= is conventionally the first decorator kwarg; a 400-char
            # window after "@skill(" is generous for every current skill.
            m2 = _NAME_RE.search(text, m.end(), m.end() + 400)
            if m2:
                names[m2.group(1)] = str(py.relative_to(REPO))
    return names


def _test_corpus() -> list[str]:
    """Contents of every test-tree file that could reference a skill name.

    This ratchet file itself is EXCLUDED — otherwise the allowlist entries
    above would count as 'referenced in tests' and the shrink check could
    never fire.
    """
    texts: list[str] = []
    for p in sorted(TESTS_DIR.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.resolve() == THIS_FILE:
            continue
        if p.suffix.lower() not in _CORPUS_SUFFIXES:
            continue
        try:
            texts.append(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return texts


def test_scanner_finds_a_plausible_skill_population():
    names = _registered_skill_names()
    # 354 registered skills at ratchet time — guard the scanner itself so
    # a silent regex/path break can't vacuously pass the coverage test.
    assert len(names) >= 300, (
        f"@skill scanner found only {len(names)} names — regex or path broke"
    )
    for known in ("feature_fidelity_diff", "classify_holes", "box"):
        assert known in names


def test_every_skill_name_is_referenced_somewhere_in_tests():
    names = _registered_skill_names()
    corpus = _test_corpus()
    assert corpus, "test-corpus glob returned nothing — path broke"

    missing = sorted(
        name
        for name in names
        if name not in UNTESTED_ALLOWLIST
        and not any(name in text for text in corpus)
    )
    assert not missing, (
        "skills with NO reference anywhere under tests/ (write a test; do "
        "NOT extend UNTESTED_ALLOWLIST):\n"
        + "\n".join(f"    {n}  ({names[n]})" for n in missing)
    )


def test_allowlist_may_only_shrink():
    names = _registered_skill_names()
    corpus = _test_corpus()

    # Sorted + duplicate-free keeps diffs reviewable.
    assert list(UNTESTED_ALLOWLIST) == sorted(set(UNTESTED_ALLOWLIST)), (
        "UNTESTED_ALLOWLIST must stay sorted and duplicate-free"
    )

    ghosts = sorted(n for n in UNTESTED_ALLOWLIST if n not in names)
    assert not ghosts, (
        f"allowlist entries that are no longer registered skills — "
        f"remove them: {ghosts}"
    )

    now_tested = sorted(
        n for n in UNTESTED_ALLOWLIST if any(n in text for text in corpus)
    )
    assert not now_tested, (
        f"allowlist entries now referenced under tests/ — the ratchet only "
        f"shrinks, remove them from UNTESTED_ALLOWLIST: {now_tested}"
    )
