"""_catalog — typed YAML catalog loader + validator.

This module is **isolated helper infrastructure**. It does NOT touch the
existing shared infra (_spec / _registry / _resolvers / executor). It only
provides:

  - One pydantic model per catalog family (ThreadMetricEntry, DowelPinEntry,
    BearingEntry, ORingEntry, ConnectorEntry, MagnetEntry, QiCoilEntry,
    CoinCellEntry, MembraneEntry, ThreadImperialEntry).
  - load_typed(family, name) -> {spec_name: EntryModel} — load a YAML file
    from catalogs/<family>/<name>.yaml, validate every row, and return a
    dict keyed by spec name (e.g. "M3", "AS568-013", "608", "USB_C_v2", ...).
  - validate_all() -> {path: [error, ...]} — walk every known catalog file
    (catalogs/<family>/<name>.yaml) and collect pydantic errors. Empty list
    means clean.
  - list_specs(family) -> [name, ...] — list catalog stems for a family.

The model definitions accept ``model_config = ConfigDict(extra="allow")``
so the catalog can carry extra metadata (notes, chemistry, etc.) without
breaking validation. Required fields are the dimensions used by the
generator / inspect skills.
"""
from __future__ import annotations

import pathlib
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


# ──────────────────────────────────────────────────────────────────────────────
# Paths


def _catalogs_root() -> pathlib.Path:
    """Repo-root/catalogs.

    This file lives at ``src/phone_designer/skills/_catalog.py`` — climb 4
    parents (skills → phone_designer → src → repo-root) to reach the repo
    and then descend into ``catalogs``.
    """
    return pathlib.Path(__file__).resolve().parents[3] / "catalogs"


# ──────────────────────────────────────────────────────────────────────────────
# Entry models — one per catalog family.
#
# Each model captures the actual schema we use today in the YAML files. Extra
# fields (notes, chemistry, voltage, grade, …) are allowed so the YAMLs can
# carry metadata for humans / agents without breaking the typed loader.


class _CatalogEntry(BaseModel):
    """Common base — allow extras, forbid nothing."""

    model_config = ConfigDict(extra="allow")


class ThreadMetricEntry(_CatalogEntry):
    """ISO metric coarse thread row (M2, M3, M5, …)."""

    outer_d_mm: float = Field(gt=0, le=200)
    pitch_mm: float = Field(gt=0, le=20)
    tap_drill_mm: float = Field(gt=0, le=200)
    clearance_close_mm: float = Field(gt=0, le=200)
    clearance_medium_mm: float = Field(gt=0, le=200)
    clearance_coarse_mm: float = Field(gt=0, le=200)
    head_diameter_socket_mm: float | None = Field(default=None, gt=0, le=200)
    counterbore_d_iso_mm: float | None = Field(default=None, gt=0, le=200)
    counterbore_depth_iso_mm: float | None = Field(default=None, gt=0, le=200)
    countersink_d_iso10642_mm: float | None = Field(default=None, gt=0, le=200)
    countersink_angle_deg: float | None = Field(default=None, gt=0, le=180)


class ThreadImperialEntry(_CatalogEntry):
    """Imperial (UNC / UNF) thread row — accepts the same shape as metric.

    We don't ship an imperial catalog today, but we still type-check anyone
    who adds one. Fields mirror the metric ones in mm-equivalent.
    """

    outer_d_in: float = Field(gt=0, le=10)
    tpi: float = Field(gt=0, le=200, description="threads per inch")
    tap_drill_in: float = Field(gt=0, le=10)
    clearance_close_in: float | None = Field(default=None, gt=0, le=10)
    clearance_medium_in: float | None = Field(default=None, gt=0, le=10)
    clearance_coarse_in: float | None = Field(default=None, gt=0, le=10)


class DowelPinEntry(_CatalogEntry):
    """ISO 2338 / DIN 7 cylindrical dowel pin row."""

    diameter_mm: float = Field(gt=0, le=200)
    press_fit_hole_mm: float = Field(gt=0, le=200)
    slip_fit_hole_mm: float = Field(gt=0, le=200)


class BearingEntry(_CatalogEntry):
    """ISO 15 deep-groove ball bearing row."""

    bore_id_mm: float = Field(gt=0, le=500)
    outer_d_mm: float = Field(gt=0, le=1000)
    width_mm: float = Field(gt=0, le=500)


class ORingEntry(_CatalogEntry):
    """AS568 / ISO 3601 O-ring row."""

    cs_mm: float = Field(gt=0, le=50, description="cross-section (W)")
    id_mm: float = Field(gt=0, le=500)
    od_mm: float = Field(gt=0, le=500)
    face_groove_width_mm: float | None = Field(default=None, gt=0, le=50)
    face_groove_depth_mm: float | None = Field(default=None, gt=0, le=50)


class ConnectorEntry(_CatalogEntry):
    """Receptacle cutout row (USB-C, USB-A, Lightning, 3.5 mm jack, …)."""

    cutout_w_mm: float = Field(gt=0, le=200)
    cutout_h_mm: float = Field(gt=0, le=200)
    inner_radius_mm: float = Field(ge=0, le=100)
    recess_depth_mm: float = Field(gt=0, le=200)
    bezel_chamfer_mm: float = Field(default=0.0, ge=0, le=10)


class MagnetEntry(_CatalogEntry):
    """NdFeB disc magnet row."""

    magnet_d_mm: float = Field(gt=0, le=200)
    magnet_t_mm: float = Field(gt=0, le=200)
    grade: str | None = None


class QiCoilEntry(_CatalogEntry):
    """WPC Qi wireless-charging coil envelope row."""

    outer_d_mm: float = Field(gt=0, le=500)
    inner_d_mm: float = Field(ge=0, le=500)
    thickness_mm: float = Field(gt=0, le=50)


class CoinCellEntry(_CatalogEntry):
    """IEC 60086-3 coin/button primary cell row."""

    diameter_mm: float = Field(gt=0, le=200)
    thickness_mm: float = Field(gt=0, le=50)
    chemistry: str | None = None
    nominal_voltage_v: float | None = Field(default=None, gt=0, le=20)


class MembraneEntry(_CatalogEntry):
    """Gore / acoustic vent membrane row."""

    membrane_d_mm: float = Field(gt=0, le=200)
    mounting_d_mm: float = Field(gt=0, le=200)
    boss_height_mm: float = Field(gt=0, le=20)


# ──────────────────────────────────────────────────────────────────────────────
# Family → (top-level key, model) routing.
#
# Each catalog YAML wraps its entries in a top-level dict key (e.g.
# `threads:`, `bearings:`, `o_rings:`, …). The router below tells the loader
# which key to look under and which model to validate each row with.


_FAMILY_ROUTES: dict[str, list[tuple[str, type[_CatalogEntry]]]] = {
    # Multiple candidate top-keys per (family, file stem) because the legacy
    # catalogs use slightly different roots. The loader tries each in order
    # and uses the first one that resolves to a non-empty dict.
    "standards/threads_metric":     [("threads", ThreadMetricEntry)],
    "standards/threads_imperial":   [("threads", ThreadImperialEntry)],
    "standards/dowel_pins":         [("pins", DowelPinEntry)],
    "standards/bearings_metric":    [("bearings", BearingEntry)],
    "standards/o_rings_as568":      [("o_rings", ORingEntry)],
    "bearings/deep_groove_ball":    [("bearings", BearingEntry)],
    "seals/iso6194":                [("seals", _CatalogEntry)],
    "connectors/usb":               [("connectors", ConnectorEntry)],
    "connectors/usb_a":             [("connectors", ConnectorEntry)],
    "connectors/usb_c":             [("connectors", ConnectorEntry)],
    "connectors/lightning":         [("connectors", ConnectorEntry)],
    "connectors/audio_jack_35":     [("connectors", ConnectorEntry)],
    "magnets/ndfeb":                [("magnets", MagnetEntry)],
    "qi_coils/wpc":                 [("coils", QiCoilEntry)],
    "coin_cells/iec60086":          [("cells", CoinCellEntry)],
    "membranes/gore":               [("membranes", MembraneEntry)],
}


# Family-only defaults (used when we discover a new YAML stem we don't know).
# Falls back to _CatalogEntry (extras-allowed) so unknown families still
# pass validation as long as the YAML is structurally a dict-of-dict.
_FAMILY_DEFAULT_KEYS: dict[str, list[str]] = {
    "standards":   ["threads", "pins", "bearings", "o_rings", "rings",
                    "screws", "nuts", "keys", "inserts"],
    "bearings":    ["bearings"],
    "seals":       ["seals"],
    "connectors":  ["connectors"],
    "magnets":     ["magnets"],
    "qi_coils":    ["coils"],
    "coin_cells":  ["cells"],
    "membranes":   ["membranes"],
}


# Config-style families — each YAML file describes one config record (not a
# dict of multiple entries). validate_all() accepts these as long as they
# parse to a non-empty top-level dict. No row-by-row validation is done.
_CONFIG_STYLE_FAMILIES: frozenset[str] = frozenset({
    "processes",
    "budgets",
    "dfm_inspect",
    "arrangements",
})


def _resolve_route(
    family: str, name: str,
) -> list[tuple[str, type[_CatalogEntry]]]:
    """Return [(top_key, model), ...] candidates for (family, name)."""
    key = f"{family}/{name}"
    if key in _FAMILY_ROUTES:
        return _FAMILY_ROUTES[key]
    # Unknown stem — fall back to per-family default keys with the generic
    # _CatalogEntry model.
    keys = _FAMILY_DEFAULT_KEYS.get(family, [name])
    return [(k, _CatalogEntry) for k in keys]


# ──────────────────────────────────────────────────────────────────────────────
# Loader / validator API


def _read_yaml(family: str, name: str) -> dict[str, Any] | None:
    path = _catalogs_root() / family / f"{name}.yaml"
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None


def load_typed(family: str, name: str) -> dict[str, _CatalogEntry]:
    """Load catalogs/<family>/<name>.yaml and return {spec_name: EntryModel}.

    Raises ``FileNotFoundError`` if the YAML does not exist and
    ``ValidationError`` (pydantic) on the first row that fails validation.
    Extra metadata fields are preserved on the model (extra="allow").
    """
    raw = _read_yaml(family, name)
    if raw is None:
        raise FileNotFoundError(
            f"catalog not found: catalogs/{family}/{name}.yaml",
        )

    routes = _resolve_route(family, name)
    entries_raw: dict[str, Any] | None = None
    model: type[_CatalogEntry] = _CatalogEntry
    for top_key, candidate_model in routes:
        block = raw.get(top_key)
        if isinstance(block, dict) and block:
            entries_raw = block
            model = candidate_model
            break

    if entries_raw is None:
        # No known top-key matched — be permissive: treat the YAML's first
        # dict-valued field that isn't metadata as the entries block.
        skip = {"schema", "schema_version", "version", "units", "standard"}
        for k, v in raw.items():
            if k in skip:
                continue
            if isinstance(v, dict) and v and all(
                isinstance(x, dict) for x in v.values()
            ):
                entries_raw = v
                break

    if not entries_raw:
        return {}

    out: dict[str, _CatalogEntry] = {}
    for spec_name, row in entries_raw.items():
        if not isinstance(row, dict):
            continue
        out[str(spec_name)] = model.model_validate(row)
    return out


def list_specs(family: str) -> list[str]:
    """Return the YAML stems (no extension) available under catalogs/<family>/."""
    folder = _catalogs_root() / family
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.yaml"))


def _known_files() -> list[tuple[str, str]]:
    """All (family, name) pairs the loader should know about — walk the tree."""
    out: list[tuple[str, str]] = []
    root = _catalogs_root()
    if not root.exists():
        return out
    for family_dir in sorted(root.iterdir()):
        if not family_dir.is_dir():
            continue
        for yaml_file in sorted(family_dir.glob("*.yaml")):
            out.append((family_dir.name, yaml_file.stem))
    return out


def validate_all() -> dict[str, list[str]]:
    """Walk every catalog YAML and collect per-file error messages.

    Return shape: ``{relative_path: [error, ...]}``. A file with no errors
    maps to an empty list. The CI gate (scripts/validate_catalogs.py) treats
    any non-empty list as a failure.

    Config-style families (``_CONFIG_STYLE_FAMILIES``) are treated as a
    single-record config per file — they only need to parse as a non-empty
    top-level dict; no row-by-row entry validation is required.
    """
    report: dict[str, list[str]] = {}
    for family, name in _known_files():
        rel = f"catalogs/{family}/{name}.yaml"
        errors: list[str] = []

        if family in _CONFIG_STYLE_FAMILIES:
            raw = _read_yaml(family, name)
            if raw is None:
                errors.append(f"catalog not found or not a dict: {rel}")
            elif not raw:
                errors.append("empty config-style catalog")
            report[rel] = errors
            continue

        try:
            entries = load_typed(family, name)
        except FileNotFoundError as exc:
            errors.append(str(exc))
            report[rel] = errors
            continue
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err.get("loc", ()))
                errors.append(f"{loc}: {err.get('msg', 'invalid')}")
            report[rel] = errors
            continue
        except yaml.YAMLError as exc:
            errors.append(f"YAML parse error: {exc}")
            report[rel] = errors
            continue
        except Exception as exc:  # pragma: no cover — defensive
            errors.append(f"unexpected: {type(exc).__name__}: {exc}")
            report[rel] = errors
            continue

        # Even on success, flag empty catalogs so we notice broken routes.
        if not entries:
            errors.append("no entries resolved — check top-level key / route")
        report[rel] = errors
    return report


__all__ = [
    "ThreadMetricEntry",
    "ThreadImperialEntry",
    "DowelPinEntry",
    "BearingEntry",
    "ORingEntry",
    "ConnectorEntry",
    "MagnetEntry",
    "QiCoilEntry",
    "CoinCellEntry",
    "MembraneEntry",
    "load_typed",
    "list_specs",
    "validate_all",
]
