"""read_step_pmi — atomic, read-only.

Read PMI / GD&T back from a STEP AP242 file. This is the import-side mirror
of ``export_step_ap242_pmi`` and uses the same two-layer strategy:

  1. **CAF / XCAF route.** ``STEPCAFControl_Reader`` (GDT mode on) into a
     fresh ``TDocStd_Document``, then ``XCAFDoc_DimTolTool`` enumerates the
     dimension / geometric-tolerance / datum labels. For every label the
     attached ``XCAFDimTolObjects_*`` object yields a readable type string,
     value(s) in mm, datum reference letters and the referenced shape label
     entries.
  2. **JSON sidecar.** If ``<path>.pmi.json`` (the sidecar written by
     ``export_step_ap242_pmi``) exists next to the STEP file it is parsed
     verbatim into ``extras['pmi']['sidecar']``.

extras schema::

    extras["pmi"] = {
        "dimensions": [{type, value_mm, values_mm, is_plus_minus,
                        upper_tol_mm, lower_tol_mm, is_range,
                        datum_refs, shape_ref_labels, label}],
        "geometric_tolerances": [{type, value_mm, datum_refs,
                                  shape_ref_labels, label}],
        "datums": [{name, label, precedence_position?, is_datum_target?}],
        "counts": {dimensions, geometric_tolerances, datums},
        "source_path": str,
        "sidecar_path": str | None,
        "sidecar": dict | None,
        "notes": [str, ...],
    }

Binding notes (cadquery-ocp / OCCT 7.8 — verified empirically):

* ``XCAFDoc_DatumTool`` is **not** exposed by this OCP build; datums are
  enumerated via ``XCAFDoc_DimTolTool.GetDatumLabels`` instead (this is
  also where OCCT itself keeps them — there is no separate datum tool in
  upstream OCCT either).
* ``TDF_Label.FindAttribute(guid, attr)`` cannot re-seat the attribute
  handle through pybind11, so attributes are fetched with
  ``XCAFDoc_Dimension.Set_s(label)`` & co. — on labels returned by
  ``Get*Labels`` the attribute already exists, so ``Set_s`` returns the
  existing attribute without creating anything (and the document is local
  to this skill anyway).
* Plus/minus bounds read back as magnitudes: a dimension exported with
  lower_tol = -0.05 reads back ``lower_tol_mm == 0.05``.

In-house round trip: PMI-EXPORT-FIX (2026-06-11) — ``export_step_ap242_pmi``
now writes real ``XCAFDimTolObjects`` payloads with shape attachment and
forces the AP242 schema after controller init, so in-house exports
round-trip tolerance types + values + datum letters through the CAF route
(verified by ``tests/skills/test_read_step_pmi.py``), as do semantically
written external files (NX / Creo). A PMI-less STEP body next to a
``.pmi.json`` sidecar can still occur (e.g. the exporter's plain-write
fallback, or sidecar-only categories like welds); the annotation table is
then recovered from the sidecar and an honest note is emitted.

body unchanged — ``body_present`` post-condition (reads a FILE only).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _label_entry(label: Any) -> str:
    """TDF label → OCAF entry string like '0:1:4:2'."""
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDF import TDF_Tool

    s = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, s)
    return s.ToCString()


def _enum_readable(enum_val: Any) -> str:
    """'XCAFDimTolObjects_GeomToleranceType_Flatness' → 'flatness'."""
    name = getattr(enum_val, "name", None) or str(enum_val)
    if "Type_" in name:
        name = name.split("Type_", 1)[1]
    return name.lower()


def _label_sequence_entries(seq: Any) -> list[str]:
    out: list[str] = []
    for i in range(1, seq.Length() + 1):
        try:
            out.append(_label_entry(seq.Value(i)))
        except Exception:
            continue
    return out


def _shape_ref_labels(lab: Any) -> dict[str, list[str]]:
    """Shape labels referenced by a GD&T label (first / second sequences)."""
    from OCP.TDF import TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_DimTolTool

    first = TDF_LabelSequence()
    second = TDF_LabelSequence()
    try:
        XCAFDoc_DimTolTool.GetRefShapeLabel_s(lab, first, second)
    except Exception:
        pass
    return {
        "first": _label_sequence_entries(first),
        "second": _label_sequence_entries(second),
    }


def _datum_name(datum_label: Any) -> str | None:
    from OCP.XCAFDoc import XCAFDoc_Datum

    try:
        attr = XCAFDoc_Datum.Set_s(datum_label)  # existing attribute returned
        obj = attr.GetObject()
        nm = obj.GetName()
        return nm.ToCString() if nm is not None else None
    except Exception:
        return None


def _datum_refs_of(lab: Any) -> list[str]:
    """Datum reference letters attached to a tolerance label (precedence
    order as stored). Returns [] for labels without datum references."""
    from OCP.TDF import TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_DimTolTool

    refs = TDF_LabelSequence()
    try:
        XCAFDoc_DimTolTool.GetDatumOfTolerLabels_s(lab, refs)
    except Exception:
        return []
    out: list[str] = []
    for i in range(1, refs.Length() + 1):
        name = _datum_name(refs.Value(i))
        if name is not None:
            out.append(name)
    return out


def _dimension_payload(lab: Any) -> dict[str, Any]:
    from OCP.XCAFDoc import XCAFDoc_Dimension

    rec: dict[str, Any] = {
        "type": None,
        "value_mm": None,
        "values_mm": [],
        "is_plus_minus": False,
        "upper_tol_mm": None,
        "lower_tol_mm": None,
        "is_range": False,
        "datum_refs": _datum_refs_of(lab),
        "shape_ref_labels": _shape_ref_labels(lab),
        "label": _label_entry(lab),
    }
    try:
        obj = XCAFDoc_Dimension.Set_s(lab).GetObject()
    except Exception as exc:
        rec["error"] = f"dimension object unavailable: {exc}"
        return rec

    try:
        rec["type"] = _enum_readable(obj.GetType())
    except Exception:
        pass
    # Values — GetValues() is the array; GetValue() is the first entry.
    vals: list[float] = []
    try:
        arr = obj.GetValues()
        if arr is not None:
            for k in range(arr.Lower(), arr.Upper() + 1):
                vals.append(float(arr.Value(k)))
    except Exception:
        pass
    if not vals:
        try:
            vals = [float(obj.GetValue())]
        except Exception:
            vals = []
    rec["values_mm"] = vals
    rec["value_mm"] = vals[0] if vals else None
    try:
        if obj.IsDimWithPlusMinusTolerance():
            rec["is_plus_minus"] = True
            rec["upper_tol_mm"] = float(obj.GetUpperTolValue())
            rec["lower_tol_mm"] = float(obj.GetLowerTolValue())
    except Exception:
        pass
    try:
        if obj.IsDimWithRange():
            rec["is_range"] = True
            rec["upper_bound_mm"] = float(obj.GetUpperBound())
            rec["lower_bound_mm"] = float(obj.GetLowerBound())
    except Exception:
        pass
    return rec


def _geom_tolerance_payload(lab: Any) -> dict[str, Any]:
    from OCP.XCAFDoc import XCAFDoc_GeomTolerance

    rec: dict[str, Any] = {
        "type": None,
        "value_mm": None,
        "datum_refs": _datum_refs_of(lab),
        "shape_ref_labels": _shape_ref_labels(lab),
        "label": _label_entry(lab),
    }
    try:
        obj = XCAFDoc_GeomTolerance.Set_s(lab).GetObject()
    except Exception as exc:
        rec["error"] = f"geom tolerance object unavailable: {exc}"
        return rec
    try:
        rec["type"] = _enum_readable(obj.GetType())
    except Exception:
        pass
    try:
        rec["value_mm"] = float(obj.GetValue())
    except Exception:
        pass
    try:
        rec["value_kind"] = _enum_readable(obj.GetTypeOfValue())
    except Exception:
        pass
    return rec


def _datum_payload(lab: Any) -> dict[str, Any]:
    from OCP.XCAFDoc import XCAFDoc_Datum

    rec: dict[str, Any] = {
        "name": None,
        "label": _label_entry(lab),
    }
    try:
        obj = XCAFDoc_Datum.Set_s(lab).GetObject()
    except Exception as exc:
        rec["error"] = f"datum object unavailable: {exc}"
        return rec
    try:
        nm = obj.GetName()
        rec["name"] = nm.ToCString() if nm is not None else None
    except Exception:
        pass
    try:
        rec["precedence_position"] = int(obj.GetPosition())
    except Exception:
        pass
    try:
        rec["is_datum_target"] = bool(obj.IsDatumTarget())
    except Exception:
        pass
    return rec


def _read_caf(path: Path) -> dict[str, Any]:
    """STEPCAFControl_Reader route — returns the enumerated PMI tables."""
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)

    reader = STEPCAFControl_Reader()
    try:
        reader.SetGDTMode(True)
    except Exception:
        pass
    status = reader.ReadFile(str(path))
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(
            f"read_step_pmi: STEPCAF ReadFile failed (status={status}) -> {path}"
        )
    if not reader.Transfer(doc):
        raise RuntimeError(
            f"read_step_pmi: STEPCAF Transfer returned False -> {path}"
        )

    dimtol = XCAFDoc_DocumentTool.DimTolTool_s(doc.Main())

    dim_labels = TDF_LabelSequence()
    dimtol.GetDimensionLabels(dim_labels)
    gt_labels = TDF_LabelSequence()
    dimtol.GetGeomToleranceLabels(gt_labels)
    datum_labels = TDF_LabelSequence()
    dimtol.GetDatumLabels(datum_labels)

    dimensions = [
        _dimension_payload(dim_labels.Value(i))
        for i in range(1, dim_labels.Length() + 1)
    ]
    geom_tolerances = [
        _geom_tolerance_payload(gt_labels.Value(i))
        for i in range(1, gt_labels.Length() + 1)
    ]
    datums = [
        _datum_payload(datum_labels.Value(i))
        for i in range(1, datum_labels.Length() + 1)
    ]
    return {
        "dimensions": dimensions,
        "geometric_tolerances": geom_tolerances,
        "datums": datums,
    }


def _read_sidecar(path: Path) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Returns (payload, sidecar_path, error_note). Same naming convention
    as the export skill: ``<path>.pmi.json``."""
    sidecar_path = path.with_suffix(path.suffix + ".pmi.json")
    if not sidecar_path.exists():
        return None, None, None
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        return payload, str(sidecar_path), None
    except Exception as exc:
        return None, str(sidecar_path), f"sidecar present but unparseable: {exc}"


@skill(
    name="read_step_pmi",
    category="pmi",
    level="atomic",
    summary="Read PMI / GD&T from a STEP AP242 file via STEPCAFControl_Reader "
            "+ XCAFDoc_DimTolTool (dimensions, geometric tolerances with type/"
            "value/datum letters, datums) and the <path>.pmi.json sidecar when "
            "present. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["step_pmi_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.step_read_failed"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class ReadStepPmi(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1, description="input .step / .stp path")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError(
                "read_step_pmi: body is None (skill is read-only and passes "
                "the body through unchanged)"
            )
        in_path = Path(args.path)
        if not in_path.exists():
            raise ValueError(f"read_step_pmi: file not found -> {in_path}")

        notes: list[str] = [
            "XCAFDoc_DatumTool is not exposed by this OCP build; datums "
            "enumerated via XCAFDoc_DimTolTool.GetDatumLabels().",
        ]

        caf = _read_caf(in_path)
        sidecar, sidecar_path, sidecar_err = _read_sidecar(in_path)
        if sidecar_err:
            notes.append(sidecar_err)

        counts = {
            "dimensions": len(caf["dimensions"]),
            "geometric_tolerances": len(caf["geometric_tolerances"]),
            "datums": len(caf["datums"]),
        }
        if sum(counts.values()) == 0 and sidecar is not None:
            # PMI-EXPORT-FIX (2026-06-11): wording updated — the in-house
            # exporter now writes real GDT entities; this branch covers its
            # plain-write fallback and sidecar-only annotation categories.
            notes.append(
                "STEP body carries no XCAF GDT entities; PMI recovered "
                "from the .pmi.json sidecar instead."
            )

        extras = {
            "pmi": {
                "dimensions": caf["dimensions"],
                "geometric_tolerances": caf["geometric_tolerances"],
                "datums": caf["datums"],
                "counts": counts,
                "source_path": str(in_path),
                "sidecar_path": sidecar_path,
                "sidecar": sidecar,
                "notes": notes,
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
