"""export_step_ap242_pmi — atomic, read-only.

Write the current body to a STEP AP242 file, packaging the PMI metadata
attached by the rest of the ``pmi/`` skills (dimensions, surface textures,
welds, FCFs, datums).

Two-layer strategy:

  1. **CAF / XCAF route (preferred).** Build a TDocStd document, add the
     shape with ``XCAFDoc_ShapeTool``, then translate every in-memory PMI
     tag into a real ``XCAFDimTolObjects_*`` payload attached to shapes:

     * ``body._pd_pmi_dimensions`` → ``XCAFDimTolObjects_DimensionObject``
       (type from kind, nominal value, ±tolerances) linked via
       ``XCAFDoc_DimTolTool.SetDimension`` to the faces its stored
       selectors resolve to (whole-shape label as last-resort anchor).
     * ``body._pd_datums`` → ``XCAFDimTolObjects_DatumObject`` (letter +
       precedence) linked via ``SetDatum`` to the recorded faces.
     * ``body._pd_fcf`` → ``XCAFDimTolObjects_GeomToleranceObject``
       (characteristic type, value, MMC/LMC/⌀-zone/projected modifiers)
       linked via ``SetGeomTolerance``; datum letters wired with
       ``SetDatumToGeomTol`` in FCF precedence order. FCFs record no
       toleranced-feature selector, so they attach to the whole-shape
       label (verified to survive the STEP round trip).

     The AP242 schema is forced *after* ``STEPControl_Controller.Init_s``
     (setting ``write.step.schema`` before the controller exists is a
     silent no-op) and restored afterwards — ``Interface_Static`` is
     process-global state.
  2. **JSON sidecar (always).** A companion file ``<path>.pmi.json``
     captures the full annotation table verbatim so a downstream
     consumer (drawing automation, QIF translator, LLM agent) can still
     reconstruct intent for the parts AP242 cannot carry.

Sidecar-only data (no XCAF / AP242 semantic equivalent — recorded honestly
in ``extras['caf_stats']['sidecar_only']``):

  * surface textures (``_pd_surface_texture``) — no XCAF GDT class in
    this OCP build;
  * weld symbols (``_pd_welds``) — AP242 has no weld-symbol PMI;
  * basic dimensions (``_pd_basic_dimensions``) — annotation table only;
  * per-dimension ``datum_refs`` — AP242 dimensions carry no datum system
    (verified: a datum linked to a dimension label does not survive the
    STEP body); FCF datum references DO survive.

Angular dimensions are written with the stored value as-is (degrees).

Both routes always run; failures in step 1 are swallowed so the export
still succeeds (plain STEP fallback). The returned ``extras`` summarise
what got written, including per-route emission counts.

body unchanged — ``body_present`` post-condition.
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


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


# PMI-EXPORT-FIX (2026-06-11): the old _configure_ap242 called
# Interface_Static.SetCVal_s BEFORE the STEP controller was initialised,
# which is a silent no-op — the files it wrote were never AP242 and carried
# zero GDT entities. The controller must be initialised first; the previous
# schema is returned so the caller can restore the process-global static.
def _init_ap242_schema() -> str | None:
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_Controller

    STEPControl_Controller.Init_s()  # idempotent
    prev = Interface_Static.CVal_s("write.step.schema")
    if not Interface_Static.SetCVal_s("write.step.schema", "AP242DIS"):
        raise RuntimeError(
            "export_step_ap242_pmi: failed to force write.step.schema=AP242DIS"
        )
    return prev


def _restore_schema(prev: str | None) -> None:
    if not prev:
        return
    try:
        from OCP.Interface import Interface_Static

        Interface_Static.SetCVal_s("write.step.schema", prev)
    except Exception:
        pass


def _gather_pmi(body: Any) -> dict[str, Any]:
    """Read every ``_pd_pmi_*`` / ``_pd_welds`` / ``_pd_datums`` / ``_pd_fcf``
    / ``_pd_surface_texture`` tag from the body."""
    pmi: dict[str, Any] = {}
    pmi["dimensions"] = list(getattr(body, "_pd_pmi_dimensions", []) or [])
    pmi["surface_textures"] = list(getattr(body, "_pd_surface_texture", []) or [])
    pmi["welds"] = list(getattr(body, "_pd_welds", []) or [])
    pmi["fcfs"] = list(getattr(body, "_pd_fcf", []) or [])
    datums = getattr(body, "_pd_datums", {}) or {}
    try:
        pmi["datums"] = dict(datums)
    except Exception:
        pmi["datums"] = {}
    # Basic dimensions (from inspect.basic_dimension_attach) — best effort.
    pmi["basic_dimensions"] = list(getattr(body, "_pd_basic_dimensions", []) or [])
    return pmi


# ---------------------------------------------------------------------------
# PMI-EXPORT-FIX (2026-06-11): in-memory tag → XCAFDimTolObjects translation.
# Recipe proven by tests/skills/test_read_step_pmi.py (the genuine-AP242
# fixture): payload object on the label via Set_s(label).SetObject(obj),
# then shape linkage via SetDimension / SetDatum / SetGeomTolerance.
# ---------------------------------------------------------------------------

def _resolve_selector_faces(shape: Any, sel_dict: Any, body: Any) -> list[Any]:
    """Re-resolve a stored selector dict to TopoDS faces. [] on any failure."""
    if not sel_dict:
        return []
    try:
        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills._selectors import selector_from_dict

        sel = selector_from_dict(sel_dict)
        try:
            return list(resolve_faces(shape, sel, body=body) or [])
        except TypeError:  # older resolve_faces signature without body kwarg
            return list(resolve_faces(shape, sel) or [])
    except Exception:
        return []


def _sub_shape_label(shape_tool: Any, main_label: Any, face: Any,
                     cache: list[tuple[Any, Any]]) -> Any | None:
    """Face → sub-shape label under the main shape label (cached, null-safe)."""
    for f, lab in cache:
        try:
            if f.IsSame(face):
                return lab
        except Exception:
            continue
    try:
        lab = shape_tool.AddSubShape(main_label, face)
    except Exception:
        return None
    if lab.IsNull():
        return None
    cache.append((face, lab))
    return lab


def _label_seq(labels: list[Any]):
    from OCP.TDF import TDF_LabelSequence

    seq = TDF_LabelSequence()
    for lab in labels:
        seq.Append(lab)
    return seq


def _emit_dimensions(dt: Any, st: Any, main_label: Any, shape: Any, body: Any,
                     dims: list[dict], cache: list, stats: dict,
                     notes: list[str]) -> None:
    from OCP.XCAFDimTolObjects import (
        XCAFDimTolObjects_DimensionObject,
        XCAFDimTolObjects_DimensionType_Location_Angular,
        XCAFDimTolObjects_DimensionType_Location_LinearDistance,
        XCAFDimTolObjects_DimensionType_Size_Diameter,
        XCAFDimTolObjects_DimensionType_Size_Radius,
    )
    from OCP.XCAFDoc import XCAFDoc_Dimension

    type_by_kind = {
        "linear": XCAFDimTolObjects_DimensionType_Location_LinearDistance,
        "angular": XCAFDimTolObjects_DimensionType_Location_Angular,
        "diameter": XCAFDimTolObjects_DimensionType_Size_Diameter,
        "radius": XCAFDimTolObjects_DimensionType_Size_Radius,
    }

    for entry in dims:
        kind = entry.get("kind", "linear")
        dim_type = type_by_kind.get(kind)
        if dim_type is None:
            notes.append(
                f"dimension kind '{kind}' has no XCAF dimension type — "
                f"sidecar only"
            )
            continue
        faces_a = _resolve_selector_faces(shape, entry.get("entity_a"), body)
        faces_b = _resolve_selector_faces(shape, entry.get("entity_b"), body)
        labels_a = [
            lab for f in faces_a
            if (lab := _sub_shape_label(st, main_label, f, cache)) is not None
        ]
        labels_b = [
            lab for f in faces_b
            if (lab := _sub_shape_label(st, main_label, f, cache)) is not None
        ]

        dim_l = dt.AddDimension()
        obj = XCAFDimTolObjects_DimensionObject()
        obj.SetType(dim_type)
        obj.SetValue(float(entry.get("nominal", 0.0)))
        upper = float(entry.get("upper_tol", 0.0) or 0.0)
        lower = float(entry.get("lower_tol", 0.0) or 0.0)
        if upper or lower:
            obj.SetUpperTolValue(upper)
            obj.SetLowerTolValue(lower)
        XCAFDoc_Dimension.Set_s(dim_l).SetObject(obj)

        if labels_a and labels_b:
            dt.SetDimension(_label_seq(labels_a), _label_seq(labels_b), dim_l)
        elif labels_a:
            try:
                dt.SetDimension(_label_seq(labels_a), _label_seq([]), dim_l)
            except Exception:
                dt.SetDimension(labels_a[0], dim_l)
        else:
            # Selector no longer resolves — anchor to the whole shape so the
            # value still survives in the STEP body.
            dt.SetDimension(main_label, dim_l)
            notes.append(
                f"dimension ({kind}, nominal={entry.get('nominal')}) selector "
                f"resolved no faces — attached to whole-shape label"
            )
        stats["dimensions_emitted"] += 1

    if any(e.get("datum_refs") for e in dims):
        notes.append(
            "per-dimension datum_refs have no AP242 representation "
            "(datum systems belong to tolerances) — preserved in the "
            ".pmi.json sidecar only"
        )


def _emit_datums(dt: Any, st: Any, main_label: Any, shape: Any,
                 datum_table: dict[str, dict], cache: list, stats: dict,
                 notes: list[str]) -> dict[str, Any]:
    """Datum table → XCAF datum labels with face linkage. Returns letter→label."""
    from OCP.TCollection import TCollection_HAsciiString
    from OCP.XCAFDimTolObjects import XCAFDimTolObjects_DatumObject
    from OCP.XCAFDoc import XCAFDoc_Datum

    all_faces: list[Any] = []
    try:
        from phone_designer.skills._resolvers import _all_faces

        all_faces = list(_all_faces(shape))
    except Exception:
        all_faces = []

    created: dict[str, Any] = {}
    ordered = sorted(
        datum_table.items(),
        key=lambda kv: (int(kv[1].get("precedence") or 99), kv[0]),
    )
    for letter, entry in ordered:
        da_l = dt.AddDatum(
            TCollection_HAsciiString(str(letter)),
            TCollection_HAsciiString(""),
            TCollection_HAsciiString(""),
        )
        obj = XCAFDimTolObjects_DatumObject()
        obj.SetName(TCollection_HAsciiString(str(letter)))
        try:
            obj.SetPosition(int(entry.get("precedence") or 1))
        except Exception:
            pass
        XCAFDoc_Datum.Set_s(da_l).SetObject(obj)

        idxs = list(entry.get("face_indices") or [])
        if not idxs and int(entry.get("face_idx", -1)) >= 0:
            idxs = [int(entry["face_idx"])]
        labels = [
            lab for i in idxs if 0 <= int(i) < len(all_faces)
            if (lab := _sub_shape_label(st, main_label, all_faces[int(i)], cache))
            is not None
        ]
        if labels:
            dt.SetDatum(_label_seq(labels), da_l)
        else:
            dt.SetDatum(_label_seq([main_label]), da_l)
            notes.append(
                f"datum '{letter}' face indices unresolvable — attached to "
                f"whole-shape label"
            )
        created[letter] = da_l
        stats["datums_emitted"] += 1
    return created


def _emit_fcfs(dt: Any, main_label: Any, fcfs: list[dict],
               datum_labels: dict[str, Any], stats: dict,
               notes: list[str]) -> None:
    from OCP.XCAFDimTolObjects import (
        XCAFDimTolObjects_GeomToleranceMatReqModif_L,
        XCAFDimTolObjects_GeomToleranceMatReqModif_M,
        XCAFDimTolObjects_GeomToleranceObject,
        XCAFDimTolObjects_GeomToleranceType_CircularRunout,
        XCAFDimTolObjects_GeomToleranceType_Concentricity,
        XCAFDimTolObjects_GeomToleranceType_Flatness,
        XCAFDimTolObjects_GeomToleranceType_Parallelism,
        XCAFDimTolObjects_GeomToleranceType_Perpendicularity,
        XCAFDimTolObjects_GeomToleranceType_Position,
        XCAFDimTolObjects_GeomToleranceTypeValue_Diameter,
    )
    from OCP.XCAFDoc import XCAFDoc_GeomTolerance

    type_by_char = {
        "flatness": XCAFDimTolObjects_GeomToleranceType_Flatness,
        "perpendicularity": XCAFDimTolObjects_GeomToleranceType_Perpendicularity,
        "parallelism": XCAFDimTolObjects_GeomToleranceType_Parallelism,
        "position": XCAFDimTolObjects_GeomToleranceType_Position,
        # FCF symbol "↗" is circular (not total) runout.
        "runout": XCAFDimTolObjects_GeomToleranceType_CircularRunout,
        "concentricity": XCAFDimTolObjects_GeomToleranceType_Concentricity,
    }

    attached_any = False
    for fcf in fcfs:
        gchar = fcf.get("geom_char")
        tol_type = type_by_char.get(gchar)
        if tol_type is None:
            notes.append(
                f"fcf geom_char '{gchar}' has no XCAF tolerance type — "
                f"sidecar only"
            )
            continue
        tol_l = dt.AddGeomTolerance()
        obj = XCAFDimTolObjects_GeomToleranceObject()
        obj.SetType(tol_type)
        obj.SetValue(float(fcf.get("tolerance_value", 0.0)))

        mods = list(fcf.get("modifiers") or [])
        if "MMC" in mods:
            obj.SetMaterialRequirementModifier(
                XCAFDimTolObjects_GeomToleranceMatReqModif_M)
        elif "LMC" in mods:
            obj.SetMaterialRequirementModifier(
                XCAFDimTolObjects_GeomToleranceMatReqModif_L)
        # RFS is the default material condition — nothing to set.
        if "DIAMETER" in mods:
            obj.SetTypeOfValue(XCAFDimTolObjects_GeomToleranceTypeValue_Diameter)
        if "PROJECTED" in mods:
            try:
                from OCP.XCAFDimTolObjects import (
                    XCAFDimTolObjects_GeomToleranceZoneModif_Projected,
                )
                obj.SetZoneModifier(
                    XCAFDimTolObjects_GeomToleranceZoneModif_Projected)
            except Exception:
                pass
        if "FREE" in mods:
            try:
                from OCP.XCAFDimTolObjects import (
                    XCAFDimTolObjects_GeomToleranceModif_Free_State,
                )
                obj.AddModifier(XCAFDimTolObjects_GeomToleranceModif_Free_State)
            except Exception:
                pass
        if "TANGENT" in mods:
            try:
                from OCP.XCAFDimTolObjects import (
                    XCAFDimTolObjects_GeomToleranceModif_Tangent_Plane,
                )
                obj.AddModifier(XCAFDimTolObjects_GeomToleranceModif_Tangent_Plane)
            except Exception:
                pass

        XCAFDoc_GeomTolerance.Set_s(tol_l).SetObject(obj)
        # FCFs record no toleranced-feature selector — attach to the whole
        # shape label (verified to survive the STEP body round trip).
        dt.SetGeomTolerance(_label_seq([main_label]), tol_l)
        attached_any = True

        for letter in fcf.get("datums") or []:
            da_l = datum_labels.get(letter)
            if da_l is None:
                notes.append(
                    f"fcf '{gchar}' references datum '{letter}' with no "
                    f"exported datum label — datum link omitted from STEP body"
                )
                continue
            dt.SetDatumToGeomTol(da_l, tol_l)
        stats["geom_tolerances_emitted"] += 1

    if attached_any:
        notes.append(
            "FCF tolerances attach to the whole-shape label "
            "(feature_control_frame_compose records no toleranced-feature "
            "selector)"
        )


def _write_caf(
    shape, out_path: Path, pmi: dict[str, Any], include_dimensions: bool,
    include_textures: bool, include_datums: bool, body: Any = None,
) -> dict[str, Any]:
    """STEPCAFControl_Writer route. Returns counts of what we managed to push.

    PMI-EXPORT-FIX (2026-06-11): previously this created *empty* XCAF labels
    (no XCAFDimTolObjects payload, no shape linkage) which the STEP writer
    silently drops — the body carried zero GDT entities. Now every tag is
    translated into a real payload + shape attachment (see module docstring).
    """
    stats: dict[str, Any] = {
        "dimensions_emitted": 0,
        "geom_tolerances_emitted": 0,
        "datums_emitted": 0,
        # Honest accounting: surface textures have no XCAF GDT class in this
        # OCP build, so nothing is ever emitted into the STEP body for them.
        "textures_emitted": 0,
        "sidecar_only": [],
        "notes": [],
    }
    notes: list[str] = stats["notes"]

    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_StepModelType
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    # PMI-EXPORT-FIX (2026-06-11): makeAssembly must be False. build123d
    # bodies wrap a TopoDS_Compound; AddShape(.., True, ..) turns it into an
    # XCAF *assembly* label, AddSubShape then returns null for every face
    # (no PMI attachment possible) and the GDT write ends in RetError.
    # Non-assembly mode keeps faces addressable as sub-shape labels.
    main_label = shape_tool.AddShape(shape, False, False)
    dimtol_tool = XCAFDoc_DocumentTool.DimTolTool_s(doc.Main())

    sub_shape_cache: list[tuple[Any, Any]] = []

    # Datums first so FCF datum references can link to them.
    datum_labels: dict[str, Any] = {}
    if include_datums and pmi.get("datums"):
        datum_labels = _emit_datums(
            dimtol_tool, shape_tool, main_label, shape, pmi["datums"],
            sub_shape_cache, stats, notes,
        )
    elif pmi.get("datums") and not include_datums:
        notes.append("include_datums=False — datum table left sidecar-only")

    if include_dimensions and pmi.get("dimensions"):
        _emit_dimensions(
            dimtol_tool, shape_tool, main_label, shape, body,
            pmi["dimensions"], sub_shape_cache, stats, notes,
        )

    if pmi.get("fcfs"):
        _emit_fcfs(dimtol_tool, main_label, pmi["fcfs"], datum_labels,
                   stats, notes)

    # Sidecar-only categories — no XCAF / AP242 semantic equivalent.
    # (include_* False means the category is dropped from the sidecar too,
    # so it is not listed as preserved-in-sidecar here.)
    if include_textures and pmi.get("surface_textures"):
        stats["sidecar_only"].append("surface_textures")
    if pmi.get("welds"):
        stats["sidecar_only"].append("welds")
    if pmi.get("basic_dimensions"):
        stats["sidecar_only"].append("basic_dimensions")
    if include_dimensions and any(
        e.get("datum_refs") for e in pmi.get("dimensions", [])
    ):
        stats["sidecar_only"].append("dimension_datum_refs")

    writer = STEPCAFControl_Writer()
    ok = writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    if not ok:
        raise RuntimeError(
            "export_step_ap242_pmi: STEPCAFControl_Writer.Transfer returned False"
        )
    write_status = writer.Write(str(out_path))
    if write_status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(
            f"export_step_ap242_pmi: STEPCAF Write failed (status={write_status})"
        )
    return stats


def _write_plain(shape, out_path: Path) -> None:
    """Fallback plain AP242 export when the CAF route fails."""
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

    writer = STEPControl_Writer()
    status = writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(f"export_step_ap242_pmi: Transfer failed (status={status})")
    write_status = writer.Write(str(out_path))
    if write_status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(
            f"export_step_ap242_pmi: Write failed (status={write_status})"
        )


@skill(
    name="export_step_ap242_pmi",
    category="pmi",
    level="atomic",
    summary="Write the body to STEP AP242 and bundle attached PMI metadata "
            "(dimensions / textures / welds / FCFs / datums). Uses "
            "STEPCAFControl_Writer when available and always emits a "
            "<path>.pmi.json sidecar. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["step_ap242_pmi_artifact"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.step_write_failed"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class ExportStepAp242Pmi(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1, description="output .step / .stp path")
        include_dimensions: bool = True
        include_textures: bool = True
        include_datums: bool = True

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("export_step_ap242_pmi: body is None")
        shape = _occt_shape(body)
        if shape is None:
            raise ValueError("export_step_ap242_pmi: body.wrapped is None")

        out_path = Path(args.path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        pmi = _gather_pmi(body)

        # PMI-EXPORT-FIX (2026-06-11): controller init BEFORE schema set
        # (reverse order is a silent no-op), and the global static is
        # restored afterwards so other exporters keep their own schema.
        prev_schema: str | None = None
        schema_forced = False
        try:
            prev_schema = _init_ap242_schema()
            schema_forced = True
        except Exception:
            pass

        # 1) Try CAF writer; fall back to plain.
        caf_stats: dict[str, Any] = {
            "dimensions_emitted": 0,
            "geom_tolerances_emitted": 0,
            "datums_emitted": 0,
            "textures_emitted": 0,
            "sidecar_only": [],
            "notes": [],
        }
        used_caf = False
        try:
            try:
                caf_stats = _write_caf(
                    shape, out_path, pmi,
                    include_dimensions=args.include_dimensions,
                    include_textures=args.include_textures,
                    include_datums=args.include_datums,
                    body=body,
                )
                used_caf = True
            except Exception as exc:
                caf_stats["notes"].append(f"CAF route failed: {exc}")
                _write_plain(shape, out_path)
        finally:
            if schema_forced:
                _restore_schema(prev_schema)

        if not out_path.exists():
            raise RuntimeError(
                f"export_step_ap242_pmi: file not produced -> {out_path}"
            )

        # 2) JSON sidecar — always written.
        sidecar_path = out_path.with_suffix(out_path.suffix + ".pmi.json")
        sidecar_payload: dict[str, Any] = {
            "schema": "AP242",
            "include_dimensions": bool(args.include_dimensions),
            "include_textures": bool(args.include_textures),
            "include_datums": bool(args.include_datums),
            "summary": {
                "dimensions": len(pmi.get("dimensions", [])),
                "surface_textures": len(pmi.get("surface_textures", [])),
                "welds": len(pmi.get("welds", [])),
                "fcfs": len(pmi.get("fcfs", [])),
                "datums": len(pmi.get("datums", {})),
                "basic_dimensions": len(pmi.get("basic_dimensions", [])),
            },
            "pmi": {
                "dimensions": pmi.get("dimensions", []) if args.include_dimensions else [],
                "surface_textures": (
                    pmi.get("surface_textures", []) if args.include_textures else []
                ),
                "welds": pmi.get("welds", []),
                "fcfs": pmi.get("fcfs", []),
                "datums": pmi.get("datums", {}) if args.include_datums else {},
                "basic_dimensions": pmi.get("basic_dimensions", []),
            },
        }
        sidecar_path.write_text(
            json.dumps(sidecar_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        extras = {
            "written_path": str(out_path),
            "sidecar_path": str(sidecar_path),
            "schema": "AP242",
            "used_caf": bool(used_caf),
            "caf_stats": caf_stats,
            "summary": sidecar_payload["summary"],
            "file_size_bytes": int(out_path.stat().st_size),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
