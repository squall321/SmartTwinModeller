"""_body_store — BodyStore: the MCP session body cache with STEP-snapshot lineage.

Every ``put()`` mints a ``body_<8hex>`` id and (unless the caller already has
one) writes a STEP snapshot of the body into ``snapshot_dir``. The snapshot is
the durability layer: the store keeps at most ``max_live`` live body objects
(LRU on last-touch), but the RECORD and its STEP file stay for the whole
session, so ``get()`` on an evicted id transparently re-imports the body from
its snapshot and marks the record ``reimported: True``.

Lineage: each record remembers its ``parent_id`` + ``op_note``, so
``undo(body_id)`` steps back one op and ``lineage(body_id)`` returns the full
root..self chain — cheap, JSON-safe provenance for the MCP tools.

HONEST LIMIT — re-import is geometry-only. A body reconstructed from its STEP
snapshot loses everything that lived on the Python object: ``_pd_tags`` face
tags, build123d component/child names, labels, colors attached in-memory.
The ``reimported`` flag is STICKY on the record precisely so downstream tools
can see the body has been through this lossy round-trip; we surface the loss,
we do not hide it. (Volume/topology are preserved to STEP precision.)

Failure modes (all reachable, all structured):
  fm.bad_max_live      — max_live < 1 at construction
  fm.unknown_body_id   — get/undo/lineage on an id this store never minted,
                         or put(parent_id=...) pointing at an unknown parent
  fm.step_write_failed — the snapshot could not be written at put() time
                         (store is left unchanged — no half-registered record)
  fm.snapshot_missing  — evicted body whose snapshot file vanished from disk
  fm.step_parse_failed — snapshot file exists but is not readable STEP
"""
from __future__ import annotations

import os
import tempfile
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any


class BodyStore:
    """LRU session cache of CAD bodies with STEP-snapshot durability + lineage.

    Records are append-only for the session: eviction only drops the live
    Python object (``record['body'] = None``); the metadata and STEP file
    remain, so any id ever minted stays resolvable via re-import.
    """

    def __init__(self, max_live: int = 32, snapshot_dir: str | None = None):
        if int(max_live) < 1:
            raise ValueError(
                f"fm.bad_max_live: max_live must be >= 1, got {max_live!r}")
        self._max_live = int(max_live)
        if snapshot_dir is None:
            snapshot_dir = tempfile.mkdtemp(prefix="pd_mcp_bodies_")
        else:
            Path(snapshot_dir).mkdir(parents=True, exist_ok=True)
        self._snapshot_dir = str(snapshot_dir)
        # body_id -> record; insertion-ordered, never deleted for the session.
        self._records: dict[str, dict[str, Any]] = {}
        # LRU of ids whose record still holds a live body object.
        # Oldest-touched is first; get()/put() move an id to the end.
        self._live: "OrderedDict[str, None]" = OrderedDict()
        self._seq = 0
        self._lock = threading.RLock()

    # ── public API ────────────────────────────────────────────────────────────

    def put(self, body, step_path: str | None = None, *,
            parent_id: str | None = None, op_note: str = "") -> str:
        """Register ``body``; return its freshly minted ``body_<8hex>`` id.

        If ``step_path`` is None a STEP snapshot is written into
        ``snapshot_dir`` (plain STEPControl_Writer — geometry only). If the
        snapshot cannot be written the put is REFUSED (fm.step_write_failed)
        and the store is left unchanged: we never register a record we could
        not make durable.
        """
        with self._lock:
            if parent_id is not None and parent_id not in self._records:
                raise ValueError(self._unknown_msg(parent_id, role="parent_id"))
            body_id = self._mint_id()
            if step_path is None:
                step_path = os.path.join(self._snapshot_dir, f"{body_id}.step")
                self._write_step_snapshot(body, step_path)
            self._seq += 1
            self._records[body_id] = {
                "body": body,
                "step_path": str(step_path),
                "parent_id": parent_id,
                "op_note": str(op_note),
                "created_seq": self._seq,
                "reimported": False,
            }
            self._touch_live(body_id)
            return body_id

    def get(self, body_id: str) -> dict:
        """Resolve an id → ``{'body','body_id','step_path','parent_id','op_note','reimported'}``.

        If the live object was LRU-evicted, transparently re-import it from
        its STEP snapshot (lossy — see module docstring) and set the sticky
        extras-visible ``reimported: True``. Refreshes LRU recency.
        Everything except ``'body'`` is strict-JSON-safe.
        """
        with self._lock:
            rec = self._records.get(body_id)
            if rec is None:
                raise ValueError(self._unknown_msg(body_id))
            if rec["body"] is None:
                rec["body"] = self._reimport(body_id, rec["step_path"])
                rec["reimported"] = True
            self._touch_live(body_id)
            return {
                "body": rec["body"],
                "body_id": body_id,
                "step_path": rec["step_path"],
                "parent_id": rec["parent_id"],
                "op_note": rec["op_note"],
                "reimported": bool(rec["reimported"]),
            }

    def undo(self, body_id: str) -> str | None:
        """One step back in the lineage: the parent id, or None at a root."""
        with self._lock:
            rec = self._records.get(body_id)
            if rec is None:
                raise ValueError(self._unknown_msg(body_id))
            return rec["parent_id"]

    def lineage(self, body_id: str) -> list[dict]:
        """Root..self chain of ``{'body_id','parent_id','op_note'}`` (JSON-safe)."""
        with self._lock:
            if body_id not in self._records:
                raise ValueError(self._unknown_msg(body_id))
            chain: list[dict] = []
            seen: set[str] = set()
            cur: str | None = body_id
            while cur is not None:
                if cur in seen:  # defensive: unreachable via the public API
                    raise RuntimeError(f"BodyStore lineage cycle at {cur!r}")
                seen.add(cur)
                rec = self._records[cur]
                chain.append({"body_id": cur,
                              "parent_id": rec["parent_id"],
                              "op_note": rec["op_note"]})
                cur = rec["parent_id"]
            chain.reverse()
            return chain

    def stats(self) -> dict:
        """``{'n_records','n_live','snapshot_dir'}`` — strict-JSON-safe."""
        with self._lock:
            return {
                "n_records": len(self._records),
                "n_live": len(self._live),
                "snapshot_dir": self._snapshot_dir,
            }

    # ── internals ─────────────────────────────────────────────────────────────

    def _mint_id(self) -> str:
        while True:
            body_id = f"body_{uuid.uuid4().hex[:8]}"
            if body_id not in self._records:
                return body_id

    def _unknown_msg(self, body_id: Any, role: str = "body_id") -> str:
        return (f"fm.unknown_body_id: {role} {body_id!r} not found in this "
                f"session's BodyStore ({len(self._records)} body id(s) exist)")

    def _touch_live(self, body_id: str) -> None:
        """Mark ``body_id`` most-recently-used; evict oldest live beyond max_live."""
        rec = self._records[body_id]
        if rec["body"] is None:
            self._live.pop(body_id, None)
            return
        self._live[body_id] = None
        self._live.move_to_end(body_id)
        while len(self._live) > self._max_live:
            oldest, _ = self._live.popitem(last=False)
            self._records[oldest]["body"] = None  # record + STEP file remain

    @staticmethod
    def _write_step_snapshot(body, out_path: str) -> None:
        """Plain STEPControl_Writer snapshot (idiom from skills/io/step_export_v2)."""
        try:
            from OCP.IFSelect import IFSelect_ReturnStatus
            from OCP.STEPControl import (STEPControl_StepModelType,
                                         STEPControl_Writer)
            shape = body.wrapped if hasattr(body, "wrapped") else body
            if shape is None:
                raise ValueError("body/body.wrapped is None")
            writer = STEPControl_Writer()
            status = writer.Transfer(
                shape, STEPControl_StepModelType.STEPControl_AsIs)
            if status != IFSelect_ReturnStatus.IFSelect_RetDone:
                raise RuntimeError(f"Transfer failed (status={status})")
            write_status = writer.Write(str(out_path))
            if write_status != IFSelect_ReturnStatus.IFSelect_RetDone:
                raise RuntimeError(f"Write failed (status={write_status})")
        except Exception as exc:
            # append the raw cause — never mask it
            raise RuntimeError(
                f"fm.step_write_failed: could not write STEP snapshot to "
                f"{out_path!r} — {type(exc).__name__}: {exc}") from exc
        if not (os.path.exists(out_path) and os.path.getsize(out_path) > 0):
            raise RuntimeError(
                f"fm.step_write_failed: snapshot missing/empty after write "
                f"→ {out_path!r}")

    @staticmethod
    def _reimport(body_id: str, step_path: str):
        """STEPControl_Reader re-import (idiom from compose/_load_other step branch).

        Returns a build123d ``Solid`` (single-solid file) or ``Part``
        (compound). Geometry-only: tags/names are lost (module docstring
        HONEST LIMIT).
        """
        p = Path(step_path)
        if not p.exists():
            raise ValueError(
                f"fm.snapshot_missing: body {body_id!r} was evicted and its "
                f"STEP snapshot is gone ({step_path!r}); cannot re-import")
        try:
            from build123d import Part, Solid
            from OCP.IFSelect import IFSelect_RetDone
            from OCP.STEPControl import STEPControl_Reader
            from OCP.TopAbs import TopAbs_ShapeEnum
            reader = STEPControl_Reader()
            status = reader.ReadFile(str(p))
            if status != IFSelect_RetDone:
                raise RuntimeError(f"ReadFile failed (status={status})")
            n_roots = reader.TransferRoots()
            if n_roots < 1:
                raise RuntimeError("no transferable STEP roots")
            shape = reader.OneShape()
            # OneShape() unwraps a single-root file to a bare TopAbs_SOLID;
            # build123d's Part is a Compound whose .volume sums CHILD solids
            # (0 for a bare solid), so wrap solids as Solid explicitly.
            if shape.ShapeType() == TopAbs_ShapeEnum.TopAbs_SOLID:
                return Solid(shape)
            return Part(shape)
        except Exception as exc:
            # append the raw cause — never mask it
            raise ValueError(
                f"fm.step_parse_failed: snapshot for body {body_id!r} at "
                f"{step_path!r} is not readable STEP — "
                f"{type(exc).__name__}: {exc}") from exc
