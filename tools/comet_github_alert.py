#!/usr/bin/env python3
"""Fast local GitHub notification relay plus GitHub Actions issue/email handler.

The local watcher reads the live hunter JSON every 30 seconds and dispatches
strong candidates through the authenticated GitHub CLI. A GitHub Actions bot
then creates/assigns an issue, allowing GitHub notification emails. A separate
push-triggered GitHub Actions path catches candidates missed by the watcher.

No SOHO requests, CUDA processes or modifications to the hunter.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import Request, urlopen

REPO = "greatn8/soho-comet-hunter"
ASSIGNEE = "greatn8"
DASHBOARD = "https://greatn8.github.io/soho-comet-hunter/"
FILE_RE = re.compile(r"^(20\d{6})_(\d{4})_c3_(?:512|1024)\.jpg$")
MARKER = "<!-- SOHO_STRONG_REVIEW_ALERT "


def utc_of_frame(value):
    match = FILE_RE.match(str(value))
    if not match:
        raise ValueError(f"Bad C3 filename: {value!r}")
    return dt.datetime.strptime("".join(match.groups()), "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def normalize(candidate, track=None):
    track = track if isinstance(track, dict) else candidate.get("track")
    track = track if isinstance(track, dict) else {}
    return {
        "candidate": str(candidate.get("candidate") or ""),
        "review_id": str(candidate.get("review_id") or ""),
        "review_class": str(candidate.get("review_class") or ""),
        "status": str(candidate.get("status") or ""),
        "first": str(candidate.get("first") or ""),
        "last": str(candidate.get("last") or ""),
        "frames": candidate.get("frames"),
        "resolution": int(candidate.get("resolution") or (1024 if "_c3_1024.jpg" in str(candidate.get("first", "")) else 512)),
        "pixel_verification": candidate.get("pixel_verification") or {},
        "official_report_check": candidate.get("official_report_check") or {},
        "submission_frames": candidate.get("submission_frames") or [],
        "submission_frame_status": candidate.get("submission_frame_status") or "",
        "track": {
            k: track.get(k) for k in ("first_x", "first_y", "last_x", "last_y", "vx", "vy")
            if k in track
        },
    }


def is_recent_strong(candidate, hours=12):
    if candidate.get("review_class") != "STRONG_REVIEW":
        return False
    if candidate.get("status") == "KNOWN_REPORT":
        return False
    try:
        age = (dt.datetime.now(dt.timezone.utc) - utc_of_frame(candidate["last"])).total_seconds()/3600.0
        return -2 <= age <= hours
    except (KeyError, ValueError):
        return False


def unique_key(candidate):
    # Review ID stays stable for repeated processing of the same endpoints.
    return candidate.get("review_id") or (
        candidate.get("candidate", "") + ":" + candidate.get("first", "") + ":" + candidate.get("last", "")
    )


def same_physical_object(a, b):
    if a.get("review_id") and a["review_id"] == b.get("review_id"):
        return True
    ta, tb = a.get("track") or {}, b.get("track") or {}
    try:
        at, bt = utc_of_frame(a["last"]), utc_of_frame(b["last"])
        hours = (bt-at).total_seconds()/3600.
        if abs(hours) > 18:
            return False
        ax = float(ta["last_x"]) + float(ta["vx"])*hours
        ay = float(ta["last_y"]) + float(ta["vy"])*hours
        distance = math.hypot(ax-float(tb["last_x"]), ay-float(tb["last_y"]))
        dv = math.hypot(float(ta["vx"])-float(tb["vx"]),
                        float(ta["vy"])-float(tb["vy"]))
        return distance <= 8 and dv <= 2
    except (KeyError, ValueError, TypeError):
        return False


def cli_dispatch(candidate=None, test=False):
    payload = {"event_type": "soho-strong-review", "client_payload": (
        {"test": True} if test else {"candidate": candidate}
    )}
    result = subprocess.run(
        ["gh", "api", "-X", "POST", f"repos/{REPO}/dispatches", "--input", "-"],
        input=json.dumps(payload), text=True, capture_output=True, timeout=25
    )
    if result.returncode:
        raise RuntimeError("GitHub dispatch failed: " + (result.stderr or result.stdout)[-500:])


def watch(project, interval, age_hours, test):
    if test:
        cli_dispatch(test=True)
        print("Test event queued at GitHub. Check Actions and your GitHub email notifications.")
        return
    results = project/"results"/"realtime"
    state_file = project/"state"/"strong_github_alerts.json"
    lock_file = project/"state"/"strong_github_alerts.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("GitHub comet watcher already running.")
        print("Watching for new strong reviews. Ctrl-C to stop.", flush=True)
        while True:
            try:
                state = read_json(state_file, {"dispatched": []})
                dispatched = state.get("dispatched", [])
                if not isinstance(dispatched, list):
                    dispatched = []
                candidates = read_json(results/"latest_candidates.json", [])
                tracks = read_json(results/"latest_tracks.json", [])
                if not isinstance(candidates, list) or not isinstance(tracks, list):
                    raise ValueError("Hunter candidate and track files must be JSON arrays")
                by_event = {
                    (x.get("candidate"), x.get("first"), x.get("last")): x
                    for x in tracks if isinstance(x, dict)
                }
                for raw in candidates:
                    if not isinstance(raw, dict):
                        continue
                    key = (raw.get("candidate"), raw.get("first"), raw.get("last"))
                    candidate = normalize(raw, by_event.get(key))
                    if not is_recent_strong(candidate, age_hours):
                        continue
                    if any(same_physical_object(old, candidate) or
                           unique_key(old)==unique_key(candidate) for old in dispatched):
                        continue
                    try:
                        from extract_submission_frames import extract_submission_rows
                        candidate["submission_frames"] = extract_submission_rows(project, candidate)
                        candidate["submission_frame_status"] = "Review all approximate pixel coordinates"
                    except Exception as frame_error:
                        candidate["submission_frame_status"] = "Frame extraction unavailable: " + str(frame_error)[:180]
                    # Check official reports before sending; do not suppress alerts.
                    from check_recent_sungrazer_reports import fetch_and_check
                    candidate["official_report_check"] = fetch_and_check(candidate)
                    cli_dispatch(candidate)
                    dispatched.append(candidate)
                    tmp = state_file.with_name(state_file.name+".tmp")
                    tmp.write_text(json.dumps({"dispatched": dispatched[-800:]}, indent=2)+"\n",
                                   encoding="utf-8")
                    tmp.replace(state_file)
                    print(f"DISPATCHED {candidate['candidate']} {candidate['last']} "
                          "to GitHub Actions", flush=True)
            except Exception as exc:
                print(f"Alert watcher error, retrying: {exc}", file=sys.stderr, flush=True)
            time.sleep(interval)


def github_api(method, endpoint, token, data=None):
    body = None if data is None else json.dumps(data).encode("utf-8")
    request = Request(
        "https://api.github.com" + endpoint,
        data=body,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "SOHO-Comet-Strong-Review-Notifier",
                 **({"Content-Type": "application/json"} if body is not None else {})},
        method=method)
    with urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def existing_alerts(token):
    """Read recent GitHub issues, including closed ones, for duplicate protection."""
    found = []
    for page in range(1, 7):
        batch = github_api("GET",
                           f"/repos/{REPO}/issues?state=all&per_page=100&page={page}",
                           token)
        if not isinstance(batch, list):
            break
        for issue in batch:
            body = str(issue.get("body") or "")
            if not body.startswith(MARKER):
                continue
            first_line = body.split("\n", 1)[0]
            encoded = first_line[len(MARKER):].removesuffix(" -->")
            try:
                record = json.loads(encoded)
                if isinstance(record, dict):
                    record["_issue_number"] = issue.get("number")
                    found.append(record)
            except ValueError:
                pass
        if len(batch) < 100:
            break
    return found


def endpoint_track_valid(track):
    """Only display finite, on-image C3 coordinates as detector estimates."""
    if not isinstance(track, dict):
        return False
    try:
        resolution = int(track.get("resolution", 512))
        return all(math.isfinite(float(track[key])) and 0 <= float(track[key]) < resolution
                   for key in ("first_x", "first_y", "last_x", "last_y"))
    except (TypeError, ValueError, KeyError):
        return False


def report_position_rows(candidate):
    """Use image-supported rows first; otherwise return CUDA fitted endpoints."""
    rows = candidate.get("submission_frames") or []
    if isinstance(rows, list) and rows:
        return [row for row in rows if isinstance(row, dict)][:64]
    track = dict(candidate.get("track") or {})
    track["resolution"] = int(candidate.get("resolution", 512))
    if not endpoint_track_valid(track):
        return []
    try:
        first = utc_of_frame(candidate["first"])
        last = utc_of_frame(candidate["last"])
    except (ValueError, KeyError):
        return []
    return [
        {"date": first.strftime("%Y-%m-%d"), "time": first.strftime("%H:%M"),
         "x": round(float(track["first_x"]), 1), "y": round(float(track["first_y"]), 1),
         "basis": "CUDA fitted endpoint (verify)"},
        {"date": last.strftime("%Y-%m-%d"), "time": last.strftime("%H:%M"),
         "x": round(float(track["last_x"]), 1), "y": round(float(track["last_y"]), 1),
         "basis": "CUDA fitted endpoint (verify)"},
    ]


def issue_details(candidate, test=False):
    if test:
        return ("[TEST] SOHO comet notification email",
                "This is a test from the SOHO Comet Hunter alert system. "
                "It is NOT a comet detection.\n\n"
                "If you received a GitHub notification email, the alert channel works.\n")
    pixel = candidate.get("pixel_verification") or {}
    motion = pixel.get("temporal_motion") or {}
    candidate_id = candidate.get("candidate") or "UNKNOWN"
    review_id = candidate.get("review_id") or ""
    link = DASHBOARD
    if review_id:
        link = (f"https://raw.githubusercontent.com/{REPO}/live-media/"
                f"{review_id}/zoom_annotated.mp4")
    track = dict(candidate.get("track") or {})
    resolution = int(candidate.get("resolution", 512))
    track["resolution"] = resolution
    endpoints = ""
    if endpoint_track_valid(track):
        endpoints = (
            f"- First CUDA fitted XY ({resolution}, top-left): ({track['first_x']}, {track['first_y']})\n"
            f"- Last CUDA fitted XY ({resolution}, top-left): ({track['last_x']}, {track['last_y']})\n"
        )
    report_check = candidate.get("official_report_check") or {}
    status_check = str(report_check.get("status") or "CHECK_UNAVAILABLE")
    report_text = "\n### Official Sungrazer reports cross-check\n\n"
    report_text += f"- Status: **{status_check}**\n"
    report_text += f"- Parsed official reports: {report_check.get('reports_parsed', 'unknown')}\n"
    if report_check.get("checked_at_utc"):
        report_text += f"- Checked at: {report_check['checked_at_utc']}\n"
    for entry in (report_check.get("matches") or [])[:3]:
        report_text += (f"- **{entry['report_id']}**: {entry['status']}; "
                        f"max endpoint difference {entry['max_endpoint_distance_px_512']} "
                        "pixels (512 scale), "
                        f"velocity difference {entry['velocity_difference_px_per_hour']} "
                        f"pixels/hour; type {entry.get('sighting_type','Unknown')}\n")
    report_text += (f"- [Check official reports]({report_check.get('official_url','https://sungrazer.nrl.navy.mil/plain-text-reports')})\n"
                    "- This is a preliminary trajectory comparison. A missing or "
                    "nonmatching report does NOT guarantee discovery priority.\n")
    if report_check.get("message"):
        report_text += "- Note: " + str(report_check["message"])[:220] + "\n"

    rows = report_position_rows(candidate)
    if rows:
        row_text = (f"\n### Frame positions for SOHO report (UTC, {resolution}x{resolution}, upper-left origin)\n\n"
                    "| Frame Time | X Pos | Y Pos | Basis |\n"
                    "|---|---:|---:|---|\n")
        for row in rows:
            row_text += (f"| {row.get('date', '')} {row.get('time', '')} | "
                         f"{row.get('x', '')} | {row.get('y', '')} | "
                         f"{row.get('basis', '')} |\n")
        row_text += ("\n**Verify every position against the original SOHO images.** "
                     "CUDA endpoints are fitted detector values; intermediate positions "
                     "are temporal pixel estimates, not independently verified astrometry. "
                     f"Use the coordinates only with the corresponding {resolution}x{resolution} images.\n")
    else:
        row_text = ("\n### Frame positions\n"
                    "No detector endpoints or independently supported per-frame "
                    "positions were available. Do not invent coordinates.\n")
    if candidate.get("submission_frame_status"):
        row_text += f"\nPosition extraction: {str(candidate['submission_frame_status'])[:240]}\n"
    body = (
        f"@{ASSIGNEE} **Investigate promptly — candidate, NOT confirmed discovery.**\n\n"
        f"- Candidate: **{candidate_id}**\n"
        f"- Review class: **STRONG_REVIEW**\n"
        f"- Tracking status: {candidate.get('status') or 'unknown'}\n"
        f"- First: {candidate.get('first')} (UTC)\n"
        f"- Last: {candidate.get('last')} (UTC)\n"
        f"- Tracked frames: {candidate.get('frames')}\n"
        f"- Pixel support: {pixel.get('hits')}/{pixel.get('samples')}\n"
        f"- Temporal-motion support: {motion.get('supported')}/{motion.get('tested')}\n"
        + endpoints + report_text + row_text +
        f"\n[Watch review video]({link}) (may not be published yet)\n"
        f"[Open live dashboard]({DASHBOARD})\n\n"
        "Check actual SOHO images and existing Sungrazer reports before claiming discovery.\n"
    )
    return (f"☄ SOHO STRONG_REVIEW: {candidate_id}", body)


def issue_body_and_metadata(candidate):
    """Embed coordinate coverage for reliable duplicate enrichment."""
    title, body = issue_details(candidate)
    track = candidate.get("track") or {}
    marker = {k: candidate.get(k) for k in
              ("review_id", "candidate", "first", "last")}
    marker["track"] = track if isinstance(track, dict) else {}
    marker["resolution"] = candidate.get("resolution", 512)
    marker["position_row_count"] = len(report_position_rows(candidate))
    return title, MARKER + json.dumps(marker, separators=(",", ":")) + " -->\n" + body


def run_issue(event_path, event_name, token, age_hours):
    event = read_json(event_path, {})
    if event_name == "repository_dispatch":
        payload = event.get("client_payload") or {}
        if payload.get("test"):
            title, body = issue_details({}, test=True)
            github_api("POST", f"/repos/{REPO}/issues", token,
                       {"title": title, "body": body, "assignees": [ASSIGNEE]})
            print("Created test notification issue.")
            return
        raw = payload.get("candidate") or {}
        choices = [raw] if isinstance(raw, dict) else []
    else:
        published = read_json(Path("docs/data/realtime.json"), {})
        choices = (published.get("review_candidates") or published.get("latest_candidates") or [])
    recent = [normalize(c) for c in choices
              if isinstance(c, dict) and is_recent_strong(c, age_hours)]
    # Dashboard-push fallback performs its own report check too.
    from check_recent_sungrazer_reports import fetch_and_check
    for candidate in recent:
        if not candidate.get("official_report_check"):
            candidate["official_report_check"] = fetch_and_check(candidate)
    old = existing_alerts(token)
    created = 0
    upgraded = 0
    for candidate in recent:
        matching = next((prior for prior in old if
                         same_physical_object(prior, candidate) or
                         unique_key(prior) == unique_key(candidate)), None)
        title, body = issue_body_and_metadata(candidate)
        row_count = len(report_position_rows(candidate))
        if matching:
            try:
                prior_count = int(matching.get("position_row_count") or 0)
            except (TypeError, ValueError):
                prior_count = 0
            issue_number = matching.get("_issue_number")
            # The dashboard may provide coordinates AFTER the direct alert,
            # or the direct dispatch may follow an initially sparse fallback.
            # Upgrade that SAME issue, never create a duplicate discovery alert.
            if issue_number and row_count > prior_count:
                github_api("PATCH", f"/repos/{REPO}/issues/{issue_number}",
                           token, {"body": body})
                github_api("POST", f"/repos/{REPO}/issues/{issue_number}/comments",
                           token, {"body":
                                   f"@{ASSIGNEE} Position table updated: {row_count} "
                                   "candidate coordinates are now included above. "
                                   "Open the issue to inspect the frame times, X/Y "
                                   "values and evidence basis. Verify all positions "
                                   "on the original SOHO images before submission."})
                matching["position_row_count"] = row_count
                upgraded += 1
                print(f"Upgraded existing alert issue #{issue_number}: "
                      f"{row_count} coordinate rows")
            continue
        issue = github_api("POST", f"/repos/{REPO}/issues", token,
                           {"title": title, "body": body, "assignees": [ASSIGNEE]})
        print(f"Created assigned alert issue #{issue.get('number')} "
              f"for {candidate['candidate']} with {row_count} coordinate rows")
        old.append({**candidate, "_issue_number": issue.get("number"),
                    "position_row_count": row_count})
        created += 1
    print(f"New strong-review notification issues: {created}; enriched: {upgraded}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--age-hours", type=float, default=12)
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--issue", action="store_true")
    args = parser.parse_args()
    if not 10 <= args.interval <= 300:
        parser.error("--interval must be 10 to 300 seconds")
    if not 1 <= args.age_hours <= 36:
        parser.error("--age-hours must be 1 to 36")
    if args.issue:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            parser.error("GitHub Actions token is required")
        run_issue(Path(os.environ.get("GITHUB_EVENT_PATH", "")),
                  os.environ.get("GITHUB_EVENT_NAME", ""),
                  token, args.age_hours)
    else:
        if args.project is None:
            parser.error("--project is required for the local watcher")
        watch(args.project.expanduser().resolve(), args.interval, args.age_hours, args.test)


if __name__ == "__main__":
    main()
