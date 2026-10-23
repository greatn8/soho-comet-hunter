#!/usr/bin/env python3
"""Opt-in EARLY C3 email watcher via the existing GitHub-issue email pipeline.

Reads early_candidates.json from the existing C3 hunter. Sends repository
dispatch events through authenticated gh CLI; GitHub Actions rechecks current
official reports before creating an assigned issue (and hence a GitHub email).
Never sends ntfy/phone notifications, runs CUDA or fetches SOHO imagery.

Only PRELIMINARY REVIEW notices are produced, never a discovery claim.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import sys
import time

from comet_github_alert import cli_dispatch
from early_c3_review import atomic_json, eligibility, same_early_track

UTC=dt.timezone.utc


def read_json(path,default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        return default


def recent_eligible(payload, now):
    """Validate a stored run again before dispatch (sender-side safety)."""
    if not isinstance(payload,dict) or (
        payload.get("mode")!="EARLY_REVIEW_UNCONFIRMED"
        or payload.get("report_check_status")!="CHECKED_RECENT_LIST"
        or not payload.get("notify_enabled")
        or int(payload.get("report_count") or 0)<1
    ):
        return []
    try:
        timestamp=dt.datetime.fromisoformat(
            payload["generated_at"].replace("Z","+00:00")
        )
        if timestamp.tzinfo is None:
            return []
    except (KeyError,ValueError,TypeError,AttributeError):
        return []
    age=(now-timestamp).total_seconds()
    if not -120<=age<=3600:
        return []
    result=[]
    for row in payload.get("candidates",[]):
        if not isinstance(row,dict):
            continue
        if (row.get("status")!="EARLY_ALERT_READY"
                or row.get("report_check_status")!="CHECKED_RECENT_LIST"
                or row.get("report_id")
                or row.get("early_alert_reasons")!=[]
                or row.get("review_class")!="STRONG_REVIEW"):
            continue
        # eligibility expects unmatched before status is promoted.
        original={**row,"status":"UNMATCHED"}
        if eligibility(original,reports_ok=True,now=now):
            continue
        track=row.get("track") or {}
        try:
            size=int(row["resolution"])
            if size not in (512,1024):
                continue
            fields=("first_x","first_y","last_x","last_y","vx","vy")
            if not all(k in track for k in fields):
                continue
            import math
            numbers=[float(track[k]) for k in fields]
            if not all(math.isfinite(v) for v in numbers):
                continue
            if not all(0<=v<size for v in numbers[:4]):
                continue
        except (KeyError,TypeError,ValueError):
            continue
        result.append(row)
        if len(result)>=2:
            break
    return result


def due_for_dispatch(payload, history, now):
    selected=[]
    for row in recent_eligible(payload,now):
        if any(same_early_track(row,old) for old in history+selected):
            continue
        selected.append(row)
    return selected


def process_once(project, *, dispatch=cli_dispatch, dry_run=False):
    result_dir=project/"results"/"realtime"
    state=project/"state"/"early_c3_email_dispatches.json"
    payload=read_json(result_dir/"early_candidates.json",{})
    history_data=read_json(state,{"dispatched":[]})
    history=history_data.get("dispatched",[]) if isinstance(history_data,dict) else []
    if not isinstance(history,list):
        history=[]
    eligible=due_for_dispatch(payload,history,dt.datetime.now(UTC))
    if dry_run:
        print(f"Eligible for preliminary email dispatch: {len(eligible)}",flush=True)
        for row in eligible:
            print(f"  {row['candidate']} {row['first']} -> {row['last']}",flush=True)
        return len(eligible)
    for row in eligible:
        # The GitHub receiver MUST re-fetch official reports and deny
        # notification on any unavailable or likely matching report.
        dispatch(row,event_type="soho-early-review")
        history.append(row)
        atomic_json(state,{"dispatched":history[-800:]})
        print(f"DISPATCHED EARLY UNCONFIRMED {row['candidate']} "
              f"({row['last']}) to GitHub email workflow",flush=True)
    return len(eligible)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project",type=Path,required=True)
    ap.add_argument("--interval",type=int,default=30)
    ap.add_argument("--once",action="store_true")
    ap.add_argument("--dry-run",action="store_true",
                    help="Only print eligible candidates; never dispatch")
    args=ap.parse_args()
    if not 10<=args.interval<=300:
        ap.error("--interval must be between 10 and 300 seconds")
    project=args.project.expanduser().resolve()
    lock=project/"state"/"early_c3_email_dispatches.lock"
    lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            ap.error("Early C3 email watcher already running")
        while True:
            try:
                process_once(project,dry_run=args.dry_run)
            except Exception as exc:
                print(f"WARN: early email cycle failed; retrying: {exc}",
                      file=sys.stderr,flush=True)
                if args.once:
                    return 1
            if args.once:
                break
            time.sleep(args.interval)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
