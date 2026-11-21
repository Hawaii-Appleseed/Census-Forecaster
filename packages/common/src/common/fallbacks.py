"""Every time the model substitutes a built-in default for data or a computation that failed, on the record.

The model keeps running when a data file is missing or a forecast fails: a hardcoded COR vintage, a flat 4% growth
rate, the deduction parameters typed into the code. That keeps the chain from breaking, and it used to be visible only
as a log line, which the chain scripts switch off. :func:`record` puts it on the record instead:

* it prints one line to stderr (logging may be disabled) and logs a warning, once per name and process;
* :func:`recorded` returns everything recorded in this process and in the worker processes it forked, which
  ``tax_modeler.runs.write_run_manifest`` writes into the run's manifest as ``fallbacks``;
* ``scripts/build_site.py`` treats a run whose manifest lists fallbacks as stale: it is not published without
  ``--allow-stale-runs``.

Worker processes (``tax_modeler.parallel``) cannot hand their records back in memory, so with
:func:`share_with_workers` every record is also appended to a file named in an environment variable, which children
inherit. ``scripts/_forecast_common.py`` calls it when it is imported, so every chain and analysis script has its own.

This module imports nothing outside the standard library, so anything may import it.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading

ENV = "CF_FALLBACK_FILE"

logger = logging.getLogger(__name__)
_records: dict[str, str] = {}
_lock = threading.Lock()


def record(name: str, detail: str) -> None:
    """Record that ``name`` (a short stable key, e.g. ``"cor_projections:forward_targets"``) fell back to a default;
    ``detail`` says what failed and what was used instead. The first record of a name in a process wins."""
    with _lock:
        if name in _records:
            return
        _records[name] = detail
    msg = f"FALLBACK {name}: {detail}"
    print(msg, file=sys.stderr, flush=True)
    logger.warning(msg)
    path = os.environ.get(ENV)
    if path:
        with open(path, "a") as f:
            f.write(json.dumps({"name": name, "detail": detail}) + "\n")


def recorded() -> dict[str, str]:
    """Everything recorded in this process and, with :func:`share_with_workers`, in its workers; sorted by name."""
    with _lock:
        out = dict(_records)
    path = os.environ.get(ENV)
    if path and os.path.exists(path):
        with open(path) as f:
            for line in f:
                if line.strip():
                    d = json.loads(line)
                    out.setdefault(d["name"], d["detail"])
    return dict(sorted(out.items()))


def share_with_workers() -> str:
    """Give this process its own record file (named in ``CF_FALLBACK_FILE``, which forked and spawned children
    inherit) and return its path. Replaces any file a parent process set: a script's records are its own."""
    fd, path = tempfile.mkstemp(prefix=f"cf-fallbacks-{os.getpid()}-", suffix=".jsonl")
    os.close(fd)
    os.environ[ENV] = path
    return path


def reset() -> None:
    """Forget every record (tests)."""
    with _lock:
        _records.clear()
    path = os.environ.get(ENV)
    if path and os.path.exists(path):
        open(path, "w").close()


__all__ = ["ENV", "record", "recorded", "share_with_workers", "reset"]
