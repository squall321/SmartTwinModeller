"""Content-addressed on-disk cache for per-component RE results.

pillar-perf (phase-4, 2026-06-15): a 148-component assembly re-run (or a
*different* assembly that shares a component — the same M3 bolt, the same
bracket) should not re-pay the per-component reverse-engineering cost. This
helper keys a small JSON-able result dict by the component's
``geometric_signature`` (the phase-0 rounded, tolerance-banded key that two
instances of the same part already hash EQUAL under), so a cache hit skips the
whole ExtractFeatureCatalog → Plan → Execute → Diff round-trip.

Design notes
------------
* **Opt-in.** ``assembly_reverse_engineer`` passes ``cache=False`` by default,
  so the default code path is byte-identical to pre-cache behaviour. Only when
  a caller sets ``cache=True`` (and optionally ``cache_dir``) does anything
  touch disk.
* **Invalidation by signature.** The key is the tolerance-banded
  ``ComponentSignature`` (volume to 3 sig-figs, bbox extents snapped to a
  0.01 mm grid, face + edge counts). A genuine design change moves the part
  into a different band → different key → automatic miss. Sub-micron OCCT
  jitter lands in the same band → hit. There is no time-based expiry: the
  signature *is* the validity contract.
* **Crash-safe writes.** Each entry is written to a temp file in the cache
  dir and ``os.replace``-d into place, so a half-written JSON can never be
  read back as a corrupt hit.
* **Read-only-failure-safe.** Every disk op is wrapped: a locked-down or full
  cache dir degrades to "always miss / silently skip write" rather than
  raising — the assembly still completes, just without the speed-up.

The cached value is exactly the picklable dict produced by ``_run_class_re``
(``match``, ``outcome``, ``plan_steps``, ``face_count``, ``lod``,
``result_grade``, ...) minus any non-JSON content. Bodies are never cached —
only the cheap result metadata.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def default_cache_dir() -> Path:
    """Default cache root: ``<repo-or-tmp>/.re_cache``.

    Honours ``PHONE_DESIGNER_RE_CACHE_DIR`` if set, else falls back to a
    stable per-user temp location so re-runs across processes share entries.
    """
    env = os.environ.get("PHONE_DESIGNER_RE_CACHE_DIR", "").strip()
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "phone_designer_re_cache"


def signature_key(sig: Any) -> str:
    """Deterministic filesystem-safe key from a ``ComponentSignature``.

    The signature is a ``NamedTuple`` of banded scalars; ``repr`` of its tuple
    form is stable for equal signatures, so we hash that. We keep a short,
    collision-resistant hex digest as the filename stem.
    """
    import hashlib

    try:
        payload = repr(tuple(sig))
    except Exception:
        payload = repr(sig)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


class ReCache:
    """Tiny content-addressed JSON cache. Opt-in; never raises on disk error.

    Usage::

        cache = ReCache(enabled=True, cache_dir=None)
        hit = cache.get(sig)            # None on miss
        ...                             # compute result
        cache.put(sig, result)         # store for next time

    When ``enabled`` is False every method is a cheap no-op (``get`` → None,
    ``put`` → nothing), so callers need no branching.
    """

    def __init__(self, enabled: bool = False, cache_dir: str | Path | None = None):
        self.enabled = bool(enabled)
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self._dir: Path | None = None
        if self.enabled:
            self._dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                # Cache dir unusable → degrade to a disabled cache.
                self.enabled = False
                self._dir = None

    def _path_for(self, sig: Any) -> Path | None:
        if not self.enabled or self._dir is None or sig is None:
            return None
        return self._dir / f"{signature_key(sig)}.json"

    def get(self, sig: Any) -> dict | None:
        """Return the cached result dict for ``sig`` or None on miss/error."""
        p = self._path_for(sig)
        if p is None:
            return None
        try:
            if not p.exists():
                self.misses += 1
                return None
            with p.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.hits += 1
                # Mark the fan-out so the caller / report can see it was a hit.
                data = dict(data)
                data["cache_hit"] = True
                return data
        except Exception:
            pass
        self.misses += 1
        return None

    def put(self, sig: Any, result: dict) -> None:
        """Store ``result`` (JSON-able dict) under ``sig``. Silent on error."""
        p = self._path_for(sig)
        if p is None:
            return
        try:
            # JSON-sanitise: drop any non-serialisable value defensively.
            payload = {k: v for k, v in result.items() if _json_ok(v)}
            payload.pop("cache_hit", None)
            tmp = None
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._dir), suffix=".tmp"
            )
            tmp = tmp_name
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, str(p))
            self.writes += 1
        except Exception:
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def stats(self) -> dict:
        """Snapshot of cache activity for the assembly report."""
        return {
            "enabled": self.enabled,
            "dir": str(self._dir) if self._dir is not None else None,
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
        }


def _json_ok(v: Any) -> bool:
    """True if ``v`` round-trips through JSON (scalars / nested lists+dicts)."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return True
    if isinstance(v, (list, tuple)):
        return all(_json_ok(x) for x in v)
    if isinstance(v, dict):
        return all(isinstance(k, str) and _json_ok(x) for k, x in v.items())
    return False
