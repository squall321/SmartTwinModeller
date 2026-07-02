"""_guarded_exec — hang-proof plan execution in a WARM WORKER subprocess.

WHY: one hung OCCT call (ThruSections on a bad wire, a pathological fillet)
must not kill — or freeze forever — the MCP stdio server. This module runs
the spec pipeline (GenerateFromSpec, or STEP-import + PlanExecutor for
modify) inside a single PERSISTENT child process ("warm worker") and enforces
a hard wall-clock timeout on every call. On timeout the worker is KILLED and
lazily respawned on the next call, so the server survives with at most one
lost operation.

Public API (what mcp_server.py wires):

    run_guarded_plan(spec, *, initial_step_path=None, out_dir, name="part",
                     timeout_s=None) -> dict
        ok True  -> {"ok": True, "step_path": <path|None>, "generated": {...}}
                    "ok" means the guarded pipeline RAN TO COMPLETION; the
                    honest per-step build grading (is_solid / n_ok / steps /
                    spec_errors) lives inside "generated" — a spec whose steps
                    all failed still returns ok=True with generated["ok"]=False
                    and step_path=None (mirrors cad_generate's partial/error
                    statuses).
        ok False -> {"ok": False, "error": "fm.timeout: ..."}       (hang)
                    {"ok": False, "error": "fm.worker_crash: ..."}  (died)
                    {"ok": False, "error": "fm.exec_error: ..."}    (raised)
        Caller-misuse (non-list spec, non-finite numbers, missing out_dir,
        negative timeout) raises ValueError("fm.bad_args: ...").

    shutdown_worker()
        Kill the warm worker (tests / server shutdown). Safe to call anytime.

Timeout resolution: explicit ``timeout_s`` wins; None reads env
``PHONE_DESIGNER_SKILL_TIMEOUT_S`` (default 120). ``timeout_s == 0`` runs
INLINE in this process (no worker, no protection) — the test/dev escape hatch
AND the automatic fallback when the worker cannot be spawned at all.

Worker protocol: JSON-lines over stdin/stdout. The worker steals its real
stdout fd for protocol writes (sentinel-prefixed lines) and points fd 1 at
devnull, so C-level OCCT chatter (STEPControl transfer stats etc.) can never
corrupt a response line. It imports phone_designer.skills.export_manifest
ONCE at startup (the ~30-40 s registry warm-up), then answers each request in
~seconds.

Test-only ops (guarded by env PHONE_DESIGNER_ALLOW_TEST_OPS=1, refused
otherwise): a spec step {"op": "__test_sleep__", "args": {"seconds": N}}
sleeps inside the worker (deterministic timeout proof); {"op": "__test_exit__",
"args": {"code": N}} hard-exits the worker (crash-recovery proof). Inline mode
REFUSES __test_exit__ — it would kill the server process itself.
"""
from __future__ import annotations

import atexit
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

# ── constants ─────────────────────────────────────────────────────────────────

#: Prefix on every protocol line the worker emits. Anything without it
#: (stray prints, OCCT noise that escapes the fd redirect) is ignored.
_SENTINEL = "@GUARDED_EXEC@ "

_DEFAULT_TIMEOUT_S = 120.0
_DEFAULT_STARTUP_TIMEOUT_S = 300.0  # warm import of ~389 skills ≈ 30-40 s

#: OCCT / warning noise dropped from stderr tails in crash diagnostics.
_NOISE_RE = re.compile(
    r"WorkSession|Transfer|Statistics|INFO|class |Deprecation|UserWarning")

_TEST_OPS = ("__test_sleep__", "__test_exit__")


class _WorkerTimeout(Exception):
    """Internal: no response within timeout_s."""


class _WorkerCrash(Exception):
    """Internal: worker died / pipe broke before a response arrived."""


# ── JSON safety ───────────────────────────────────────────────────────────────


def _json_safe(obj: Any) -> Any:
    """Recursively coerce to strict-JSON values: non-finite floats -> None,
    unknown objects -> str. json.dumps(..., allow_nan=False) is guaranteed to
    pass on the result."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    return str(obj)


def _safe_name(name: str) -> str:
    """Filename-safe part name (no separators / traversal)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "")).strip("._")
    return cleaned[:80] or "part"


# ── the pipeline itself (SHARED by inline mode and the worker loop) ───────────


def _write_step(body: Any, path: str) -> bool:
    """STEP export (same idiom as mcp_server._write_step, duplicated here so
    the worker never imports the FastMCP server module)."""
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    shape = body.wrapped if hasattr(body, "wrapped") else body
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    w.Write(path)
    return os.path.exists(path) and os.path.getsize(path) > 0


def _handle_test_ops(spec: list[dict], *, in_worker: bool) -> list[dict]:
    """Strip + execute the env-guarded test-only ops; return the real spec."""
    real: list[dict] = []
    for item in spec:
        op = (item or {}).get("op") or (item or {}).get("skill")
        if op not in _TEST_OPS:
            real.append(item)
            continue
        if os.environ.get("PHONE_DESIGNER_ALLOW_TEST_OPS") != "1":
            raise ValueError(
                f"fm.exec_error: test op '{op}' requires "
                "PHONE_DESIGNER_ALLOW_TEST_OPS=1")
        targs = item.get("args") or {}
        if op == "__test_sleep__":
            time.sleep(float(targs.get("seconds", 0.0)))
        else:  # __test_exit__
            if in_worker:
                os._exit(int(targs.get("code", 3)))
            raise ValueError(
                "fm.exec_error: __test_exit__ refused outside the worker "
                "(it would kill the server process)")
    return real


def _execute_payload(payload: dict, *, in_worker: bool) -> dict:
    """Run ONE request. Never raises — every failure lands in
    {"ok": False, "error": "fm.exec_error: ..."} (raw error appended, never
    masked). The success dict is _json_safe by construction."""
    try:
        spec = payload.get("spec") or []
        out_dir = os.path.abspath(payload["out_dir"])
        name = _safe_name(payload.get("name") or "part")
        initial = payload.get("initial_step_path") or None
        os.makedirs(out_dir, exist_ok=True)

        real_spec = _handle_test_ops(spec, in_worker=in_worker)

        body0 = None
        if initial:
            from phone_designer.skills.create.import_step import ImportStep
            body0 = ImportStep().apply(None, {"path": initial}).body

        # GenerateFromSpec IS the "mirror generate_from_spec's Plan/Step
        # building + registry pre-check" — reused verbatim for both lanes so
        # the guarded path can never drift from the front door: it registers
        # the whole library, pre-checks each op, schema-validates args,
        # isolates per-step failures via PlanExecutor(initial_body=body0),
        # and grades the result (is_solid = TopAbs_SOLID AND volume>0).
        from phone_designer.skills.create.generate_from_spec import (
            GenerateFromSpec,
        )
        res = GenerateFromSpec().apply(
            body0, {"spec": real_spec, "plan_name": name})
        generated = dict(res.extras.get("generated") or {})
        generated.pop("_step_metrics", None)
        generated["mode"] = "modify" if initial else "generate"

        step_path = None
        body = res.body
        if body is not None:
            candidate = os.path.join(out_dir, f"{name}.step")
            try:
                if _write_step(body, candidate):
                    step_path = candidate
                else:
                    generated.setdefault("spec_errors", []).append(
                        "step_export_failed: writer produced no file")
            except Exception as exc:  # noqa: BLE001
                generated.setdefault("spec_errors", []).append(
                    f"step_export_failed: {type(exc).__name__}: "
                    f"{str(exc)[:200]}")

        return _json_safe(
            {"ok": True, "step_path": step_path, "generated": generated})
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or type(exc).__name__
        if msg.startswith("fm."):
            err = msg  # already a structured refusal — pass through intact
        else:
            err = f"fm.exec_error: {type(exc).__name__}: {msg[:300]}"
        return {"ok": False, "error": err}


# ── worker side (child process entry: `-m ... --worker`) ─────────────────────


def _worker_main() -> int:
    """Persistent request loop. One JSON line in -> one sentinel line out."""
    os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")

    # Steal the real stdout for the protocol; point fd 1 at devnull so
    # C-level OCCT writes (STEP transfer stats, ...) can't corrupt responses.
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1,
                      newline="\n")
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_fd, 1)

    def _send(obj: dict) -> None:
        proto.write(_SENTINEL
                    + json.dumps(_json_safe(obj), ensure_ascii=False,
                                 allow_nan=False)
                    + "\n")
        proto.flush()

    t0 = time.perf_counter()
    try:
        # THE warm-up: registers all ~389 skills once; every later request
        # pays only its own OCCT time.
        import phone_designer.skills.export_manifest  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        _send({"ready": False,
               "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        return 1
    _send({"ready": True, "warm_s": round(time.perf_counter() - t0, 1)})

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            _send({"ok": False,
                   "error": f"fm.exec_error: bad request line: {exc}"})
            continue
        _send(_execute_payload(payload, in_worker=True))
    return 0


# ── parent side: warm-worker handle + watchdog ────────────────────────────────


class _WorkerHandle:
    """One live worker subprocess + its reader/drain threads."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=5)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        try:
            for raw in self.proc.stdout:  # type: ignore[union-attr]
                line = raw.strip()
                if line.startswith(_SENTINEL):
                    self._lines.put(line[len(_SENTINEL):])
                # everything else = noise, dropped
        except Exception:  # noqa: BLE001 — pipe closed on kill
            pass
        self._lines.put(None)  # EOF marker

    def _read_stderr(self) -> None:
        try:
            for raw in self.proc.stderr:  # type: ignore[union-attr]
                line = raw.strip()
                if line and not _NOISE_RE.search(line):
                    self._stderr_tail.append(line[:200])
        except Exception:  # noqa: BLE001
            pass

    def stderr_tail(self) -> str:
        return " | ".join(self._stderr_tail)[:300]

    def wait_ready(self, startup_timeout_s: float) -> None:
        """Block until the worker's ready line (raises on failure)."""
        try:
            line = self._lines.get(timeout=startup_timeout_s)
        except queue.Empty:
            self.kill()
            raise RuntimeError(
                f"worker not ready within {startup_timeout_s:g}s")
        if line is None:
            rc = self.proc.poll()
            self.kill()
            raise RuntimeError(
                f"worker died during warm-up (exit={rc}) {self.stderr_tail()}")
        msg = json.loads(line)
        if not msg.get("ready"):
            self.kill()
            raise RuntimeError(f"worker warm-up failed: {msg.get('error')}")

    def request(self, payload_line: str, timeout_s: float) -> dict:
        """Send one pre-serialized request line; wait for one response."""
        try:
            self.proc.stdin.write(payload_line + "\n")  # type: ignore[union-attr]
            self.proc.stdin.flush()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            raise _WorkerCrash(
                f"could not send request ({type(exc).__name__}: {exc}) "
                f"{self.stderr_tail()}")
        try:
            line = self._lines.get(timeout=timeout_s)
        except queue.Empty:
            raise _WorkerTimeout()
        if line is None:
            raise _WorkerCrash(
                f"worker exited (code={self.proc.poll()}) "
                f"{self.stderr_tail()}")
        try:
            return json.loads(line)
        except Exception as exc:  # noqa: BLE001
            raise _WorkerCrash(f"unparseable response line: {exc}")

    def alive(self) -> bool:
        return self.proc.poll() is None

    def kill(self) -> None:
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass


def _repo_paths() -> tuple[Path, Path]:
    """(repo_root, src_dir) derived from this file's location."""
    here = Path(__file__).resolve()
    src = here.parents[2]        # .../src
    return src.parent, src


def _spawn_worker() -> _WorkerHandle:
    """Spawn + warm one worker. Raises on any startup failure (the caller
    falls back to inline execution)."""
    repo, src = _repo_paths()
    env = os.environ.copy()
    env["PHONE_DESIGNER_UI_HEADLESS"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    prev = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{prev}" if prev else str(src)
    creationflags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     if os.name == "nt" else 0)
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m",
         "phone_designer.mcp_support._guarded_exec", "--worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=str(repo),
        env=env,
        creationflags=creationflags,
    )
    handle = _WorkerHandle(proc)
    startup_s = float(
        os.environ.get("PHONE_DESIGNER_WORKER_STARTUP_TIMEOUT_S",
                       str(_DEFAULT_STARTUP_TIMEOUT_S))
        or _DEFAULT_STARTUP_TIMEOUT_S)
    handle.wait_ready(startup_s)
    return handle


_LOCK = threading.RLock()
_HANDLE: _WorkerHandle | None = None


def shutdown_worker() -> None:
    """Kill the warm worker (if any). Next run_guarded_plan respawns."""
    global _HANDLE
    with _LOCK:
        if _HANDLE is not None:
            _HANDLE.kill()
            _HANDLE = None


atexit.register(shutdown_worker)


# ── public entry ──────────────────────────────────────────────────────────────


def run_guarded_plan(
    spec: list[dict],
    *,
    initial_step_path: str | None = None,
    out_dir: str,
    name: str = "part",
    timeout_s: float | None = None,
) -> dict:
    """Execute a spec (generate) or STEP+spec (modify) hang-proof.

    See the module docstring for the full contract. Summary:
      - timeout_s None  -> env PHONE_DESIGNER_SKILL_TIMEOUT_S (default 120)
      - timeout_s 0     -> INLINE (no worker; also the spawn-failure fallback,
                           flagged via a "warnings" entry)
      - timeout_s > 0   -> warm worker; kill+lazy-respawn on timeout
    """
    if not isinstance(spec, list) or any(
            not isinstance(s, dict) for s in spec):
        raise ValueError(
            "fm.bad_args: spec must be a list of {op, args} objects")
    if not out_dir or not str(out_dir).strip():
        raise ValueError("fm.bad_args: out_dir is required")
    if timeout_s is None:
        timeout_s = float(
            os.environ.get("PHONE_DESIGNER_SKILL_TIMEOUT_S",
                           str(_DEFAULT_TIMEOUT_S)) or _DEFAULT_TIMEOUT_S)
    timeout_s = float(timeout_s)
    if timeout_s < 0 or not math.isfinite(timeout_s):
        raise ValueError(
            f"fm.bad_args: timeout_s must be finite and >= 0, got {timeout_s}")

    payload = {
        "spec": spec,
        "initial_step_path": initial_step_path,
        "out_dir": str(out_dir),
        "name": name,
    }
    try:
        # Serialize ONCE, strictly — a NaN inside a spec arg is refused here
        # instead of poisoning the pipe protocol.
        payload_line = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except ValueError as exc:
        raise ValueError(
            f"fm.bad_args: spec is not strict-JSON-serializable ({exc})")

    if timeout_s == 0:
        return _execute_payload(payload, in_worker=False)

    global _HANDLE
    with _LOCK:
        if _HANDLE is None or not _HANDLE.alive():
            try:
                _HANDLE = _spawn_worker()
            except Exception as exc:  # noqa: BLE001
                # Worker unavailable — degrade honestly to inline (no hang
                # protection) rather than refusing all modelling.
                _HANDLE = None
                res = _execute_payload(payload, in_worker=False)
                res.setdefault("warnings", []).append(
                    f"fm.worker_spawn_failed: {type(exc).__name__}: "
                    f"{str(exc)[:300]} (ran inline, NO hang protection)")
                return res
        try:
            return _HANDLE.request(payload_line, timeout_s)
        except _WorkerTimeout:
            _HANDLE.kill()
            _HANDLE = None  # lazy respawn on the next call
            return {
                "ok": False,
                "error": (f"fm.timeout: op exceeded {timeout_s:g}s "
                          "(worker respawned)"),
            }
        except _WorkerCrash as exc:
            _HANDLE.kill()
            _HANDLE = None
            return {"ok": False, "error": f"fm.worker_crash: {exc}"}


if __name__ == "__main__":
    if "--worker" in sys.argv[1:]:
        sys.exit(_worker_main())
    print("usage: python -m phone_designer.mcp_support._guarded_exec --worker",
          file=sys.stderr)
    sys.exit(2)
