"""feature_control_frame_compose — atomic, read-only.

Compose an ISO 1101 / ASME Y14.5 Feature Control Frame (FCF) structure
ready for STEP / QIF / drawing export. An FCF is the three-cell row a
designer reads as:

    │ geometric_char │ tolerance_value [Ⓜ/Ⓛ/Ⓢ] │ datum_A [Ⓜ] │ datum_B │ datum_C │

Symbol legend (Unicode round-letter):
    Ⓜ  MMC
    Ⓛ  LMC
    Ⓢ  RFS
    Ⓟ  Projected tolerance zone
    Ⓕ  Free state
    ⊕   Position
    ⌀   Diameter tolerance zone

This skill *composes* the data structure — it does not verify the actual
geometry. Verification is done by the per-characteristic skills
(``gdt_flatness``, ``gdt_position``, ``runout_check``, ...).

Storage: appends to ``body._pd_fcf`` (list).

extras["fcf"] = the composed entry (also added to the list).

body unchanged (post ``body_present``).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


_GEOM_SYMBOLS: dict[str, str] = {
    "flatness":         "⏥",
    "perpendicularity": "⊥",
    "parallelism":      "∥",
    "position":         "⊕",
    "runout":           "↗",
    "concentricity":    "◎",
}

_MOD_SYMBOLS: dict[str, str] = {
    "MMC":       "Ⓜ",
    "LMC":       "Ⓛ",
    "RFS":       "Ⓢ",
    "PROJECTED": "Ⓟ",
    "FREE":      "Ⓕ",
    "DIAMETER":  "⌀",
    "TANGENT":   "Ⓣ",
}


_VALID_MODIFIERS = set(_MOD_SYMBOLS.keys())


@skill(
    name="feature_control_frame_compose",
    category="inspect",
    level="atomic",
    summary="Compose an ISO 1101 / ASME Y14.5 Feature Control Frame "
            "(geom_char + tolerance + modifiers + datum refs). Appended "
            "to body._pd_fcf and returned in extras['fcf']. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["feature_control_frame"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.02,
    post_conditions=[PostCondition(kind="body_present")],
)
class FeatureControlFrameCompose(SkillBase):
    class Args(BaseModel):
        geom_char: Literal[
            "flatness", "perpendicularity", "parallelism",
            "position", "runout", "concentricity",
        ]
        tolerance_value: float = Field(ge=0.0, le=100.0)
        modifiers: list[str] = Field(default_factory=list)
        datums: list[str] = Field(default_factory=list)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        # Validate modifier strings (skill-level guard — Pydantic Literal in
        # list[str] would over-constrain since some callers may pass empty).
        bad = [m for m in args.modifiers if m not in _VALID_MODIFIERS]
        if bad:
            raise ValueError(
                f"feature_control_frame_compose: unknown modifier(s) {bad}; "
                f"valid = {sorted(_VALID_MODIFIERS)}"
            )

        # Try to enrich datums from body._pd_datums (populated by
        # datum_target_assign / datum_plane_assign).
        try:
            datum_table = dict(getattr(body, "_pd_datums", {}) or {})
        except Exception:
            datum_table = {}
        datum_refs: list[dict[str, Any]] = []
        for d in args.datums:
            entry = datum_table.get(d)
            if entry is None:
                datum_refs.append({"label": d, "resolved": False})
            else:
                datum_refs.append({
                    "label": d,
                    "resolved": True,
                    "precedence": entry.get("precedence"),
                    "kind": entry.get("kind"),
                    "face_idx": entry.get("face_idx"),
                })

        geom_symbol = _GEOM_SYMBOLS.get(args.geom_char, "?")
        modifier_symbols = [_MOD_SYMBOLS[m] for m in args.modifiers]

        # Human-readable single-row textual FCF.
        cells: list[str] = [geom_symbol]
        tol_cell = f"{args.tolerance_value:g}"
        if "DIAMETER" in args.modifiers:
            tol_cell = "⌀" + tol_cell
        # Material-condition modifiers belong in the tolerance cell.
        for mod in ("MMC", "LMC", "RFS", "PROJECTED", "FREE", "TANGENT"):
            if mod in args.modifiers:
                tol_cell += _MOD_SYMBOLS[mod]
        cells.append(tol_cell)
        for d in args.datums:
            cells.append(d)
        text = " | ".join(cells)

        fcf = {
            "geom_char": args.geom_char,
            "geom_symbol": geom_symbol,
            "tolerance_value": float(args.tolerance_value),
            "modifiers": list(args.modifiers),
            "modifier_symbols": modifier_symbols,
            "datums": list(args.datums),
            "datum_refs": datum_refs,
            "text": text,
        }

        try:
            existing = list(getattr(body, "_pd_fcf", []) or [])
        except Exception:
            existing = []
        existing.append(fcf)
        try:
            body._pd_fcf = existing
        except Exception:
            pass

        extras = {
            "fcf": fcf,
            "index": len(existing) - 1,
            "total": len(existing),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
