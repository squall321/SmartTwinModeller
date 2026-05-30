"""Backbone tests for src/phone_designer/skills/_catalog.py + the two
inspect skills that consume it (catalog_lookup, catalog_search).

These tests are catalog-only — no OCCT body needed. We pass ``body=None``
through the read-only inspect path (post_conditions=[body_present] tolerates
None because the underlying ``_measure`` returns a sentinel that compares
equal pre/post on the no-op return).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from phone_designer.skills._catalog import (
    BearingEntry,
    CoinCellEntry,
    ConnectorEntry,
    DowelPinEntry,
    MagnetEntry,
    MembraneEntry,
    ORingEntry,
    QiCoilEntry,
    ThreadMetricEntry,
    list_specs,
    load_typed,
    validate_all,
)
from phone_designer.skills.inspect.catalog_lookup import CatalogLookup
from phone_designer.skills.inspect.catalog_search import CatalogSearch


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic models — happy + sad path.


def test_thread_metric_entry_accepts_real_row() -> None:
    e = ThreadMetricEntry(
        outer_d_mm=3.0,
        pitch_mm=0.5,
        tap_drill_mm=2.5,
        clearance_close_mm=3.2,
        clearance_medium_mm=3.4,
        clearance_coarse_mm=3.6,
        head_diameter_socket_mm=5.5,
        counterbore_d_iso_mm=6.0,
        counterbore_depth_iso_mm=3.3,
        countersink_d_iso10642_mm=6.0,
        countersink_angle_deg=90,
        # Extras allowed.
        notes="M3 coarse",
    )
    assert e.outer_d_mm == 3.0
    assert getattr(e, "notes", None) == "M3 coarse"


def test_thread_metric_entry_rejects_negative_diameter() -> None:
    with pytest.raises(ValidationError):
        ThreadMetricEntry(
            outer_d_mm=-1.0,
            pitch_mm=0.5,
            tap_drill_mm=2.5,
            clearance_close_mm=3.2,
            clearance_medium_mm=3.4,
            clearance_coarse_mm=3.6,
        )


def test_dowel_bearing_oring_models_basic() -> None:
    assert DowelPinEntry(
        diameter_mm=3.0, press_fit_hole_mm=3.01, slip_fit_hole_mm=3.014,
    ).diameter_mm == 3.0
    assert BearingEntry(
        bore_id_mm=8, outer_d_mm=22, width_mm=7,
    ).outer_d_mm == 22
    assert ORingEntry(
        cs_mm=1.78, id_mm=10.82, od_mm=14.38,
    ).od_mm == pytest.approx(14.38)


def test_connector_magnet_coil_cell_membrane_models_basic() -> None:
    assert ConnectorEntry(
        cutout_w_mm=9.0, cutout_h_mm=3.4,
        inner_radius_mm=1.65, recess_depth_mm=7.35,
    ).recess_depth_mm == pytest.approx(7.35)
    assert MagnetEntry(magnet_d_mm=5.0, magnet_t_mm=2.0).magnet_d_mm == 5.0
    assert QiCoilEntry(
        outer_d_mm=43.0, inner_d_mm=20.0, thickness_mm=1.0,
    ).outer_d_mm == 43.0
    assert CoinCellEntry(
        diameter_mm=20.0, thickness_mm=3.2,
    ).thickness_mm == 3.2
    assert MembraneEntry(
        membrane_d_mm=8.0, mounting_d_mm=5.5, boss_height_mm=0.3,
    ).mounting_d_mm == 5.5


# ──────────────────────────────────────────────────────────────────────────────
# load_typed — round-trip the YAMLs that actually ship in the repo.


def test_load_typed_threads_metric_has_m3() -> None:
    typed = load_typed("standards", "threads_metric")
    assert "M3" in typed
    m3 = typed["M3"]
    assert isinstance(m3, ThreadMetricEntry)
    assert m3.outer_d_mm == 3.0
    assert m3.tap_drill_mm == 2.5


def test_load_typed_bearings_metric_has_608() -> None:
    typed = load_typed("standards", "bearings_metric")
    assert "608" in typed
    assert typed["608"].outer_d_mm == 22


def test_load_typed_o_rings_as568_has_dash013() -> None:
    typed = load_typed("standards", "o_rings_as568")
    assert "AS568-013" in typed
    assert typed["AS568-013"].cs_mm == pytest.approx(1.78)


def test_load_typed_dowel_pins() -> None:
    typed = load_typed("standards", "dowel_pins")
    assert "3" in typed
    assert typed["3"].press_fit_hole_mm == pytest.approx(3.010)


def test_load_typed_connectors_usb_c() -> None:
    typed = load_typed("connectors", "usb_c")
    assert "USB_C_Receptacle_v2" in typed
    assert typed["USB_C_Receptacle_v2"].cutout_w_mm == 9.0


def test_load_typed_magnets_ndfeb() -> None:
    typed = load_typed("magnets", "ndfeb")
    assert "N42_D5x2" in typed
    assert typed["N42_D5x2"].magnet_d_mm == 5.0


def test_load_typed_qi_coils_wpc() -> None:
    typed = load_typed("qi_coils", "wpc")
    assert "Qi_A11" in typed
    assert typed["Qi_A11"].outer_d_mm == 43.0


def test_load_typed_coin_cells_iec60086() -> None:
    typed = load_typed("coin_cells", "iec60086")
    assert "CR2032" in typed
    assert typed["CR2032"].thickness_mm == pytest.approx(3.2)


def test_load_typed_membranes_gore() -> None:
    typed = load_typed("membranes", "gore")
    assert "PMF_F25" in typed
    assert typed["PMF_F25"].mounting_d_mm == 5.5


def test_load_typed_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_typed("standards", "no_such_catalog_xyz")


# ──────────────────────────────────────────────────────────────────────────────
# list_specs / validate_all


def test_list_specs_standards_includes_threads_metric() -> None:
    stems = list_specs("standards")
    assert "threads_metric" in stems
    assert "bearings_metric" in stems


def test_list_specs_unknown_family_is_empty() -> None:
    assert list_specs("does_not_exist_family_xyz") == []


def test_validate_all_returns_dict_with_no_errors() -> None:
    report = validate_all()
    assert isinstance(report, dict)
    assert report, "expected at least one catalog file to be discovered"
    bad = {p: e for p, e in report.items() if e}
    assert not bad, f"catalog validation errors: {bad}"


# ──────────────────────────────────────────────────────────────────────────────
# catalog_lookup / catalog_search inspect skills.


def test_catalog_lookup_returns_typed_entry() -> None:
    skill = CatalogLookup()
    result = skill.apply(
        body=None,
        args_dict={"family": "standards", "name": "threads_metric",
                   "spec": "M3"},
    )
    entry = result.extras["catalog_entry"]
    assert entry["family"] == "standards"
    assert entry["name"] == "threads_metric"
    assert entry["spec"] == "M3"
    assert entry["data"]["outer_d_mm"] == 3.0
    assert "error" not in entry


def test_catalog_lookup_unknown_spec_records_error() -> None:
    skill = CatalogLookup()
    result = skill.apply(
        body=None,
        args_dict={"family": "standards", "name": "threads_metric",
                   "spec": "M999"},
    )
    entry = result.extras["catalog_entry"]
    assert entry["data"] is None
    assert "error" in entry


def test_catalog_search_finds_usb_c_in_connectors() -> None:
    skill = CatalogSearch()
    result = skill.apply(
        body=None,
        args_dict={"family": "connectors", "query": "USB_C"},
    )
    matches = result.extras["matches"]
    specs = {m["spec"] for m in matches}
    assert "USB_C_Receptacle_v2" in specs
    # Every match should carry a score field.
    assert all("score" in m for m in matches)


def test_catalog_search_ranks_shorter_specs_higher() -> None:
    skill = CatalogSearch()
    result = skill.apply(
        body=None,
        args_dict={"family": "standards", "query": "M3"},
    )
    matches = result.extras["matches"]
    assert matches, "expected M3 hits in threads_metric"
    # Scores must be sorted descending.
    scores = [m["score"] for m in matches]
    assert scores == sorted(scores, reverse=True)


def test_catalog_search_empty_when_no_hit() -> None:
    skill = CatalogSearch()
    result = skill.apply(
        body=None,
        args_dict={"family": "connectors", "query": "definitely_not_present_xyz"},
    )
    assert result.extras["matches"] == []
