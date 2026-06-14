"""Build the PILLAR FREEFORM corpus fixtures into fixtures/freeform/ (phase-3,
2026-06-14).

The freeform reconstruction lane (Phase-1 silhouette base + Phase-2 loft
recovery) needs its OWN committed fixtures so its honest scores are tracked
separately from the prismatic box_complex lane. This script writes one STEP
per builder in ``fixtures.freeform`` and verifies each is a single valid solid
whose volume matches the builder's closed-form analytic expectation (±2 %).

Like the other corpus STEP outputs the generated ``.step`` files are
GITIGNORED (root ``.gitignore`` already globs ``*.step``); only the builders
(``fixtures/freeform/__init__.py``) and this script are tracked, so a fresh
checkout regenerates byte-deterministic fixtures via build123d.

Fixtures produced (all under fixtures/freeform/):
  rounded_plate.step    -- rounded prismatic plate  (silhouette-extrude target)
  loft_boss.step        -- circle→rounded-rect loft  (loft-section target)
  revolve_frustum.step  -- revolved trapezoid meridian (revolve target)
  swept_channel.step    -- L-shaped swept channel     (sweep target)

Exit codes:
  0 — every fixture written, single solid, analytic volume within ±2 %.
  1 — at least one builder failed or drifted; the offending names are listed.

Usage (from repo root):
    venv/Scripts/python.exe scripts/build_freeform_fixtures.py [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

#: builders are validated to be within this RELATIVE tolerance of the
#: analytic volume (matches the tests/skills/test_freeform_fixtures.py gate).
VOLUME_REL_TOL = 0.02


def _ensure_import_path() -> None:
    """Allow running the script directly without `pip install -e .`."""
    src = PROJECT / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    fixtures = PROJECT / "fixtures"
    if fixtures.exists() and str(fixtures) not in sys.path:
        sys.path.insert(0, str(fixtures))


_ensure_import_path()


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    shape = body.wrapped if hasattr(body, "wrapped") else body
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _write_step(body, out_path: Path) -> None:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    shape = body.wrapped if hasattr(body, "wrapped") else body
    writer = STEPControl_Writer()
    if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
        raise RuntimeError(f"STEP transfer failed for {out_path.name}")
    if writer.Write(str(out_path)) != IFSelect_RetDone:
        raise RuntimeError(f"STEP write failed for {out_path.name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out-dir", type=Path, default=PROJECT / "fixtures" / "freeform"
    )
    args = ap.parse_args()

    import freeform  # fixtures/freeform/__init__.py

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    for name, builder in freeform.BUILDERS.items():
        print(f">>> {name} ({freeform.MECHANISM[name]}) ...", flush=True)
        try:
            part, analytic_vol = builder()
            n_solids = len(part.solids())
            if n_solids != 1:
                raise RuntimeError(f"expected 1 solid, got {n_solids}")

            out = out_dir / f"{name}.step"
            _write_step(part, out)
            size = out.stat().st_size
            if size == 0:
                raise RuntimeError(f"{out.name} written but empty")

            measured = _volume(part)
            rel = abs(measured - analytic_vol) / analytic_vol
            if rel > VOLUME_REL_TOL:
                raise RuntimeError(
                    f"volume drift {rel * 100:.3f}% > {VOLUME_REL_TOL * 100:.0f}% "
                    f"(measured={measured:.3f}, analytic={analytic_vol:.3f})"
                )
            print(
                f"    {out.name}: {size} bytes, solids={n_solids}, "
                f"vol={measured:.3f} mm^3 (analytic {analytic_vol:.3f}, "
                f"{rel * 100:.3f}%)"
            )
        except Exception:
            failures.append(name)
            traceback.print_exc()

    if failures:
        print(f"\nFAILED builders: {', '.join(failures)}")
        return 1
    print(f"\nAll freeform fixtures written to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
