"""Round-trip tests for read_step_pmi (plan item A7) — no external corpus.

Two round-trip layers, mirroring the export skill's two-layer strategy:

1. **In-house export round trip** (mandated path): attach PMI with the
   existing pmi/ + inspect/ skills, export with ``export_step_ap242_pmi``,
   read back with ``read_step_pmi``. Empirically verified limitation: the
   export skill creates *empty* XCAF GDT labels and never gets the AP242
   schema applied (it calls ``Interface_Static.SetCVal_s`` before the STEP
   controller is initialised), so the STEP **body** contains zero GDT
   entities — the tolerance types / values / datum letters survive through
   the ``.pmi.json`` sidecar, which the reader picks up.

2. **Genuine AP242 PMI round trip** (CAF route proof): build a STEP file
   whose XCAF document carries real ``XCAFDimTolObjects`` payloads
   (flatness + position tolerances, datums A/B, a ±-toleranced linear
   dimension), write it with the AP242 schema properly forced, and assert
   ``read_step_pmi`` extracts types + values + datum letters from the STEP
   body itself.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.datum_target_assign import DatumTargetAssign
from phone_designer.skills.inspect.feature_control_frame_compose import (
    FeatureControlFrameCompose,
)
from phone_designer.skills.pmi.export_step_ap242_pmi import ExportStepAp242Pmi
from phone_designer.skills.pmi.pmi_dimension_callout import PmiDimensionCallout
from phone_designer.skills.pmi.read_step_pmi import ReadStepPmi


def _box():
    return Box().apply(None, {"length_mm": 40, "width_mm": 30, "height_mm": 10}).body


def _box_with_pmi():
    """Box + dimension callout + datums A/B + flatness & position FCFs."""
    body = _box()
    body = PmiDimensionCallout().apply(body, {
        "entity_a_selector": {"kind": "face_named", "name": "top"},
        "entity_b_selector": {"kind": "face_named", "name": "bottom"},
        "kind": "linear",
        "nominal_value": 10.0,
        "upper_tol": 0.05,
        "lower_tol": -0.05,
        "datum_refs": ["A"],
    }).body
    body = DatumTargetAssign().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "datum_label": "A",
        "precedence": 1,
    }).body
    body = DatumTargetAssign().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "datum_label": "B",
        "precedence": 2,
    }).body
    body = FeatureControlFrameCompose().apply(body, {
        "geom_char": "flatness",
        "tolerance_value": 0.02,
        "datums": [],
    }).body
    body = FeatureControlFrameCompose().apply(body, {
        "geom_char": "position",
        "tolerance_value": 0.1,
        "modifiers": ["DIAMETER", "MMC"],
        "datums": ["A", "B"],
    }).body
    return body


# ---------------------------------------------------------------------------
# 1. In-house export round trip (sidecar carries the fidelity).
# ---------------------------------------------------------------------------

def test_roundtrip_via_export_skill_sidecar(tmp_path: Path):
    body = _box_with_pmi()
    out = tmp_path / "rt_inhouse.step"
    ExportStepAp242Pmi().apply(body, {"path": str(out)})

    r = ReadStepPmi().apply(body, {"path": str(out)})
    pmi = r.extras["pmi"]

    # Contract shape.
    assert pmi["source_path"] == str(out)
    assert isinstance(pmi["dimensions"], list)
    assert isinstance(pmi["geometric_tolerances"], list)
    assert isinstance(pmi["datums"], list)
    assert set(pmi["counts"]) == {"dimensions", "geometric_tolerances", "datums"}

    # Sidecar found and parsed — this is where the in-house exporter
    # actually preserves the PMI (its STEP body carries no GDT entities).
    assert pmi["sidecar_path"] is not None
    sc = pmi["sidecar"]
    assert sc is not None
    assert sc["schema"] == "AP242"

    # Tolerance types + values survive (FCFs).
    fcfs = {f["geom_char"]: f for f in sc["pmi"]["fcfs"]}
    assert set(fcfs) == {"flatness", "position"}
    assert fcfs["flatness"]["tolerance_value"] == 0.02
    assert fcfs["position"]["tolerance_value"] == 0.1
    # Datum letters survive (FCF refs + datum table).
    assert fcfs["position"]["datums"] == ["A", "B"]
    assert set(sc["pmi"]["datums"].keys()) == {"A", "B"}
    # Dimension value + tolerances survive.
    dims = sc["pmi"]["dimensions"]
    assert len(dims) == 1
    assert dims[0]["nominal"] == 10.0
    assert dims[0]["upper_tol"] == 0.05
    assert dims[0]["lower_tol"] == -0.05
    assert dims[0]["datum_refs"] == ["A"]

    # Honest reporting: when the STEP body carries nothing, the skill says so.
    if sum(pmi["counts"].values()) == 0:
        assert any("sidecar" in n for n in pmi["notes"])

    # Read-only — body unchanged.
    assert r.body is body


# ---------------------------------------------------------------------------
# 2. Genuine AP242 PMI round trip through the STEP body (CAF route).
# ---------------------------------------------------------------------------

def _write_real_pmi_step(out: Path) -> None:
    """Write a STEP AP242 file whose body carries semantic XCAF PMI:
    linear dimension 10 ±0.05, flatness 0.02, position 0.1 |A|B|."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_Controller, STEPControl_StepModelType
    from OCP.TCollection import TCollection_ExtendedString, TCollection_HAsciiString
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDimTolObjects import (
        XCAFDimTolObjects_DatumObject,
        XCAFDimTolObjects_DimensionObject,
        XCAFDimTolObjects_DimensionType_Location_LinearDistance,
        XCAFDimTolObjects_GeomToleranceObject,
        XCAFDimTolObjects_GeomToleranceType_Flatness,
        XCAFDimTolObjects_GeomToleranceType_Position,
    )
    from OCP.XCAFDoc import (
        XCAFDoc_Datum,
        XCAFDoc_Dimension,
        XCAFDoc_DocumentTool,
        XCAFDoc_GeomTolerance,
    )

    shape = BRepPrimAPI_MakeBox(40.0, 30.0, 10.0).Shape()
    faces = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        faces.append(exp.Current())
        exp.Next()
    assert len(faces) >= 2

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    dt = XCAFDoc_DocumentTool.DimTolTool_s(doc.Main())
    main_l = st.AddShape(shape, True, True)
    f0_l = st.AddSubShape(main_l, faces[0])
    f1_l = st.AddSubShape(main_l, faces[1])

    # Linear dimension 10 +0.05/-0.05 between two faces.
    dim_l = dt.AddDimension()
    dobj = XCAFDimTolObjects_DimensionObject()
    dobj.SetType(XCAFDimTolObjects_DimensionType_Location_LinearDistance)
    dobj.SetValue(10.0)
    dobj.SetUpperTolValue(0.05)
    dobj.SetLowerTolValue(-0.05)
    XCAFDoc_Dimension.Set_s(dim_l).SetObject(dobj)
    s1 = TDF_LabelSequence(); s1.Append(f0_l)
    s2 = TDF_LabelSequence(); s2.Append(f1_l)
    dt.SetDimension(s1, s2, dim_l)

    # Flatness 0.02 on face0.
    fl_l = dt.AddGeomTolerance()
    fobj = XCAFDimTolObjects_GeomToleranceObject()
    fobj.SetType(XCAFDimTolObjects_GeomToleranceType_Flatness)
    fobj.SetValue(0.02)
    XCAFDoc_GeomTolerance.Set_s(fl_l).SetObject(fobj)
    sf = TDF_LabelSequence(); sf.Append(f0_l)
    dt.SetGeomTolerance(sf, fl_l)

    # Position 0.1 on face1 referencing datums A then B.
    pos_l = dt.AddGeomTolerance()
    pobj = XCAFDimTolObjects_GeomToleranceObject()
    pobj.SetType(XCAFDimTolObjects_GeomToleranceType_Position)
    pobj.SetValue(0.1)
    XCAFDoc_GeomTolerance.Set_s(pos_l).SetObject(pobj)
    sp = TDF_LabelSequence(); sp.Append(f1_l)
    dt.SetGeomTolerance(sp, pos_l)

    for i, (letter, face_l) in enumerate((("A", f0_l), ("B", f1_l))):
        da_l = dt.AddDatum(
            TCollection_HAsciiString(letter),
            TCollection_HAsciiString(""),
            TCollection_HAsciiString(""),
        )
        daobj = XCAFDimTolObjects_DatumObject()
        daobj.SetName(TCollection_HAsciiString(letter))
        daobj.SetPosition(i + 1)
        XCAFDoc_Datum.Set_s(da_l).SetObject(daobj)
        sd = TDF_LabelSequence(); sd.Append(face_l)
        dt.SetDatum(sd, da_l)
        dt.SetDatumToGeomTol(da_l, pos_l)

    # AP242 schema must be set AFTER the controller exists, or it is a no-op
    # (this is exactly the bug that keeps the in-house exporter PMI-less).
    STEPControl_Controller.Init_s()
    prev_schema = Interface_Static.CVal_s("write.step.schema")
    assert Interface_Static.SetCVal_s("write.step.schema", "AP242DIS")
    try:
        w = STEPCAFControl_Writer()
        assert w.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
        assert w.Write(str(out)) == IFSelect_ReturnStatus.IFSelect_RetDone
    finally:
        if prev_schema:
            Interface_Static.SetCVal_s("write.step.schema", prev_schema)
    assert out.exists() and out.stat().st_size > 0


def test_roundtrip_real_ap242_pmi_caf_route(tmp_path: Path):
    out = tmp_path / "rt_real_pmi.step"
    _write_real_pmi_step(out)

    body = _box()
    r = ReadStepPmi().apply(body, {"path": str(out)})
    pmi = r.extras["pmi"]

    # No sidecar for this fixture — everything must come from the STEP body.
    assert pmi["sidecar"] is None
    assert pmi["counts"] == {
        "dimensions": 1,
        "geometric_tolerances": 2,
        "datums": 2,
    }

    # Geometric tolerance types + values survive the STEP body.
    gts = {g["type"]: g for g in pmi["geometric_tolerances"]}
    assert set(gts) == {"flatness", "position"}
    assert gts["flatness"]["value_mm"] == pytest.approx(0.02)
    assert gts["position"]["value_mm"] == pytest.approx(0.1)

    # Datum letters survive — both on the position FCF (precedence order)
    # and in the datum table.
    assert gts["position"]["datum_refs"] == ["A", "B"]
    assert gts["flatness"]["datum_refs"] == []
    assert {d["name"] for d in pmi["datums"]} == {"A", "B"}

    # Dimension value + plus/minus tolerance survive. NOTE: OCCT returns the
    # lower bound as a magnitude after the round trip (exported as -0.05).
    dims = pmi["dimensions"]
    assert len(dims) == 1
    d = dims[0]
    assert "linear" in d["type"]
    assert d["value_mm"] == pytest.approx(10.0)
    assert d["is_plus_minus"] is True
    assert d["upper_tol_mm"] == pytest.approx(0.05)
    assert abs(d["lower_tol_mm"]) == pytest.approx(0.05)

    # Tolerances reference shape labels in the read document.
    assert gts["flatness"]["shape_ref_labels"]["first"]

    # Read-only — body unchanged.
    assert r.body is body

    # Labels reported as OCAF entry strings.
    assert all(g["label"].startswith("0:") for g in pmi["geometric_tolerances"])


# ---------------------------------------------------------------------------
# 3. Error contract.
# ---------------------------------------------------------------------------

def test_missing_file_raises(tmp_path: Path):
    body = _box()
    with pytest.raises(Exception, match="file not found"):
        ReadStepPmi().apply(body, {"path": str(tmp_path / "nope.step")})
