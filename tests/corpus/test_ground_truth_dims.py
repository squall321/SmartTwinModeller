"""Dimensional ground-truth regression — V7/A4.

Each ``corpus/oem/ground_truth/*.json`` sidecar pins NOMINAL dimensions for a
gitignored corpus STEP (nominals from filenames + catalogs/standards/*.yaml +
published standards — see each sidecar's per-entry ``note``). For every
sidecar whose corpus file exists locally we import the STEP and measure:

    bbox_x / bbox_y / bbox_z  — OCCT optimal-bbox extents along X/Y/Z
    height                    — bbox Z extent (parts modeled axis-up)
    af_width                  — min(bbox X, bbox Y): hex width across flats
    thickness / width         — min of all three extents (orientation-robust:
                                washer thickness, bearing axial width)
    outer_diameter            — 2 x max cylindrical-face radius (BRepAdaptor)
    bore_diameter             — classify_holes diameter set must contain a
                                value within tolerance of the nominal

Assertion: |measured - nominal| / nominal * 100 <= tol_pct (default 5).

KNOWN_GAPS lists corpus files whose measurement legitimately fails today —
they xfail with the recorded reason and turn into a hard FAIL the moment they
start passing (so stale entries cannot linger). Tolerances are never widened
to make a file pass; deliberate per-dimension omissions are documented inside
the sidecar notes (e.g. DIN 125 M8/M10 thickness model deviations).

Markers: requires_oem (corpus is gitignored — gate via PHONE_DESIGNER_OEM_REF,
see tests/conftest.py) + slow (imports ~40 STEP files).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

PROJECT = Path(__file__).resolve().parents[2]
GT_DIR = PROJECT / "corpus" / "oem" / "ground_truth"

VALID_KINDS = {
    "outer_diameter", "bore_diameter", "width", "height",
    "bbox_x", "bbox_y", "bbox_z", "af_width", "thickness",
}

# Corpus files whose ground-truth measurement legitimately fails today.
# relpath (sidecar "file" value) -> reason. Entries xfail; an entry that
# unexpectedly passes FAILS the run so it gets removed promptly.
KNOWN_GAPS: dict[str, str] = {
    # Body bbox measures 1.6 x 0.87 x 0.66 mm: the molded SOD-package body in
    # the KiCad 3D model is 0.87 mm wide, +8.7% over the 1608-metric nominal
    # 0.8. Model deviation from the package nominal, not a measurement bug —
    # do not widen tol.
    "corpus/oem/kicad__D_0603_1608Metric.step":
        "KiCad D_0603 mold body is 0.87 mm wide vs 1608-metric nominal "
        "0.8 mm (+8.7%) — source-model deviation",
}

pytestmark = [pytest.mark.requires_oem, pytest.mark.slow]


# ──────────────────────────────────────────────────────────────────────────────
# Measurement helpers


def _load_shape(path: Path):
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != 1:  # IFSelect_RetDone
        raise RuntimeError(f"STEP read failed (status={status}): {path}")
    reader.TransferRoots()
    return reader.OneShape()


def _bbox_extents(shape) -> tuple[float, float, float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bb)
    if bb.IsVoid():
        raise RuntimeError("empty bounding box")
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (xmax - xmin, ymax - ymin, zmax - zmin)


def _cylinder_radii(shape) -> list[float]:
    """All distinct cylindrical-face radii on the shape (BRepAdaptor probe)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    radii: set[float] = set()
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        face = TopoDS.Face_s(it.Current())
        try:
            adaptor = BRepAdaptor_Surface(face)
            if adaptor.GetType() == GeomAbs_SurfaceType.GeomAbs_Cylinder:
                radii.add(adaptor.Cylinder().Radius())
        except Exception:
            pass  # degenerate face — skip
        it.Next()
    return sorted(radii)


def _hole_diameters(shape) -> list[float]:
    """Every diameter classify_holes reports on the body (all holes, all bands)."""
    from build123d import Part

    from phone_designer.skills.inspect.classify_holes import ClassifyHoles

    holes = ClassifyHoles().apply(Part(shape), {"match_standards": False}).extras["holes"]
    out: list[float] = []
    for h in holes:
        out.extend(float(d) for d in h["diameters_mm"])
    return out


class _Measurements:
    """Lazy per-file measurement cache (bbox / cylinders / holes computed once)."""

    def __init__(self, path: Path):
        self.shape = _load_shape(path)
        self._bbox: tuple[float, float, float] | None = None
        self._radii: list[float] | None = None
        self._holes: list[float] | None = None

    @property
    def bbox(self) -> tuple[float, float, float]:
        if self._bbox is None:
            self._bbox = _bbox_extents(self.shape)
        return self._bbox

    @property
    def cylinder_radii(self) -> list[float]:
        if self._radii is None:
            self._radii = _cylinder_radii(self.shape)
        return self._radii

    @property
    def hole_diameters(self) -> list[float]:
        if self._holes is None:
            self._holes = _hole_diameters(self.shape)
        return self._holes


_CACHE: dict[str, _Measurements] = {}


def _measurements_for(relpath: str) -> _Measurements:
    if relpath not in _CACHE:
        _CACHE[relpath] = _Measurements(PROJECT / relpath)
    return _CACHE[relpath]


def _measure_kind(meas: _Measurements, kind: str, nominal: float) -> float:
    """Return the measured value for ``kind``. Raises ValueError when the
    geometry exposes nothing measurable for the kind (treated as a failure)."""
    ext = meas.bbox
    if kind == "bbox_x":
        return ext[0]
    if kind == "bbox_y":
        return ext[1]
    if kind in ("bbox_z", "height"):
        return ext[2]
    if kind == "af_width":
        return min(ext[0], ext[1])
    if kind in ("thickness", "width"):
        return min(ext)
    if kind == "outer_diameter":
        radii = meas.cylinder_radii
        if not radii:
            raise ValueError("no cylindrical faces found")
        return 2.0 * max(radii)
    if kind == "bore_diameter":
        diams = meas.hole_diameters
        if not diams:
            raise ValueError("classify_holes found no holes")
        # The nominal bore must appear somewhere in the classified diameter
        # set — compare against the closest candidate.
        return min(diams, key=lambda d: abs(d - nominal))
    raise ValueError(f"unknown kind {kind!r}")


# ──────────────────────────────────────────────────────────────────────────────
# Sidecar discovery


def _sidecars() -> list[Path]:
    if not GT_DIR.is_dir():
        return []
    return sorted(GT_DIR.glob("*.json"))


def _load_sidecar(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ──────────────────────────────────────────────────────────────────────────────
# Tests


def test_sidecar_corpus_is_populated():
    """≥40 committed sidecars, every one schema-valid (plan V7/A4 target)."""
    sidecars = _sidecars()
    assert len(sidecars) >= 40, f"expected ≥40 ground-truth sidecars, found {len(sidecars)}"
    for sc in sidecars:
        spec = _load_sidecar(sc)
        assert isinstance(spec.get("file"), str) and spec["file"], f"{sc.name}: bad 'file'"
        expected = spec.get("expected")
        assert isinstance(expected, list) and expected, f"{sc.name}: bad 'expected'"
        for entry in expected:
            assert entry.get("kind") in VALID_KINDS, f"{sc.name}: bad kind {entry.get('kind')!r}"
            assert isinstance(entry.get("value_mm"), (int, float)) and entry["value_mm"] > 0, (
                f"{sc.name}: bad value_mm {entry.get('value_mm')!r}"
            )
            assert 0 < float(entry.get("tol_pct", 5)) <= 10, f"{sc.name}: bad tol_pct"
    # every KNOWN_GAPS key must correspond to a sidecar
    sidecar_files = {_load_sidecar(sc)["file"] for sc in sidecars}
    for rel in KNOWN_GAPS:
        assert rel in sidecar_files, f"KNOWN_GAPS entry has no sidecar: {rel}"


@pytest.mark.parametrize("sidecar", _sidecars(), ids=lambda p: p.stem)
def test_ground_truth_dims(sidecar: Path):
    spec = _load_sidecar(sidecar)
    rel = spec["file"]
    src = PROJECT / rel
    if not src.exists():
        pytest.skip(f"corpus file not present locally: {rel}")

    meas = _measurements_for(rel)
    failures: list[str] = []
    for entry in spec["expected"]:
        kind = entry["kind"]
        nominal = float(entry["value_mm"])
        tol_pct = float(entry.get("tol_pct", 5))
        try:
            measured = _measure_kind(meas, kind, nominal)
        except Exception as exc:  # measurement infrastructure failure
            failures.append(f"{kind}: unmeasurable — {type(exc).__name__}: {exc}")
            continue
        err_pct = abs(measured - nominal) / nominal * 100.0
        if err_pct > tol_pct:
            failures.append(
                f"{kind}: nominal {nominal} mm, measured {measured:.4f} mm, "
                f"error {err_pct:.2f}% > {tol_pct}% [{entry.get('note', '')}]"
            )

    if failures:
        msg = f"{rel}: {len(failures)} dimension(s) out of tolerance:\n  " + "\n  ".join(failures)
        if rel in KNOWN_GAPS:
            pytest.xfail(f"KNOWN_GAPS[{rel}]: {KNOWN_GAPS[rel]}\n{msg}")
        pytest.fail(msg)
    elif rel in KNOWN_GAPS:
        pytest.fail(
            f"KNOWN_GAPS entry now PASSES — remove it from KNOWN_GAPS: {rel} "
            f"(was: {KNOWN_GAPS[rel]})"
        )
