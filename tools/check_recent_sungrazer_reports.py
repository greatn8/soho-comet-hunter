#!/usr/bin/env python3
"""Compare a strong C3 candidate against recent official Sungrazer reports.

The existing hunter report match requires at least two report observations in
a narrow time window. That misses a prior report ending shortly before a new
track starts (C0120040). This read-only check instead fits the REPORTED
trajectory, checks the fit, and projects it up to 24 hours to compare both
candidate endpoints and motion. Results are triage guidance, not definitive
identity determinations. Only official published C3 report positions are used.

No SOHO image requests; fail-open if report site is unreachable or malformed.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from urllib.request import Request, urlopen

from realtime_hunter import (
    SUNGRAZER_REPORTS, REPORT_ID_RE, parse_reports, report_transforms_1024,
    report_xy_1024, strip_html,
)

REPORT_URL = "https://sungrazer.nrl.navy.mil/plain-text-reports"
LOOKBACK_HOURS = 36.0
MAX_PROJECT_HOURS = 24.0


def report_metadata(html_text):
    matches = list(REPORT_ID_RE.finditer(html_text))
    out = {}
    for i, match in enumerate(matches):
        rid = match.group(0)
        chunk = strip_html(html_text[match.start():matches[i+1].start()
                                if i+1 < len(matches) else len(html_text)])
        if "Retracting Report" in chunk:
            continue
        typ = next((v for v in ("Potential Comet", "Known Comet Now Entering",
                                "Additional Observations", "Correction",
                                "Possible Fragment/Companion") if v in chunk), "Unspecified")
        out[rid] = typ
    return out


def report_line(points):
    """Least-squares straight line in 512px, time centered on first observation."""
    if len(points) < 2:
        return None
    ordered = sorted(points, key=lambda p:p[0])
    ref = ordered[0][0]
    times = [(t-ref).total_seconds()/3600.0 for t,_,_ in ordered]
    average_t = sum(times)/len(times)
    denominator = sum((h-average_t)**2 for h in times)
    if denominator < (1./12.)**2:
        return None
    xs = [x for _,x,_ in ordered]
    ys = [y for _,_,y in ordered]
    avg_x = sum(xs)/len(xs)
    avg_y = sum(ys)/len(ys)
    vx = sum((h-average_t)*(x-avg_x) for h,x in zip(times,xs))/denominator
    vy = sum((h-average_t)*(y-avg_y) for h,y in zip(times,ys))/denominator
    ax, ay = avg_x-vx*average_t, avg_y-vy*average_t
    errors = [math.hypot(ax+vx*h-x,ay+vy*h-y) for h,x,y in zip(times,xs,ys)]
    return {"reference":ref,"position":(ax,ay),"velocity":(vx,vy),
            "rms_px":math.sqrt(sum(e*e for e in errors)/len(errors)),
            "last":ordered[-1][0],"first":ordered[0][0]}


def candidate_line(candidate):
    track = candidate.get("track") or {}
    try:
        a = dt.datetime.strptime(candidate["first"][:13], "%Y%m%d_%H%M").replace(tzinfo=dt.timezone.utc)
        b = dt.datetime.strptime(candidate["last"][:13], "%Y%m%d_%H%M").replace(tzinfo=dt.timezone.utc)
        if b <= a:
            return None
        x0,y0,x1,y1 = (float(track[k]) for k in
                        ("first_x","first_y","last_x","last_y"))
        if not all(math.isfinite(v) for v in (x0,y0,x1,y1)):
            return None
        hours = (b-a).total_seconds()/3600.
        return {"first":a,"last":b,"x0":x0,"y0":y0,
                "vx":(x1-x0)/hours,"vy":(y1-y0)/hours}
    except (KeyError,TypeError,ValueError):
        return None


def compare_lines(c, r):
    """Check report positions and motion at both candidate endpoints."""
    if (c["last"]-r["first"]).total_seconds()/3600 < -LOOKBACK_HOURS:
        return None
    if (c["first"]-r["last"]).total_seconds()/3600 > MAX_PROJECT_HOURS:
        return None
    # Do not compare years-apart or distant orbits with long extrapolation.
    if (r["first"]-c["last"]).total_seconds()/3600 > MAX_PROJECT_HOURS:
        return None
    projected=[]
    for when in (c["first"],c["last"]):
        hours=(when-r["reference"]).total_seconds()/3600.
        x=r["position"][0]+r["velocity"][0]*hours
        y=r["position"][1]+r["velocity"][1]*hours
        c_hours=(when-c["first"]).total_seconds()/3600.
        own=(c["x0"]+c["vx"]*c_hours, c["y0"]+c["vy"]*c_hours)
        projected.append(math.hypot(x-own[0],y-own[1]))
    delta_velocity=math.hypot(c["vx"]-r["velocity"][0],
                              c["vy"]-r["velocity"][1])
    max_distance=max(projected)
    if r["rms_px"] > 10.0:
        return None
    if max_distance <= 12.0 and delta_velocity <= 2.0:
        status="LIKELY_PRIOR_REPORT"
    elif max_distance <= 25.0 and delta_velocity <= 4.0:
        status="POSSIBLE_PRIOR_REPORT"
    else:
        return None
    return {"status":status,
            "max_endpoint_distance_px_512":round(max_distance,2),
            "first_distance_px_512":round(projected[0],2),
            "last_distance_px_512":round(projected[1],2),
            "velocity_difference_px_per_hour":round(delta_velocity,2),
            "report_fit_rms_px_512":round(r["rms_px"],2),
            "latest_report_observation_utc":r["last"].isoformat().replace("+00:00","Z")}


def find_matches(candidate, reports):
    c = candidate_line(candidate)
    if not c:
        return {"status":"INSUFFICIENT_COORDINATES",
                "message":"No valid candidate endpoints: cannot check official positional reports",
                "checked_c3":0,"matches":[]}
    matches=[]
    eligible_reports=0
    for report in reports:
        for size,origin in report_transforms_1024(report):
            points=[(t,*(v/2. for v in report_xy_1024(x,y,size,origin)))
                    for t,x,y in report["points"]]
            model=report_line(points)
            if not model:
                continue
            if (c["first"]-model["last"]).total_seconds()/3600 > MAX_PROJECT_HOURS:
                continue
            if (model["first"]-c["last"]).total_seconds()/3600 > MAX_PROJECT_HOURS:
                continue
            eligible_reports+=1
            result=compare_lines(c,model)
            if result:
                result.update({"report_id":report["id"],"report_size":size,
                               "report_origin":origin,
                               "report_url":REPORT_URL,
                               "sighting_type":report.get("sighting_type","Unknown")})
                matches.append(result)
    matches.sort(key=lambda x:(0 if x["status"]=="LIKELY_PRIOR_REPORT" else 1,
                                x["max_endpoint_distance_px_512"],
                                x["velocity_difference_px_per_hour"]))
    seen=set()
    distinct=[]
    for match in matches:
        if match["report_id"] not in seen:
            seen.add(match["report_id"])
            distinct.append(match)
    return {"status":distinct[0]["status"] if distinct else "NO_CLOSE_POSITIONAL_MATCH",
            "checked_c3":eligible_reports,"matches":distinct[:5],
            "message":("Trajectory agreement is a screening result, not proof of identity. "
                       "No close match is NOT proof of a new discovery.")}


def fetch_and_check(candidate,timeout=9):
    """Return explicit unavailable result if the official report check fails."""
    try:
        request=Request(SUNGRAZER_REPORTS,headers={"User-Agent":"SOHO-Comet-Hunter/1.0",
                                                   "Cache-Control":"no-cache"})
        with urlopen(request,timeout=timeout) as response:
            page=response.read(3_000_001)
        if len(page)>3_000_000:
            raise ValueError("report response exceeds safety limit")
        html_text=page.decode("utf-8","replace")
        reports=parse_reports(html_text)
        metadata=report_metadata(html_text)
        for report in reports:
            report["sighting_type"]=metadata.get(report["id"],"Unknown")
        reports=[r for r in reports if r.get("sighting_type")!="Retracting Report"]
        if not reports:
            raise ValueError("No parseable multi-frame official C3 reports")
        result=find_matches(candidate,reports)
        result["official_url"]=REPORT_URL
        result["reports_parsed"]=len(reports)
        result["checked_at_utc"]=dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z")
        return result
    except Exception as exc:
        return {"status":"CHECK_UNAVAILABLE","checked_c3":0,"matches":[],
                "official_url":REPORT_URL,
                "message":"Official reporting site lookup failed; check manually. "
                          +str(exc)[:160]}
