#!/usr/bin/env python3
"""Reject unconfirmed realtime media added to permanent main Git history."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

def verify(root):
    from prune_realtime_archive import is_protected, safe_media_id, ALLOWED_FILES
    data=root/"docs"/"data"
    raw=subprocess.check_output(["git","-C",str(root),"diff","--cached",
      "--name-only","--diff-filter=A","-z","--","docs/media/realtime_archive/"])
    staged=[Path(s.decode("utf-8","surrogateescape")) for s in raw.split(b"\0") if s]
    if not staged:
        print("[OK] No new realtime archive media added to main Git history.")
        return
    overrides_path=data/"review_overrides.json"
    overrides=json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    overrides={str(k).upper():v for k,v in overrides.items()}
    protected=set()
    for filename in ("realtime_archive.json","candidates.json"):
        payload=json.loads((data/filename).read_text())
        if not isinstance(payload,dict) or not isinstance(payload.get("candidates"),list):
            raise ValueError("Invalid candidate metadata "+filename)
        for row in payload["candidates"]:
            if isinstance(row,dict) and row.get("id") and is_protected(row,overrides):
                ident=safe_media_id(row["id"])
                if ident:
                    protected.add(ident)
    blocked=[]
    for path in staged:
        if (len(path.parts)!=5 or path.parts[:3]!=("docs","media","realtime_archive")
                or path.parts[3] not in protected or path.parts[4] not in ALLOWED_FILES):
            blocked.append(str(path))
    if blocked:
        raise ValueError("Blocked unconfirmed realtime media in main: "+", ".join(blocked[:12]))
    print(f"[OK] Only {len(staged)} new known/protected realtime media files staged.")
if __name__=="__main__":
    try:
        verify(Path(__file__).resolve().parents[1])
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:
        print("[ERROR]",exc,file=sys.stderr)
        raise SystemExit(1)
