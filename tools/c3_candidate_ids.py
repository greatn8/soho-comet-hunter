#!/usr/bin/env python3
"""Issue durable, never-recycled C3 candidate numbers across CUDA runs.

The native CUDA binary's C01/C02 numbers (and old C0120001-style names) are
RUN-LOCAL and must not be presented as globally unique candidate identities.
Use a separate C9 namespace with a persistent, file-locked monotonic counter.
Review IDs (RT...) remain the durable identity of published media.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

PREFIX = "C9"
DIGITS = 12
MAX_NUMBER = 10**DIGITS - 1
COUNTER_NAME = "c3_candidate_id_counter.json"
ID_PATTERN = re.compile(r"^C9\d{12}$")


def _read_counter(path):
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("namespace") != PREFIX or
            type(value.get("last_issued")) is not int or
            not 0 <= value["last_issued"] <= MAX_NUMBER):
        raise ValueError("Invalid persistent C3 candidate number state: " + str(path))
    return value["last_issued"]


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, prefix="."+path.name+".",
                                         suffix=".tmp", delete=False) as handle:
            temp = Path(handle.name)
            json.dump(value, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        temp = None
        # Make reservation durable before candidate IDs are exposed to callers.
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _assert_no_orphaned_ids(project):
    """Never reset to zero if the counter vanished but C9 IDs still exist."""
    result_dir = project/"results"/"realtime"
    for name in ("latest_candidates.json", "latest_tracks.json"):
        path = result_dir/name
        if not path.exists():
            continue
        # Never guess state from partially parsed/corrupt files.
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("Cannot verify prior C3 IDs from " + str(path))
        if any(ID_PATTERN.fullmatch(str(row.get("candidate", "")))
               for row in rows if isinstance(row, dict)):
            raise RuntimeError("C3 counter missing but globally issued IDs exist: "
                               "recover the original counter before continuing")


def allocate_candidate_ids(project, count):
    """Reserve count unique IDs atomically; unused numbers are never reused."""
    if type(count) is not int or count < 0:
        raise ValueError("count must be a nonnegative integer")
    if count == 0:
        return []
    project = Path(project)
    directory = project/"state"
    directory.mkdir(parents=True, exist_ok=True)
    primary = directory/COUNTER_NAME
    recovery = directory/(COUNTER_NAME+".recovery")
    lockfile = directory/(COUNTER_NAME+".lock")
    with lockfile.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        last_primary = _read_counter(primary)
        last_recovery = _read_counter(recovery)
        if last_primary is None and last_recovery is None:
            _assert_no_orphaned_ids(project)
        current = max(x for x in (last_primary,last_recovery,0) if x is not None)
        if current + count > MAX_NUMBER:
            raise OverflowError("C3 candidate ID sequence exhausted; no IDs recycled")
        last = current + count
        data = {"namespace":PREFIX,"last_issued":last,"format":"C9 plus 12 digits"}
        # Recovery goes first; if a crash occurs after it, IDs are still reserved.
        _atomic_json(recovery, data)
        _atomic_json(primary, data)
        return [PREFIX+f"{i:0{DIGITS}d}" for i in range(current+1,last+1)]
