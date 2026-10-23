#!/usr/bin/env python3
"""Optional ntfy alerts for narrowly vetted EARLY C3 reviews, not discoveries.

Reads only results/realtime/early_candidates.json, NEVER accesses SOHO/CUDA.
Disabled unless explicitly launched; no previously-seen/known/check-error alert.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import fcntl
import time
from pathlib import Path

from early_c3_review import same_early_track, atomic_json
from watch_strong_reviews import send_alert, topic_from_file

def read_json(path,default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError,OSError):
        return default

def early_message(row):
    p=row.get("pixel_verification") or {}
    t=row.get("track") or {}
    return (
        "EARLY UNCONFIRMED COMET-LIKE TRACK — manual review only.\n"
        f"Candidate: {row.get('candidate','?')} (run-local ID)\n"
        f"UTC frames: {row['first']} → {row['last']}\n"
        f"Coordinates in {row['resolution']}px C3 image: "
        f"({t.get('first_x')},{t.get('first_y')}) → "
        f"({t.get('last_x')},{t.get('last_y')})\n"
        f"Evidence: {p.get('hits')}/{p.get('samples')} pixel hits; "
        f"peak SNR {p.get('peak_snr')}\n"
        "No match in recently fetched Sungrazer C3 reports at check time. "
        "Check the LIVE reports and original images before any submission.\n"
        "https://greatn8.github.io/soho-comet-hunter/"
    )

def due_alerts(payload, history,now):
    if (not isinstance(payload,dict)
            or payload.get("mode")!="EARLY_REVIEW_UNCONFIRMED"
            or payload.get("report_check_status")!="CHECKED_RECENT_LIST"
            or not payload.get("notify_enabled")):
        return []
    try:
        generated=dt.datetime.fromisoformat(payload["generated_at"].replace("Z","+00:00"))
    except (KeyError,TypeError,ValueError):
        return []
    if not -120 <= (now-generated).total_seconds() <= 3600:
        return []
    selected=[]
    for row in payload.get("candidates",[]):
        if (not isinstance(row,dict)
                or row.get("status")!="EARLY_ALERT_READY"
                or row.get("report_check_status")!="CHECKED_RECENT_LIST"
                or row.get("early_alert_reasons")
                or row.get("report_id")):
            continue
        if any(same_early_track(row,old) for old in history+selected):
            continue
        selected.append(row)
        if len(selected)>=2:
            break
    return selected

def once(project,send=send_alert):
    results=project/"results"/"realtime"
    state=project/"state"/"early_c3_notifications.json"
    payload=read_json(results/"early_candidates.json",{})
    saved=read_json(state,{"sent":[]})
    sent=saved.get("sent",[]) if isinstance(saved,dict) else []
    if not isinstance(sent,list):
        sent=[]
    topic=topic_from_file(project/"state"/"comet_ntfy_topic.txt")
    count=0
    for row in due_alerts(payload,sent,dt.datetime.now(dt.timezone.utc)):
        title="SOHO EARLY REVIEW (UNCONFIRMED) "+str(row.get("candidate"))
        send(topic,title,early_message(row),priority="4")
        sent.append(row)
        atomic_json(state,{"sent":sent[-250:]})
        count+=1
    return count

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project",required=True,type=Path)
    ap.add_argument("--once",action="store_true")
    ap.add_argument("--interval",type=int,default=45)
    args=ap.parse_args()
    if not 30<=args.interval<=300:
        ap.error("interval must be 30..300 seconds")
    project=args.project.expanduser().resolve()
    lock=project/"state"/"early_c3_notifications.lock"
    lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open("a+") as f:
        try: fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Early C3 notification watcher already running")
        while True:
            try:
                count=once(project)
                if count: print(f"Sent {count} EARLY UNCONFIRMED review notifications",flush=True)
            except Exception as exc:
                print(f"Early notifier skipped cycle: {exc}",flush=True)
            if args.once:
                return
            time.sleep(args.interval)
if __name__=="__main__":
    main()
