"""Shared face-count guard for inspect/detect/RE skills.

COMPLEX-CAD pass-6 dehardcode (2026-06-09): the 16 000 face-count cap
used to live as two duplicated literals — one in classify_pockets.py and
one in extract_feature_catalog.py — which would drift apart on the next
refactor. Both now import this constant, and the value is overridable
via the ``PHONE_DESIGNER_MAX_FACE_COUNT`` environment variable so
hosts with more RAM / CPU can raise the budget without code edits.

Empirically the per-face surface-type probe in classify_pockets is O(N)
and 16 000 faces complete in ~60 s on commodity laptops; large
assemblies above the cap should be routed to
``assembly_reverse_engineer`` which decomposes via
``split_into_components``.
"""
from __future__ import annotations

import os


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else default
    except ValueError:
        return default


DEFAULT_MAX_FACE_COUNT: int = _env_int("PHONE_DESIGNER_MAX_FACE_COUNT", 16000)
"""Macro-level face-count cap for inspect + RE skills.

Override with ``PHONE_DESIGNER_MAX_FACE_COUNT=N``. Set to ``None``-equivalent
behaviour at the skill Args level (``max_face_count=None``) to disable
entirely.
"""
