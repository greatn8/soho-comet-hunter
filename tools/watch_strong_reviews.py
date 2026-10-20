#!/usr/bin/env python3
"""Push new SOHO STRONG_REVIEW events to the ntfy phone app.

Independent watcher: reads cached hunter results, never runs CUDA, downloads
SOHO imagery or edits the realtime detector. Python standard library only.

  python3 tools/watch_strong_reviews.py --project PROJECT --test
  python3 tools/watch_strong_reviews.py --project PROJECT
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DASHBOARD = "https://greatn8.github.io/soho-comet-hunter/"
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
FRAME_RE = re.compile(r"^(20\d{6})_(\d{4})_c3_512\.jpg$")


def frame_time(name):
    match = FRAME_RE.match(str(name))
    if not match:
        raise ValueError("Bad frame filename: " + str(name))
    return dt.datetime.strptime(
        match.group(1) + match.group(2), "%Y%m%d%H%M"
    ).replace(tzinfo=dt.timezone.utc)


def load_json(path, default):
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def topic_from_file(path):
    value = path.read_text(encoding="utf-8").strip()
    if not TOPIC_RE.fullmatch(value):
        raise ValueError("Topic must be 16-128 characters using letters, numbers, '-' or '_'")
    return value


def send_alert(topic, title, message, priority="5"):
    request = Request(
        "https://ntfy.sh/" + topic,
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": "telescope,warning",
            "Click": DASHBOARD,
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError("ntfy server returned HTTP " + str(response.status))
    except HTTPError as exc:
        raise RuntimeError(f"ntfy returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError("ntfy connection failed: " + str(exc.reason)) from exc


def signature(candidate, track):
    """Identify physical tracks separately from run-local candidate IDs."""
    try:
        last = frame_time(candidate["last"])
        return {
            "candidate": str(candidate.get("candidate", "")),
            "review_id": str(candidate.get("review_id", "")),
            "time": last.isoformat(),
            "x": float(track["last_x"]),
            "y": float(track["last_y"]),
            "vx": float(track["vx"]),
            "vy": float(track["vy"]),
        }
    except (ValueError, TypeError, KeyError):
        return {
            "candidate": str(candidate.get("candidate", "")),
            "review_id": str(candidate.get("review_id", "")),
            "time": "",
        }


def same_object(a, b):
    if a.get("review_id") and a["review_id"] == b.get("review_id"):
        return True
    # CUDA candidate IDs can change between runs. Predict position at the
    # later observation and use motion consistency to avoid repeat alerts.
    try:
        ta = dt.datetime.fromisoformat(a["time"])
        tb = dt.datetime.fromisoformat(b["time"])
        hours = (tb - ta).total_seconds() / 3600.0
        if abs(hours) > 18:
            return False
        distance = math.hypot(
            float(a["x"]) + float(a["vx"])*hours - float(b["x"]),
            float(a["y"]) + float(a["vy"])*hours - float(b["y"]),
        )
        velocity_delta = math.hypot(
            float(a["vx"]) - float(b["vx"]),
            float(a["vy"]) - float(b["vy"]),
        )
        return distance <= 8.0 and velocity_delta <= 2.0
    except (KeyError, ValueError, TypeError, OverflowError):
        return False


def active_strong_candidates(results, now, max_age_hours):
    candidates = load_json(results / "latest_candidates.json", [])
    tracks = load_json(results / "latest_tracks.json", [])
    if not isinstance(candidates, list) or not isinstance(tracks, list):
        return []
    by_id = {row.get("candidate"): row for row in tracks if isinstance(row, dict)}
    selected = []
    for item in candidates:
        if not isinstance(item, dict) or str(item.get("review_class", "")).upper() != "STRONG_REVIEW":
            continue
        try:
            age_hours = (now - frame_time(item["last"])).total_seconds()/3600.0
        except (KeyError, ValueError):
            continue
        if not -2 <= age_hours <= max_age_hours:
            continue
        selected.append((item, signature(item, by_id.get(item.get("candidate"), {}))))
    return selected


def notification_message(item):
    pixel = item.get("pixel_verification") or {}
    tm = pixel.get("temporal_motion") or {}
    return (
        f"Candidate: {item.get('candidate', '?')}\n"
        "Review: STRONG_REVIEW (NOT a confirmed comet)\n"
        f"Frames: {item.get('frames', '?')}\n"
        f"Observed UTC: {item.get('first', '?')} to {item.get('last', '?')}\n"
        f"Pixel evidence: {pixel.get('hits', '?')}/{pixel.get('samples', '?')}\n"
        f"Temporal motion: {tm.get('supported', '?')}/{tm.get('tested', '?')}\n"
        f"Internal tracking status: {item.get('status', '?')}\n"
        "Check original images and official Sungrazer reports before submitting.\n"
        + DASHBOARD
    )


def save_state(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump({"notified": entries[-1000:]}, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def process_once(results, state_file, topic, max_age_hours, send=send_alert):
    now = dt.datetime.now(dt.timezone.utc)
    data = load_json(state_file, {"notified": []})
    notified = data.get("notified", []) if isinstance(data, dict) else []
    if not isinstance(notified, list):
        notified = []
    new_count = 0
    for candidate, sig in active_strong_candidates(results, now, max_age_hours):
        if any(same_object(old, sig) for old in notified):
            continue
        title = "SOHO STRONG REVIEW " + str(candidate.get("candidate", ""))
        send(topic, title, notification_message(candidate))
        sig["sent_at"] = now.isoformat()
        notified.append(sig)
        save_state(state_file, notified)
        logging.info("SENT STRONG_REVIEW %s, observed %s to %s",
                     candidate.get("candidate"), candidate.get("first"), candidate.get("last"))
        new_count += 1
    return new_count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--topic-file", type=Path, help="Default PROJECT/state/comet_ntfy_topic.txt")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds, 10-300")
    parser.add_argument("--max-age-hours", type=float, default=36)
    parser.add_argument("--once", action="store_true", help="Check once then exit")
    parser.add_argument("--test", action="store_true", help="Send a test notification then exit")
    args = parser.parse_args(argv)
    if not 10 <= args.interval <= 300:
        parser.error("--interval must be 10..300")
    if not 1 <= args.max_age_hours <= 336:
        parser.error("--max-age-hours must be 1..336")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s UTC %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    logging.Formatter.converter = time.gmtime

    project = args.project.expanduser().resolve()
    topic_file = (args.topic_file or project/"state"/"comet_ntfy_topic.txt").expanduser().resolve()
    try:
        topic = topic_from_file(topic_file)
    except (OSError, ValueError) as exc:
        parser.error(f"Cannot read a valid ntfy topic file: {exc}")
    if args.test:
        send_alert(topic, "SOHO Hunter - test notification",
                   "Phone alerts connected. You will receive future STRONG_REVIEW candidates here.",
                   priority="3")
        print("Test notification sent. Check your ntfy app.")
        return 0

    results = project/"results"/"realtime"
    state_file = project/"state"/"strong_review_notifications.json"
    lock_file = project/"state"/"strong_review_notifications.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("A strong-review watcher is already running; exiting.", file=sys.stderr)
            return 2
        logging.info("Strong-review watcher started. Polling %s every %d seconds.",
                     results, args.interval)
        while True:
            try:
                process_once(results, state_file, topic, args.max_age_hours)
            except Exception as exc:
                logging.error("Notification check failed; will retry: %s", exc)
                if args.once:
                    return 1
            if args.once:
                break
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
