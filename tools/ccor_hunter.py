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

def track_points(frames, times, sizes, camera):
    if len(frames) < 6:
        return []
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
                if len(results) >= 40:
                    return results
    return results

def integrate_events(previous, detected, camera, now):
    retained = [r for r in previous
                if r.get("camera")==camera
                and r.get("last","") >= iso(now-dt.timedelta(hours=48))]
    for row in detected:
        match = next((old for old in retained if match_track(old,row,None,None)),None)
        if match:
            row["id"] = match["id"]
            if row["frames"] >= match.get("frames",0) or row["last"] > match["last"]:
                match.update(row)
        else:
            retained.append(row)
    return sorted(retained, key=lambda x:x["last"],reverse=True)[:100]

def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    temp.replace(path)

def run_once(project, now=None, fetch=get, offline=False):
    now = now or utcnow()
    out = project/"results"/"ccor"/"ccor.json"
    previous = {}
    if out.exists():
        try:
            previous=json.loads(out.read_text(encoding="utf-8"))
        except (OSError,ValueError):
            pass
    prior = previous.get("tracks", [])
    instrument_states = {}
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
        detected=track_points(images,times,sizes,camera)
        retained=integrate_events(prior,detected,camera,now)
        tracks.extend(retained)
        instrument_states[camera]={
            "camera":camera,"new_images":new,"indexed_images":len(listed),
            "cached_recent_frames":len(images),
            "last_frame":iso(times[-1]) if times else None,
            "detected_this_cycle":len(detected),
            "retained_review_tracks":len(retained),
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
            payload=run_once(project,offline=args.offline)
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
