"""Fetch public CAD files for RE corpus testing.

Downloads STEP/IGES from no-auth public sources (mostly OCCT / FreeCAD / kicad
sample data on GitHub raw), verifies size + magic header, and writes an
inventory JSON.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "corpus" / "oem"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_BYTES = 10 * 1024 * 1024  # 10 MB cap
TIMEOUT = 30

# Candidate URLs. Verified via GitHub/GitLab API where possible.
CANDIDATES = [
    # --- OCCT (only 2 STEP + 2 IGES live in repo) ---
    ("occt", "https://raw.githubusercontent.com/Open-Cascade-SAS/OCCT/master/data/step/screw.step"),
    ("occt", "https://raw.githubusercontent.com/Open-Cascade-SAS/OCCT/master/data/step/linkrods.step"),
    ("occt-iges", "https://raw.githubusercontent.com/Open-Cascade-SAS/OCCT/master/data/iges/bearing.iges"),
    ("occt-iges", "https://raw.githubusercontent.com/Open-Cascade-SAS/OCCT/master/data/iges/hammer.iges"),
    # --- pythonocc-demos (verified list) ---
    ("pythonocc", "https://raw.githubusercontent.com/tpaviot/pythonocc-demos/master/assets/models/as1-oc-214.stp"),
    ("pythonocc", "https://raw.githubusercontent.com/tpaviot/pythonocc-demos/master/assets/models/as1_pe_203.stp"),
    ("pythonocc", "https://raw.githubusercontent.com/tpaviot/pythonocc-demos/master/assets/models/face_recognition_sample_part.stp"),
    ("pythonocc", "https://raw.githubusercontent.com/tpaviot/pythonocc-demos/master/assets/models/splinecage.stp"),
    ("pythonocc", "https://raw.githubusercontent.com/tpaviot/pythonocc-demos/master/assets/models/Ventilator.stp"),
    ("pythonocc", "https://raw.githubusercontent.com/tpaviot/pythonocc-demos/master/assets/models/11752.stp"),
    ("pythonocc-iges", "https://raw.githubusercontent.com/tpaviot/pythonocc-demos/master/assets/models/curve_geom_plate.igs"),
    ("pythonocc-iges", "https://raw.githubusercontent.com/tpaviot/pythonocc-demos/master/assets/models/surf114.igs"),
    # --- KiCad SMD components (small, varied) ---
    ("kicad", "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/master/Capacitor_SMD.3dshapes/C_0402_1005Metric.step"),
    ("kicad", "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/master/Capacitor_SMD.3dshapes/CP_Elec_3x5.4.step"),
    ("kicad", "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/master/Capacitor_SMD.3dshapes/CP_Elec_10x10.step"),
    ("kicad", "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/master/Resistor_SMD.3dshapes/R_0402_1005Metric.step"),
    ("kicad", "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/master/LED_SMD.3dshapes/LED_0402_1005Metric.step"),
    ("kicad", "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/master/Inductor_SMD.3dshapes/L_0402_1005Metric.step"),
    ("kicad", "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/master/Diode_SMD.3dshapes/D_0603_1608Metric.step"),
]


def is_step_or_iges(blob: bytes) -> str | None:
    """Return 'step' / 'iges' / None based on file magic."""
    head = blob[:200].decode("latin1", errors="ignore")
    if "ISO-10303" in head or "STEP" in head[:50]:
        return "step"
    # IGES start record: cols 73-80 ' S      1'
    if re.search(r"S +\d+\s*$", blob[:90].decode("latin1", errors="ignore"), re.M):
        return "iges"
    if head.lstrip().startswith("S"):
        return "iges"
    return None


def fetch(url: str, timeout: int = TIMEOUT) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": "stm-corpus/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            blob = r.read(MAX_BYTES + 1)
            if len(blob) > MAX_BYTES:
                print(f"  SKIP (too big >{MAX_BYTES}): {url}")
                return None
            return blob
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {url}")
    except urllib.error.URLError as e:
        print(f"  URLError {e.reason}: {url}")
    except TimeoutError:
        print(f"  TIMEOUT: {url}")
    except Exception as e:
        print(f"  ERR {type(e).__name__}: {e} :: {url}")
    return None


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")


def main() -> int:
    inventory: list[dict] = []
    seen_sha: set[str] = set()
    print(f"Output dir: {OUT_DIR}")
    print(f"Trying {len(CANDIDATES)} candidate URLs...")
    for source, url in CANDIDATES:
        basename = url.rsplit("/", 1)[-1]
        print(f"[{source}] {basename}")
        blob = fetch(url)
        if blob is None:
            continue
        kind = is_step_or_iges(blob)
        if kind is None:
            print(f"  SKIP (not STEP/IGES): {url}")
            continue
        sha = hashlib.sha256(blob).hexdigest()
        if sha in seen_sha:
            print(f"  SKIP (dup sha): {url}")
            continue
        seen_sha.add(sha)
        ext = ".step" if kind == "step" else ".iges"
        # normalise output extension
        base_noext = basename.rsplit(".", 1)[0]
        out_name = f"{slug(source)}__{slug(base_noext)}{ext}"
        out_path = OUT_DIR / out_name
        out_path.write_bytes(blob)
        inventory.append({
            "filename": out_name,
            "source_url": url,
            "source": source,
            "kind": kind,
            "size_bytes": len(blob),
            "sha256": sha,
        })
        print(f"  OK  {out_name}  ({len(blob)} bytes)")
    inv_path = OUT_DIR / "_inventory.json"
    inv_path.write_text(json.dumps(inventory, indent=2))
    print()
    print(f"Downloaded: {len(inventory)} files")
    print(f"Inventory: {inv_path}")
    for it in inventory:
        print(f"  - {it['filename']}  {it['size_bytes']} B  {it['kind']}")
    return 0 if len(inventory) >= 5 else 1


if __name__ == "__main__":
    sys.exit(main())
