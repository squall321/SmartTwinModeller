"""BodyStore — session body cache with STEP-snapshot lineage (mcp_support).

Pins: put/get round-trip, LRU eviction + transparent lossy re-import with the
sticky ``reimported`` flag, undo/lineage chains, structured fm.* refusals,
stats live-vs-records accounting, and strict-JSON safety of the metadata.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")

import pytest

from phone_designer.mcp_support._body_store import BodyStore


def _box(l=10.0, w=20.0, h=5.0):
    from build123d import Box
    return Box(l, w, h)


def _step_files(d):
    return [f for f in os.listdir(d) if f.lower().endswith((".step", ".stp"))]


# ── put / get round-trip ──────────────────────────────────────────────────────

def test_put_get_roundtrip_same_volume_and_snapshot_on_disk(tmp_path):
    store = BodyStore(snapshot_dir=str(tmp_path))
    body = _box(10, 20, 5)
    v0 = body.volume
    bid = store.put(body, op_note="box 10x20x5")

    assert bid.startswith("body_") and len(bid) == len("body_") + 8

    rec = store.get(bid)
    assert rec["body"] is body                      # live — same object back
    assert abs(rec["body"].volume - v0) <= 1e-6 * v0
    assert rec["reimported"] is False
    assert rec["parent_id"] is None
    assert rec["op_note"] == "box 10x20x5"

    # snapshot STEP exists on disk, non-empty, inside snapshot_dir
    assert os.path.dirname(rec["step_path"]) == str(tmp_path)
    assert os.path.getsize(rec["step_path"]) > 0

    # everything except 'body' is strict-JSON-safe
    meta = {k: v for k, v in rec.items() if k != "body"}
    json.dumps(meta, allow_nan=False)


def test_caller_supplied_step_path_is_reused_not_rewritten(tmp_path):
    writer_store = BodyStore(snapshot_dir=str(tmp_path / "a"))
    box = _box(6, 6, 6)
    existing = writer_store.get(writer_store.put(box))["step_path"]

    store = BodyStore(snapshot_dir=str(tmp_path / "b"))
    bid = store.put(box, step_path=existing)
    assert store.get(bid)["step_path"] == existing
    assert _step_files(str(tmp_path / "b")) == []   # no duplicate snapshot


def test_default_snapshot_dir_is_a_pd_mcp_bodies_tempdir():
    import shutil
    store = BodyStore()
    d = store.stats()["snapshot_dir"]
    try:
        assert os.path.isdir(d)
        assert "pd_mcp_bodies" in os.path.basename(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── LRU eviction + transparent re-import ──────────────────────────────────────

def test_eviction_then_get_reimports_with_flag_and_equal_volume(tmp_path):
    store = BodyStore(max_live=2, snapshot_dir=str(tmp_path))
    b1, b2, b3 = _box(10, 10, 10), _box(20, 10, 10), _box(30, 10, 10)
    v1 = b1.volume
    id1 = store.put(b1, op_note="b1")
    id2 = store.put(b2, op_note="b2")
    id3 = store.put(b3, op_note="b3")            # evicts id1 (oldest-touched)

    assert store.stats() == {"n_records": 3, "n_live": 2,
                             "snapshot_dir": str(tmp_path)}

    rec = store.get(id1)                          # transparent re-import
    assert rec["reimported"] is True
    assert rec["body"] is not b1                  # a fresh object, not the old one
    assert abs(rec["body"].volume - v1) <= 1e-6 * v1
    assert store.stats()["n_live"] == 2           # re-import respects max_live

    # the flag is STICKY: the lossy round-trip already happened
    assert store.get(id1)["reimported"] is True
    # never-evicted bodies stay live and unflagged
    assert store.get(id3)["reimported"] is False
    assert store.get(id3)["body"] is b3
    assert id2 is not None                        # record survives regardless


def test_get_refreshes_lru_recency(tmp_path):
    store = BodyStore(max_live=2, snapshot_dir=str(tmp_path))
    id_a = store.put(_box(5, 5, 5))
    id_b = store.put(_box(7, 7, 7))
    store.get(id_a)                               # A is now most-recently-used
    id_c = store.put(_box(9, 9, 9))               # must evict B, NOT A

    assert store.get(id_a)["reimported"] is False
    assert store.get(id_c)["reimported"] is False
    assert store.get(id_b)["reimported"] is True  # B was the one evicted


# ── lineage / undo ────────────────────────────────────────────────────────────

def test_undo_and_lineage_walk_root_child_grandchild(tmp_path):
    store = BodyStore(snapshot_dir=str(tmp_path))
    root = store.put(_box(10, 10, 10), op_note="root box")
    child = store.put(_box(10, 10, 8), parent_id=root, op_note="shell")
    grand = store.put(_box(10, 10, 6), parent_id=child, op_note="fillet")

    assert store.undo(grand) == child
    assert store.undo(child) == root
    assert store.undo(root) is None

    chain = store.lineage(grand)
    assert [c["body_id"] for c in chain] == [root, child, grand]  # root..self
    assert [c["parent_id"] for c in chain] == [None, root, child]
    assert [c["op_note"] for c in chain] == ["root box", "shell", "fillet"]
    json.dumps(chain, allow_nan=False)            # strict-JSON-safe

    assert store.lineage(root) == [
        {"body_id": root, "parent_id": None, "op_note": "root box"}]


# ── structured refusals ───────────────────────────────────────────────────────

def test_unknown_body_id_refused_with_token_and_count(tmp_path):
    store = BodyStore(snapshot_dir=str(tmp_path))
    store.put(_box())
    store.put(_box())
    for call in (store.get, store.undo, store.lineage):
        with pytest.raises(ValueError, match=r"fm\.unknown_body_id") as ei:
            call("body_deadbeef")
        assert "2 body id(s) exist" in str(ei.value)


def test_unknown_parent_id_refused(tmp_path):
    store = BodyStore(snapshot_dir=str(tmp_path))
    with pytest.raises(ValueError, match=r"fm\.unknown_body_id") as ei:
        store.put(_box(), parent_id="body_00000000")
    assert "parent_id" in str(ei.value)
    assert store.stats()["n_records"] == 0        # refused put leaves no record


def test_unwritable_body_refused_and_store_unchanged(tmp_path):
    store = BodyStore(snapshot_dir=str(tmp_path))
    with pytest.raises(RuntimeError, match=r"fm\.step_write_failed"):
        store.put(object())                       # not a shape → snapshot fails
    assert store.stats() == {"n_records": 0, "n_live": 0,
                             "snapshot_dir": str(tmp_path)}


def test_snapshot_missing_after_eviction_refused(tmp_path):
    store = BodyStore(max_live=1, snapshot_dir=str(tmp_path))
    id1 = store.put(_box(4, 4, 4))
    os.remove(store._records[id1]["step_path"])   # simulate a vanished snapshot
    store.put(_box(5, 5, 5))                      # evicts id1's live body
    with pytest.raises(ValueError, match=r"fm\.snapshot_missing"):
        store.get(id1)


def test_corrupt_snapshot_refused_as_parse_failed(tmp_path):
    store = BodyStore(max_live=1, snapshot_dir=str(tmp_path))
    id1 = store.put(_box(4, 4, 4))
    with open(store._records[id1]["step_path"], "w", encoding="ascii") as fh:
        fh.write("this is not a STEP file\n")     # corrupt in place
    store.put(_box(5, 5, 5))                      # evicts id1's live body
    with pytest.raises(ValueError, match=r"fm\.step_parse_failed"):
        store.get(id1)


def test_bad_max_live_refused():
    with pytest.raises(ValueError, match=r"fm\.bad_max_live"):
        BodyStore(max_live=0)


# ── stats accounting ──────────────────────────────────────────────────────────

def test_stats_counts_live_vs_records_and_is_json_safe(tmp_path):
    store = BodyStore(max_live=2, snapshot_dir=str(tmp_path))
    assert store.stats() == {"n_records": 0, "n_live": 0,
                             "snapshot_dir": str(tmp_path)}
    ids = [store.put(_box(4 + i, 4, 4)) for i in range(4)]
    s = store.stats()
    assert s["n_records"] == 4 and s["n_live"] == 2
    json.dumps(s, allow_nan=False)
    store.get(ids[0])                             # re-import: still capped
    assert store.stats()["n_live"] == 2
    assert store.stats()["n_records"] == 4        # records never dropped
    assert len(_step_files(str(tmp_path))) == 4   # one snapshot per record
