#!/usr/bin/env python3
"""Isolated NOAA CCOR1/CCOR2 experimental realtime motion hunter.

NO C3 files, GPU processes, SOHO fetch quotas, existing alerts, or detector
binaries are changed. Tracks are engineering review hypotheses only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import signal
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import numpy as np
from ccor_motion_consistency import annotated_track
from PIL import Image

BASE = "https://services.swpc.noaa.gov/products"
CAMERAS = ("ccor1", "ccor2")
STAMP_RE = re.compile(r"(20\d{6}_\d{4}_(ccor[12])_\d+by\d+\.jpg)", re.I)
HEADERS = {"User-Agent": "SOHO-Comet-Hunter-CCOR/0.1 (research; 15-minute polls)"}
STOP = False

def utcnow():
    return dt.datetime.now(dt.timezone.utc)

def iso(value):
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def stamp(name):
    return dt.datetime.strptime(name[:13], "%Y%m%d_%H%M").replace(tzinfo=dt.timezone.utc)

def get(url, limit=4_000_000):
    request = Request(url, headers=HEADERS)
    with urlopen(request, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Remote response exceeds allowed size")
    return data

def discover(camera, now, lookback_hours=8, fetch=get):
    """Use NOAA's public JPEG index; never guess remote image filenames."""
    assert camera in CAMERAS
    base = f"{BASE}/{camera}/"
    errors = []
    entries = {}
    for index in ("jpegs.json", "jpegs/"):
        try:
            text = fetch(urljoin(base, index)).decode("utf-8", "replace")
            for match in STAMP_RE.finditer(text):
                name, product = match.groups()
                if product.lower() != camera:
                    continue
                when = stamp(name)
                if now - dt.timedelta(hours=lookback_hours) <= when <= now + dt.timedelta(minutes=15):
                    entries[name] = {
                        "name": name,
                        "time": iso(when),
                        "url": urljoin(base + "jpegs/", name),
                    }
            if entries:
                break
        except Exception as exc:
            errors.append(f"{index}: {exc}")
    return sorted(entries.values(), key=lambda row: row["name"]), errors

def fetch_new(camera, cache, now, fetch=get, max_new=40):
    cache.mkdir(parents=True, exist_ok=True)
    listed, errors = discover(camera, now, fetch=fetch)
    new = 0
    for row in listed:
        if STOP or new >= max_new:
            break
        dest = cache / row["name"]
        if dest.exists():
            continue
        try:
            raw = fetch(row["url"], limit=1_500_000)
            if len(raw) < 2500 or raw[:2] != b"\xff\xd8":
                raise ValueError("JPEG signature or size invalid")
            temp = dest.with_name(dest.name + ".part")
            temp.write_bytes(raw)
            try:
                with Image.open(temp) as image:
                    image.verify()
                temp.replace(dest)
                new += 1
            finally:
                temp.unlink(missing_ok=True)
        except Exception as exc:
            errors.append(f"{row['name']}: {exc}")
    for old in cache.glob("*.jpg"):
        try:
            if stamp(old.name) < now - dt.timedelta(hours=14):
                old.unlink()
        except ValueError:
            pass
    return listed, new, errors

def load_frames(cache, camera, now, hours=5):
    paths = sorted(
        (p for p in cache.glob(f"*_{camera}_*by*.jpg")
         if now - dt.timedelta(hours=hours) <= stamp(p.name) <= now),
        key=lambda p: p.name
    )[-25:]
    frames, times, sizes = [], [], []
    for path in paths:
        try:
            with Image.open(path) as source:
                gray = source.convert("L")
                original = gray.size
                # Preserve aspect ratio; coordinates can be scaled to the JPEG.
                factor = min(1.0, 512.0 / original[0])
                scaled = (round(original[0]*factor), round(original[1]*factor))
                gray = gray.resize(scaled, Image.Resampling.BOX)
                arr = np.asarray(gray, dtype=np.float32)
            if frames and arr.shape != frames[0].shape:
                continue
            frames.append(arr)
            times.append(stamp(path.name))
            sizes.append(original)
        except (OSError, ValueError):
            continue
    return frames, times, sizes

# CCOR1's diagonal black occulter support has bright moving edge artifacts.
# Coordinates are in a normalized 512x512 JPEG detector plane, NOT FITS
# sky coordinates. The polygon deliberately includes a roughly 20-pixel
# safety margin around the arm seen in the source NOAA quicklook images.
# CCOR2 has a different image geometry and MUST NOT inherit this CCOR1 mask.
# This is an engineering veto, not a comet-orbit selection rule.
CCOR1_SUPPORT_ARM_EXCLUSION_512 = (
    (195, 260), (304, 260), (310, 300), (266, 385),
    (210, 482), (123, 482), (141, 425), (162, 364),
)


# CCOR2 has a different, upward-pointing support structure and adjacent
# vertical stripe artifacts. This shape is traced from the displayed NOAA
# CCOR2 quicklook and expanded modestly around its bright arm edges.
# It is NOT a measured solar/sky coordinate calibration.
CCOR2_SUPPORT_ARM_EXCLUSION_512 = (
    (192, 50), (290, 50), (289, 86), (288, 120),
    (298, 167), (305, 217), (297, 247),
    (218, 247), (205, 220), (208, 180),
    (216, 147), (211, 109), (202, 80),
)

SUPPORT_ARM_EXCLUSIONS_512 = {
    "ccor1": CCOR1_SUPPORT_ARM_EXCLUSION_512,
    "ccor2": CCOR2_SUPPORT_ARM_EXCLUSION_512,
}

def _inside_polygon(x, y, vertices):
    """Point-in-polygon for numpy grids and individual coordinates."""
    result = np.zeros(np.broadcast_shapes(np.shape(x), np.shape(y)), dtype=bool)
    for i, (xi, yi) in enumerate(vertices):
        xj, yj = vertices[i - 1]
        if yi == yj:
            continue
        crosses = ((yi > y) != (yj > y)) & (
            x < (xj - xi) * (y - yi) / (yj - yi) + xi
        )
        result ^= crosses
    return result


def support_arm_mask(camera, shape):
    """Mask camera-specific occulter support and edge-artifact regions."""
    height, width = shape
    vertices = SUPPORT_ARM_EXCLUSIONS_512.get(camera)
    if vertices is None:
        return np.zeros(shape, dtype=bool)
    yy, xx = np.ogrid[:height, :width]
    return _inside_polygon(
        xx * (512.0 / width), yy * (512.0 / height), vertices,
    )


def track_hits_support_arm(row):
    """Veto tracks near the arm, including old retained pre-mask tracks."""
    vertices = SUPPORT_ARM_EXCLUSIONS_512.get(row.get("camera"))
    if vertices is None:
        return False
    shape = row.get("processing_shape") or [512, 512]
    try:
        width, height = (float(shape[0]), float(shape[1]))
        if not (width > 0 and height > 0):
            return False
        raw = row.get("points") or []
        coords = [(float(p["x"]), float(p["y"])) for p in raw]
        if not coords:
            coords = [(float(row["x_first"]), float(row["y_first"])),
                      (float(row["x_last"]), float(row["y_last"]))]
        for x, y in coords:
            if bool(_inside_polygon(
                x * 512.0 / width, y * 512.0 / height,
                vertices,
            )):
                return True
        # Check between observations too, so a track crossing the arm
        # is excluded even when individual recorded peaks lie outside it.
        for (ax, ay), (bx, by) in zip(coords, coords[1:]):
            for alpha in (0.25, 0.5, 0.75):
                x = ax + alpha * (bx - ax)
                y = ay + alpha * (by - ay)
                if bool(_inside_polygon(
                    x * 512.0 / width, y * 512.0 / height,
                    vertices,
                )):
                    return True
    except (KeyError, ValueError, TypeError, IndexError, OverflowError):
        return False
    return False


# The NOAA quicklook includes a bright UTC date/time legend at bottom left.
# Pixel values can change each frame and mimic a moving point source.
# The normalized 512-pixel exclusion covers only the legend plus a margin:
# x = [0,205), y = [466,512). Unlike support arms this is shared by both
# cameras. It is an engineering JPEG overlay mask, NOT celestial geometry.
TIMESTAMP_OVERLAY_EXCLUSION_512 = (0.0, 466.0, 205.0, 512.0)


def timestamp_overlay_mask(camera, shape):
    """Exclude NOAA quicklook timestamp text on both CCOR camera feeds."""
    h, w = shape
    if camera not in CAMERAS:
        return np.zeros(shape, dtype=bool)
    left, top, right, bottom = TIMESTAMP_OVERLAY_EXCLUSION_512
    yy, xx = np.ogrid[:h, :w]
    return ((xx * 512.0 / w >= left) & (xx * 512.0 / w < right) &
            (yy * 512.0 / h >= top) & (yy * 512.0 / h < bottom))


def track_hits_timestamp_overlay(row):
    """Remove existing hypotheses touching the changing date/time legend."""
    if row.get("camera") not in CAMERAS:
        return False
    shape = row.get("processing_shape") or [512, 512]
    try:
        width, height = float(shape[0]), float(shape[1])
        if width <= 0 or height <= 0:
            return False
        points = row.get("points") or [
            {"x": row["x_first"], "y": row["y_first"]},
            {"x": row["x_last"], "y": row["y_last"]},
        ]
        coords = [(float(p["x"]) * 512.0 / width,
                   float(p["y"]) * 512.0 / height) for p in points]
        for i, (x, y) in enumerate(coords):
            if _timestamp_contains(x, y):
                return True
            if i:
                a, b = coords[i-1], coords[i]
                for alpha in (0.25, 0.5, 0.75):
                    if _timestamp_contains(
                        a[0] + alpha * (b[0] - a[0]),
                        a[1] + alpha * (b[1] - a[1]),
                    ):
                        return True
    except (IndexError, KeyError, TypeError, ValueError, OverflowError):
        return False
    return False


def _timestamp_contains(x, y):
    left, top, right, bottom = TIMESTAMP_OVERLAY_EXCLUSION_512
    return left <= x < right and top <= y < bottom


def peaks(frame, baseline, camera, limit=70):
    """Robust positive temporal residual point sources; provisional masks."""
    residual = frame - baseline
    h, w = residual.shape
    yy, xx = np.ogrid[:h, :w]
    # These are conservative engineering masks, NOT astrometric calibration.
    radius_fraction = {"ccor1": 0.19, "ccor2": 0.16}[camera]
    mask = ((xx-w/2)**2 + (yy-h/2)**2) >= (min(w,h)*radius_fraction)**2
    mask[:int(h*.04)] = False
    mask[int(h*.96):] = False
    mask[:, :int(w*.04)] = False
    mask[:, int(w*.96):] = False
    mask &= ~(support_arm_mask(camera, (h, w)) |
              timestamp_overlay_mask(camera, (h, w)))
    inside = residual[mask]
    if inside.size == 0:
        return []
    med = float(np.median(inside))
    mad = float(np.median(np.abs(inside - med))) * 1.4826
    threshold = max(10.0, med + 6.0*max(1.0, mad))
    strong = (residual >= threshold) & mask
    # Require a strict local maximum to suppress extended structures.
    for dy, dx in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
        neighbor = np.roll(np.roll(residual,dy,axis=0),dx,axis=1)
        strong &= (residual > neighbor) if (dy,dx) in ((-1,0),(0,-1),(-1,-1),(-1,1)) else (residual >= neighbor)
    ys, xs = np.nonzero(strong)
    if not len(xs):
        return []
    values = residual[ys, xs]
    top = np.argsort(values)[-limit:][::-1]
    return [(float(xs[i]),float(ys[i]),float(values[i])) for i in top]

def match_track(a, b, time_a, time_b, tolerance=4.0):
    """Same-instrument association; pixels are never compared across cameras."""
    if a["camera"] != b["camera"]:
        return False
    try:
        t1 = dt.datetime.fromisoformat(a["first"].replace("Z","+00:00"))
        t2 = dt.datetime.fromisoformat(b["first"].replace("Z","+00:00"))
        x1 = a["x_first"] + a["vx_px_h"] * ((max(t1,t2)-t1).total_seconds()/3600)
        y1 = a["y_first"] + a["vy_px_h"] * ((max(t1,t2)-t1).total_seconds()/3600)
        x2 = b["x_first"] + b["vx_px_h"] * ((max(t1,t2)-t2).total_seconds()/3600)
        y2 = b["y_first"] + b["vy_px_h"] * ((max(t1,t2)-t2).total_seconds()/3600)
        return (math.hypot(x1-x2,y1-y2) <= tolerance
                and math.hypot(a["vx_px_h"]-b["vx_px_h"],a["vy_px_h"]-b["vy_px_h"]) <= 3.5
                and abs((t1-t2).total_seconds()) <= 4*3600)
    except (KeyError, TypeError, ValueError):
        return False

def track_points(frames, times, sizes, camera, *, complete_search=False,
                 return_diagnostics=False):
    """Run the established CCOR seed finder.

    Legacy calls retain the original first-40 behavior exactly. Opt-in
    complete_search processes all selected peaks first, then globally ranks
    and returns only the best 40. It NEVER increases dashboard/alert volume.
    A finite 5000-hypothesis safety budget returns scan_completed=False; the
    caller must not publish those partially ranked results as complete.
    """
    stats = {"complete_search": bool(complete_search),
             "scan_completed": False, "stop_reason": None,
             "seed_pairs_examined": 0, "distinct_hypotheses": 0,
             "selected_hypotheses": 0, "per_frame_peak_cap": 70,
             "distinct_hypothesis_budget": 5000 if complete_search else 40}

    def result(rows, complete, reason=None):
        stats["scan_completed"] = complete
        stats["stop_reason"] = reason
        stats["distinct_hypotheses"] = len(rows)
        chosen = (sorted(rows, key=track_quality, reverse=True)[:40]
                  if complete_search else rows)
        stats["selected_hypotheses"] = len(chosen)
        return (chosen, stats) if return_diagnostics else chosen

    if len(frames) < 6:
        return result([], True)
    baseline = np.median(np.stack(frames), axis=0)
    # Search recent frames only; the current window is independently rechecked.
    start = max(0, len(frames)-16)
    spots = [peaks(frame,baseline,camera) for frame in frames[start:]]
    ts = times[start:]
    results = []
    for i in range(len(spots)-3):
        if not spots[i] or not spots[i+1]:
            continue
        step = (ts[i+1]-ts[i]).total_seconds()/3600
        if not 0.12 <= step <= 0.65:
            continue
        for x0,y0,v0 in spots[i]:
            for x1,y1,v1 in spots[i+1]:
                stats["seed_pairs_examined"] += 1
                speed = math.hypot(x1-x0,y1-y0)/step
                if not 0.7 <= speed <= 35:
                    continue
                vx, vy = (x1-x0)/step,(y1-y0)/step
                points = [(ts[i],x0,y0,v0),(ts[i+1],x1,y1,v1)]
                for j in range(i+2,len(spots)):
                    elapsed = (ts[j]-ts[i]).total_seconds()/3600
                    px, py = x0+vx*elapsed, y0+vy*elapsed
                    nearby = [(math.hypot(x-px,y-py),x,y,v) for x,y,v in spots[j]
                              if math.hypot(x-px,y-py) <= 2.5 + .25*elapsed]
                    if nearby:
                        _,x,y,v = min(nearby)
                        points.append((ts[j],x,y,v))
                if len(points) < 4 or (points[-1][0]-points[0][0]).total_seconds() < 2700:
                    continue
                hh = np.array([(p[0]-points[0][0]).total_seconds()/3600 for p in points])
                xs = np.array([p[1] for p in points])
                ys = np.array([p[2] for p in points])
                fx = np.polyfit(hh,xs,1)
                fy = np.polyfit(hh,ys,1)
                rms = float(np.sqrt(np.mean((xs-np.polyval(fx,hh))**2 +
                                             (ys-np.polyval(fy,hh))**2)))
                if rms > 1.8:
                    continue
                h,w = frames[0].shape
                cx,cy=w/2,h/2
                r0=math.hypot(xs[0]-cx,ys[0]-cy)
                r1=math.hypot(xs[-1]-cx,ys[-1]-cy)
                sunward=(r0-r1)/max(.01,hh[-1])
                # Do not discard non-sunward motions before calibration.
                digest=hashlib.sha1(f"{camera}:{iso(points[0][0])}:{round(xs[0])}:{round(ys[0])}".encode()).hexdigest()[:12]
                row={
                    "id": f"{camera.upper()}-{digest}", "camera":camera,
                    "review_class":"UNVERIFIED_MOTION",
                    "first":iso(points[0][0]), "last":iso(points[-1][0]),
                    "frames":len(points), "x_first":round(float(xs[0]),1),
                    "y_first":round(float(ys[0]),1), "x_last":round(float(xs[-1]),1),
                    "y_last":round(float(ys[-1]),1),
                    "vx_px_h":round(float(fx[0]),2),"vy_px_h":round(float(fy[0]),2),
                    "speed_px_h":round(float(math.hypot(fx[0],fy[0])),2),
                    "sunward_px_h":round(sunward,2),
                    "rms_px":round(rms,2),
                    "processing_shape":[w,h],"source_jpeg_shape":list(sizes[start]),
                    "source":"NOAA SWPC calibrated, background-subtracted L3 JPEG",
                    "points":[{"time":iso(t),"x":round(x,1),"y":round(y,1)}
                              for t,x,y,_ in points],
                }
                if not any(match_track(old,row,None,None) for old in results):
                    results.append(row)
                if not complete_search and len(results) >= 40:
                    return result(results, False, "LEGACY_FIRST_40")
                if complete_search and len(results) >= 5000:
                    return result(results, False, "COMPLETE_SEARCH_BUDGET_EXCEEDED")
    return result(results, True)

def track_quality(row):
    """Prefer repeated, longer, lower-error evidence; not a comet probability."""
    try:
        start=dt.datetime.fromisoformat(row["first"].replace("Z","+00:00"))
        end=dt.datetime.fromisoformat(row["last"].replace("Z","+00:00"))
        hours=max(0.0,(end-start).total_seconds()/3600.0)
    except (KeyError,TypeError,ValueError):
        hours=0.0
    return (min(int(row.get("frames") or 0),20),
            min(hours,8.0),
            -float(row.get("rms_px") or 1000),
            row.get("last") or "")


def integrate_events(previous, detected, camera, now):
    """Consolidate only same-camera hypotheses, retaining stable unique IDs.

    Older versions accidentally allowed multiple rows sharing an ID. No
    pixel coordinates are ever compared between CCOR1 and CCOR2.
    """
    cutoff=iso(now-dt.timedelta(hours=48))
    retained=[]
    id_index={}
    for row in previous:
        if (row.get("camera")!=camera or row.get("last","")<cutoff or
                track_hits_support_arm(row) or track_hits_timestamp_overlay(row)):
            continue
        ident=row.get("id")
        if not ident:
            continue
        if ident in id_index:
            pos=id_index[ident]
            if track_quality(row)>track_quality(retained[pos]):
                retained[pos]=dict(row)
            continue
        id_index[ident]=len(retained)
        retained.append(dict(row))
    for row in detected:
        if track_hits_support_arm(row) or track_hits_timestamp_overlay(row):
            continue
        match_index=next((i for i,old in enumerate(retained)
                          if match_track(old,row,None,None)),None)
        if match_index is not None:
            prior=retained[match_index]
            if track_quality(row)>track_quality(prior):
                revised=dict(row)
                revised["id"]=prior["id"]
                retained[match_index]=revised
            continue
        ident=row.get("id")
        if ident in id_index:
            pos=id_index[ident]
            if track_quality(row)>track_quality(retained[pos]):
                retained[pos]=dict(row)
            continue
        id_index[ident]=len(retained)
        retained.append(dict(row))
    return sorted(retained,key=lambda x:x.get("last",""),reverse=True)[:100]


# A short track can be a comet. These limits are DISPLAY-ONLY and do
# not delete detections or constitute a physical comet classification.
# In 512-pixel detector coordinates, sub-5-pixel apparent displacement
# is hard to distinguish from quantization and slowly drifting stars.
REVIEW_MIN_DISPLACEMENT_PX_512 = 6.0
REVIEW_MIN_DURATION_HOURS = 1.5
REVIEW_MIN_OBSERVATIONS = 5


def review_motion_evidence(row):
    """Time span and endpoint displacement for display-only CCOR triage.

    The pixel coordinates are normalized to 512-wide image plane so the
    evidence thresholds remain consistent if a resized working width changes.
    Missing or inconsistent data is never invented.
    """
    try:
        shape = row.get("processing_shape") or [512, 512]
        width = float(shape[0])
        if not math.isfinite(width) or width <= 0:
            raise ValueError("Bad processing image width")
        x0 = float(row["x_first"])
        y0 = float(row["y_first"])
        x1 = float(row["x_last"])
        y1 = float(row["y_last"])
        displacement = math.hypot(x1 - x0, y1 - y0) * 512.0 / width
        start = dt.datetime.fromisoformat(str(row["first"]).replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(str(row["last"]).replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Missing UTC time zone")
        hours = (end - start).total_seconds() / 3600.0
        count = int(row["frames"])
        if not math.isfinite(displacement) or hours < 0 or count < 0:
            raise ValueError("Invalid displacement, elapsed hours or frame count")
    except (ValueError, KeyError, TypeError, IndexError, OverflowError):
        return {
            "status": "UNASSESSABLE", "displacement_px_512": None,
            "duration_hours": None, "minimum_displacement_px_512":
                REVIEW_MIN_DISPLACEMENT_PX_512,
        }

    measurable = (displacement >= REVIEW_MIN_DISPLACEMENT_PX_512 and
                  hours >= REVIEW_MIN_DURATION_HOURS and
                  count >= REVIEW_MIN_OBSERVATIONS)
    return {
        "status": "MEASURABLE_MOTION" if measurable else "LIMITED_MOTION_EVIDENCE",
        "displacement_px_512": round(displacement, 2),
        "duration_hours": round(hours, 2),
        "observation_count": count,
        "minimum_displacement_px_512": REVIEW_MIN_DISPLACEMENT_PX_512,
        "minimum_duration_hours": REVIEW_MIN_DURATION_HOURS,
        "minimum_observations": REVIEW_MIN_OBSERVATIONS,
    }


# Earlier stages are valuable for timely manual review. They are not
# confirmed comets, and early reviews are less reliable than mature tracks.
EARLY_MIN_FRAMES = 4
EARLY_MIN_HOURS = .75
EARLY_MIN_DISPLACEMENT_PX_512 = 4.0
EARLY_MAX_RMS_PX = 1.5


def early_motion_review(row):
    """Separate early-evidence eligibility from established motion evidence."""
    established = review_motion_evidence(row)
    if established["status"] == "MEASURABLE_MOTION":
        return "ESTABLISHED"
    if established["status"] == "UNASSESSABLE":
        return "INSUFFICIENT"
    try:
        displacement = established["displacement_px_512"]
        hours = established["duration_hours"]
        frames = established["observation_count"]
        rms = float(row["rms_px"])
        if (displacement >= EARLY_MIN_DISPLACEMENT_PX_512 and
                hours >= EARLY_MIN_HOURS and frames >= EARLY_MIN_FRAMES and
                math.isfinite(rms) and 0 <= rms <= EARLY_MAX_RMS_PX):
            return "EARLY"
    except (KeyError, TypeError, ValueError, OverflowError):
        return "INSUFFICIENT"
    return "INSUFFICIENT"


def review_shortlist(tracks, limit_per_camera=12):
    """Mix freshly appearing tracks with strong mature tracks for timely review.

    This is display-only: retain every non-masked hypothesis locally. Fill up
    to half the quota from newest acceptable tracks, the remainder by quality.
    Apply spatial diversity with a fallback if too few distinct cells exist.
    """
    selected = []
    for camera in CAMERAS:
        rows = [r for r in tracks if r.get("camera") == camera
                and (r.get("motion_consistency") or {}).get("status") != "IRREGULAR_MOTION"
                and early_motion_review(r) in ("EARLY", "ESTABLISHED")]
        newest = sorted(rows, key=lambda r: (
            r.get("last", ""), r.get("first", ""), track_quality(r)), reverse=True)
        strongest = sorted(rows, key=lambda r: (
            early_motion_review(r) == "ESTABLISHED", track_quality(r),
            r.get("last", "")), reverse=True)

        chosen, counts, seen = [], {}, set()

        def add(row, relaxed=False):
            ident = row.get("id")
            if not ident or ident in seen or len(chosen) >= limit_per_camera:
                return False
            shape = row.get("processing_shape") or [512,512]
            try:
                column = min(3, max(0, int(4*float(row["x_first"])/float(shape[0]))))
                quadrant = min(3, max(0, int(4*float(row["y_first"])/float(shape[1]))))
                cell = (column, quadrant)
            except (KeyError, TypeError, ValueError, ZeroDivisionError, IndexError):
                cell = (-1,-1)
            if not relaxed and counts.get(cell,0) >= 2:
                return False
            seen.add(ident)
            chosen.append(row)
            counts[cell] = counts.get(cell,0)+1
            return True

        # A selection with 12 slots reserves 6 for recent arrivals. Do not
        # demote new candidates behind many hours of older high-frame tracks.
        recent_quota = (limit_per_camera + 1) // 2
        for row in newest:
            if len(chosen) >= recent_quota:
                break
            add(row)
        for row in strongest:
            add(row)
        # Fill remaining slots from recent evidence if spatial cells are full.
        for row in newest:
            add(row, relaxed=True)
        selected.extend(row["id"] for row in chosen)
    return selected


def source_frame_manifest(cache, camera, now, hours=14, limit=96):
    """Publish observed NOAA JPEG URLs; never guess names from timestamps.

    URLs are display-only. No additional NOAA polling or image downloading.
    """
    earliest = now - dt.timedelta(hours=hours)
    frames = []
    for path in cache.glob(f"*_{camera}_*by*.jpg"):
        match = STAMP_RE.fullmatch(path.name)
        if not match or match.group(2).lower() != camera:
            continue
        when = stamp(path.name)
        if earliest <= when <= now:
            frames.append({
                "time": iso(when),
                "image_url": urljoin(f"{BASE}/{camera}/jpegs/", path.name),
            })
    return sorted(frames, key=lambda row: row["time"])[-limit:]


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    temp.replace(path)

def run_once(project, now=None, fetch=get, offline=False, complete_search=False):
    now = now or utcnow()
    out = project/"results"/"ccor"/"ccor.json"
    previous = {}
    if out.exists():
        try:
            previous=json.loads(out.read_text(encoding="utf-8"))
        except (OSError,ValueError):
            pass
    prior = previous.get("tracks", [])
    # Preserve a once-only copy of pre-fix hypotheses, including duplicate IDs.
    # These may contain alternate fits from the same seed, so do not destroy
    # the historical evidence when consolidating dashboard identities.
    if out.exists() and any(
        len([r for r in prior if r.get("camera")==camera]) !=
        len({r.get("id") for r in prior if r.get("camera")==camera})
        for camera in CAMERAS
    ):
        backup=out.with_name("ccor_pre_identity_fix.json")
        if not backup.exists():
            backup.write_bytes(out.read_bytes())
    instrument_states = {}
    frame_manifests = {}
    tracks = []
    for camera in CAMERAS:
        cache=project/"cache"/"ccor"/camera
        errors=[]
        if not offline:
            try:
                listed,new,errors=fetch_new(camera,cache,now,fetch=fetch)
            except Exception as exc:
                listed,new=[],0
                errors=[str(exc)]
        else:
            listed,new=[],0
        images,times,sizes=load_frames(cache,camera,now)
        detected, search_info = track_points(
            images,times,sizes,camera,complete_search=complete_search,
            return_diagnostics=True)
        if complete_search and not search_info["scan_completed"]:
            # An exhausted search must never be labelled globally ranked.
            # Preserve original first-40 behavior and expose the fallback.
            detected=track_points(images,times,sizes,camera)
            search_info["fell_back_to_legacy"] = True
        else:
            search_info["fell_back_to_legacy"] = False
        prior_masked = sum(track_hits_support_arm(row) for row in prior
                           if row.get("camera") == camera)
        detected_masked = sum(track_hits_support_arm(row) for row in detected)
        timestamp_prior_masked = sum(
            track_hits_timestamp_overlay(row) for row in prior
            if row.get("camera") == camera and not track_hits_support_arm(row))
        timestamp_detected_masked = sum(
            track_hits_timestamp_overlay(row) for row in detected
            if not track_hits_support_arm(row))
        if timestamp_prior_masked:
            # Keep one untouched pre-timestamp snapshot for audit, even
            # though filtered tracks leave the public review dashboard.
            backup = out.with_name("ccor_pre_timestamp_overlay_mask.json")
            if out.exists() and not backup.exists():
                backup.write_bytes(out.read_bytes())
        if prior_masked:
            # One-time forensic snapshot: old excluded hypotheses never
            # re-enter the live dashboard, but remain locally inspectable.
            backup = out.with_name(
                "ccor2_pre_support_arm_mask.json" if camera == "ccor2"
                else "ccor_pre_support_arm_mask.json"
            )
            if out.exists() and not backup.exists():
                backup.write_bytes(out.read_bytes())
        retained=[annotated_track(row) for row in integrate_events(prior,detected,camera,now)]
        for track in retained:
            track["review_motion_evidence"] = review_motion_evidence(track)
            track["review_stage"] = early_motion_review(track)
        # Remove legacy background-motion classifications from older saved
        # reviews. Star-field co-motion was not independently calibrated.
        for track in retained:
            track.pop("background_motion", None)
        # Exact source filenames are discovered from cached JPEGs, not inferred.
        # Keep CCOR image review entirely separate from existing C3 media.
        frame_manifests[camera] = source_frame_manifest(cache, camera, now)
        by_time = {frame["time"]: frame["image_url"]
                   for frame in frame_manifests[camera]}
        for track in retained:
            for point in track.get("points", []):
                if point.get("time") in by_time:
                    point["image_url"] = by_time[point["time"]]
        tracks.extend(retained)
        prior_rows=[r for r in prior if r.get("camera")==camera]
        prior_repeated_ids=len(prior_rows)-len({r.get("id") for r in prior_rows})
        instrument_states[camera]={
            "camera":camera,"new_images":new,"indexed_images":len(listed),
            "cached_recent_frames":len(images),
            "last_frame":iso(times[-1]) if times else None,
            "detected_this_cycle":len(detected),
            "retained_review_tracks":len(retained),
            "duplicate_identity_rows_in_prior_output":prior_repeated_ids,
            "support_arm_mask":camera in SUPPORT_ARM_EXCLUSIONS_512,
            "support_arm_excluded_from_prior":prior_masked,
            "support_arm_excluded_new_tracks":detected_masked,
            "timestamp_overlay_mask":camera in CAMERAS,
            "timestamp_excluded_from_prior":timestamp_prior_masked,
            "timestamp_excluded_new_tracks":timestamp_detected_masked,
            "irregular_motion_review_tracks":sum(
                row["motion_consistency"]["status"] == "IRREGULAR_MOTION"
                for row in retained),
            "limited_motion_review_tracks":sum(
                row["review_motion_evidence"]["status"] != "MEASURABLE_MOTION"
                for row in retained),
            "detector_hit_40_hypothesis_cap":(
                not complete_search and search_info["stop_reason"]=="LEGACY_FIRST_40"),
            "detector_search":search_info,
            "status":"FEED_ERROR" if errors and not images else
                     "INSUFFICIENT_FRAMES" if len(images)<6 else "REVIEW_ONLY",
            "errors":errors[-5:]
        }
    # Different instruments have different viewpoints and pixel systems.
    # Record temporal overlap only: NEVER suppress as same physical comet.
    overlaps=[]
    for a in tracks:
        for b in tracks:
            if a["camera"] >= b["camera"]:
                continue
            if a["first"] <= b["last"] and b["first"] <= a["last"]:
                overlaps.append([a["id"],b["id"]])
    payload={
        "schema_version":1, "generated_at":iso(now),
        "mode":"EXPERIMENTAL_REVIEW_ONLY", "automatic_email_alerts":False,
        "independently_confirmed":False,
        "instruments":instrument_states,"tracks":tracks,
        "frame_manifests":frame_manifests,
        "review_shortlist_ids":review_shortlist(tracks),
        "review_shortlist_policy":"Camera mask and trajectory screening; recent early (>=4 frames, >=45min, >=4px) and established (>=5 frames, >=90min, >=6px) candidates prioritised by recency and independent evidence; no background-star motion comparison",
        "temporal_overlaps_not_duplicates":overlaps[:40],
        "warning":"Candidate trajectories are UNVERIFIED. Cross-camera time overlap is not a deduplication. No discovery or realtime alert is asserted."
    }
    atomic_json(out,payload)
    return payload

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project",required=True,type=Path)
    ap.add_argument("--once",action="store_true")
    ap.add_argument("--offline",action="store_true",help="Use cached JPEGs only")
    ap.add_argument("--complete-search",action="store_true",
                    help="Opt in to complete selected-peak search and globally rank best 40")
    ap.add_argument("--poll-seconds",type=int,default=920)
    args=ap.parse_args()
    if args.poll_seconds < 900:
        ap.error("NOAA CCOR polls must be at least 900 seconds apart")
    project=args.project.expanduser().resolve()
    def stop(signum, frame):
        global STOP
        STOP=True
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    while not STOP:
        try:
            payload=run_once(project,offline=args.offline,
                             complete_search=args.complete_search)
            print(f"[{payload['generated_at']}] CCOR "
                  +", ".join(f"{c} {s['status']} {s['retained_review_tracks']} review"
                             for c,s in payload["instruments"].items()),flush=True)
        except Exception as exc:
            print(f"CCOR cycle error: {exc}",flush=True)
        if args.once or args.offline:
            return
        for _ in range(args.poll_seconds):
            if STOP: break
            time.sleep(1)

if __name__=="__main__":
    main()
