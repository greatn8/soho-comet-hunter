#!/usr/bin/env python3
"""Bounded, fail-closed, early review of the FIRST C3 CUDA window.

This is NOT a comet detector of its own and does NOT reduce the existing five
frame CUDA threshold or change the established 12/48/336-hour searches.
Runs inline after the 12-hour CUDA pass, before the slower 48/336-hour passes.

'EARLY_ALERT_READY' means a pixel-supported UNCONFIRMED review hypothesis
with a successful recent-report check and some temporal motion evidence.
It must never be described as an independently established new comet.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from bright_streak_review import bright_streak_review_evidence
from coronal_structure_review import coronal_structure_evidence
import os
from pathlib import Path


def eligibility(row, *, reports_ok, now, max_age_minutes=90):
    """Reasons a candidate may NOT be surfaced as an early phone review."""
    problems=[]
    if not reports_ok or row.get("status")!="UNMATCHED":
        problems.append("REPORT_CHECK_UNAVAILABLE_OR_MATCHED")
    if row.get("review_class")!="STRONG_REVIEW":
        problems.append("PIXEL_VERIFICATION_NOT_STRONG")
    if int(row.get("frames") or 0)<5:
        problems.append("TOO_FEW_TRACKED_FRAMES")
    if row.get("trajectory_warning"):
        problems.append("INCONSISTENT_TRAJECTORY")
    if row.get("coronal_structure_review",{}).get("status")=="LIKELY_CORONAL_STRUCTURE":
        problems.append("BROAD_CORONAL_STRUCTURE")
    if row.get("bright_streak_review",{}).get("status")=="LIKELY_STREAK_ARTIFACT":
        problems.append("BRIGHT_PLANET_STREAK")
    if row.get("edge_review",{}).get("status")=="BORDER_CONFINED":
        problems.append("BORDER_CONFINED")
    if row.get("star_field",{}).get("status")=="STAR_LIKE":
        problems.append("COMMON_STAR_MOTION")
    pixel=row.get("pixel_verification") or {}
    if (int(pixel.get("hits") or 0)<5
            or float(pixel.get("hit_fraction") or 0)<0.80
            or int(pixel.get("longest_hit_run") or 0)<4
            or float(pixel.get("peak_snr") or 0)<6.0):
        problems.append("WEAK_PIXEL_EVIDENCE")
    motion=pixel.get("temporal_motion") or {}
    if (int(motion.get("tested") or 0)<1
            or int(motion.get("supported") or 0)<1
            or float(motion.get("support_fraction") or 0)<0.5):
        problems.append("INSUFFICIENT_INDEPENDENT_MOTION")
    try:
        last=dt.datetime.strptime(row["last"][:13],"%Y%m%d_%H%M").replace(tzinfo=dt.timezone.utc)
        age=(now-last).total_seconds()/60.0
        if not -15 <= age <= max_age_minutes:
            problems.append("NOT_A_FRESH_TRACK")
    except (KeyError,ValueError,TypeError):
        problems.append("BAD_TRACK_TIME")
    return problems


def same_early_track(a,b,timeout_hours=18):
    """Stable identity based on trajectory, not reused CUDA candidate label."""
    try:
        ta=dt.datetime.strptime(a["last"][:13],"%Y%m%d_%H%M").replace(tzinfo=dt.timezone.utc)
        tb=dt.datetime.strptime(b["last"][:13],"%Y%m%d_%H%M").replace(tzinfo=dt.timezone.utc)
        elapsed=(tb-ta).total_seconds()/3600
        if abs(elapsed)>timeout_hours:
            return False
        scale_a=1024.0/float(a.get("resolution") or 1024)
        scale_b=1024.0/float(b.get("resolution") or 1024)
        ax=float(a["track"]["last_x"])*scale_a+float(a["track"]["vx"])*scale_a*elapsed
        ay=float(a["track"]["last_y"])*scale_a+float(a["track"]["vy"])*scale_a*elapsed
        bx=float(b["track"]["last_x"])*scale_b
        by=float(b["track"]["last_y"])*scale_b
        dv=math.hypot(
            float(a["track"]["vx"])*scale_a-float(b["track"]["vx"])*scale_b,
            float(a["track"]["vy"])*scale_a-float(b["track"]["vy"])*scale_b,
        )
        return math.hypot(ax-bx,ay-by)<=8 and dv<=2
    except (ValueError,TypeError,KeyError,ZeroDivisionError):
        return False


def atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
    temporary.replace(path)


def process_early_tracks(raw_candidates, frames, results_dir, args, run_id, hunter,
                         *, notify_enabled=True, report_fetch=None):
    """Early pass is allowed to fail without altering the main live detector."""
    now=dt.datetime.now(dt.timezone.utc)
    output=Path(results_dir)/"early_candidates.json"
    supported=[]
    candidates=[]
    gray_cache={}
    for c in raw_candidates:
        c["resolution"]=args.resolution
        try:
            hunter.audit_track_frames(c,frames)
            if c.get("trajectory_warning") or not hunter.comet_like(c):
                continue
        except Exception:
            continue
        candidates.append(c)
    # GPU capped independently; do not spend unbounded CPU verifying noise.
    candidates.sort(key=hunter.live_candidate_rank,reverse=True)
    for c in candidates[:60]:
        try:
            pixel=hunter.verify_track_pixels(c,frames,gray_cache=gray_cache)
            review_class,_=hunter.classify_for_review(c,pixel)
            if review_class!="STRONG_REVIEW":
                continue
            pixel["temporal_motion"]=hunter.verify_temporal_motion(
                c,frames,gray_cache=gray_cache)
            c["pixel_verification"]=pixel
            c["review_class"]=review_class
            c["edge_review"]=hunter.edge_review_evidence(c)
            c["bright_streak_review"]=bright_streak_review_evidence(
                c,frames,gray_cache,hunter.pixel_verify._decode_gray,args.resolution)
            c["coronal_structure_review"]=coronal_structure_evidence(
                c,frames,gray_cache,hunter.pixel_verify._decode_gray,args.resolution)
            supported.append(c)
        except Exception:
            continue
    hunter.annotate_star_field(supported)
    fetched_reports=[]
    report_error=None
    if supported:
        try:
            reader=report_fetch or (lambda: hunter.fetch_text(hunter.SUNGRAZER_REPORTS,60))
            fetched_reports=hunter.parse_reports(reader())
            if not fetched_reports:
                raise ValueError("No usable C3 reports parsed; cannot assert recent-report check")
        except Exception as exc:
            report_error=str(exc)
    reports_ok=bool(fetched_reports) and report_error is None
    rows=[]
    for c in supported:
        match=hunter.match_recent_report(c,fetched_reports) if reports_ok else None
        row={
            "candidate":c["cid"],"resolution":args.resolution,
            "first":c["first"]["file"],"last":c["last"]["file"],
            "frames":c["frames"],"rms":c["rms"],
            "vx":c["vx"],"vy":c["vy"],
            "score":c["score"],"raw_priority":c.get("priority"),
            "source_window_hours":c.get("source_window_hours"),
            "review_class":c.get("review_class"),
            "pixel_verification":c.get("pixel_verification",{}),
            "star_field":c.get("star_field",{}),
            "edge_review":c.get("edge_review",{}),
            "bright_streak_review":c.get("bright_streak_review",{}),
            "coronal_structure_review":c.get("coronal_structure_review",{}),
            "trajectory_warning":c.get("trajectory_warning",""),
            "report_id":match["report_id"] if match else "",
            "report_check_status":"CHECKED_RECENT_LIST" if reports_ok else "UNAVAILABLE",
            "status":"KNOWN_REPORT" if match else ("UNMATCHED" if reports_ok else "CHECK_UNAVAILABLE"),
            "track":{
                "first_x":c["first"]["x"],"first_y":c["first"]["y"],
                "last_x":c["last"]["x"],"last_y":c["last"]["y"],
                "vx":c["vx"],"vy":c["vy"],
            },
            "run_id":run_id,
            "warning":"EARLY REVIEW ONLY. This is not a confirmed comet discovery.",
        }
        row["early_alert_reasons"]=eligibility(row,reports_ok=reports_ok,now=now)
        rows.append(row)

    # Deduplicate physically overlapping hypotheses before deciding which
    # independent events qualify. Preserve every candidate for auditing.
    eligible=sorted((r for r in rows if not r["early_alert_reasons"]),
                    key=lambda r:r["pixel_verification"].get("evidence_score",0),
                    reverse=True)
    selected=[]
    for row in eligible:
        if any(same_early_track(row,other) for other in selected):
            row["early_alert_reasons"].append("SAME_TRACK_AS_HIGHER_RANKED")
            continue
        if len(selected)>=2:
            row["early_alert_reasons"].append("EARLY_ALERT_CYCLE_CAP")
            continue
        selected.append(row)
    for row in rows:
        if row in selected and notify_enabled:
            row["status"]="EARLY_ALERT_READY"
        elif row in selected and not notify_enabled:
            row["status"]="REPLAY_ONLY"
        elif row["status"]=="UNMATCHED":
            row["status"]="EARLY_REVIEW_ONLY"

    rows.sort(key=lambda r:(r["status"]!="EARLY_ALERT_READY",
                            -r["pixel_verification"].get("evidence_score",0)))
    data={
        "schema_version":1,"run_id":run_id,"generated_at":now.isoformat(),
        "newest_input_frame":frames[-1].name if frames else None,
        "mode":"EARLY_REVIEW_UNCONFIRMED",
        "report_check_status":"CHECKED_RECENT_LIST" if reports_ok else "UNAVAILABLE",
        "report_count":len(fetched_reports),
        "report_error":report_error,
        "raw_tracks":len(raw_candidates),"filtered_tracks":len(candidates),
        "strong_pixel_tracks":len(supported),
        "early_alert_ready":sum(r["status"]=="EARLY_ALERT_READY" for r in rows),
        "notify_enabled":notify_enabled,
        "candidates":rows[:60],
        "warning":"No early candidate is a confirmed comet or proof of an unreported discovery.",
    }
    atomic_json(output,data)
    return data
