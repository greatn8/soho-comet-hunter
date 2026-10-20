#!/usr/bin/env python3
"""Extract independently checked approximate C3 positions for a report draft.

This tool is READ-ONLY and CPU-only. It inspects original 512x512 images
already cached by the realtime hunter. Intermediate coordinates are localized
from moving pixel excess over nearby control frames; they are ESTIMATES to
check visually before official submission, not independently fitted CUDA
detections. CUDA endpoints are retained separately as pipeline coordinates.
No SOHO downloads, GPU tasks, hunter or dashboard modifications.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path

from pixel_verify import _decode_gray, _sample_signal, WIDTH, HEIGHT

FRAME_SUFFIX = "_c3_512.jpg"


def image_time(name):
    if not (len(name) == 24 and name.endswith(FRAME_SUFFIX)):
        raise ValueError("Unexpected C3 filename: " + str(name))
    return dt.datetime.strptime(name[:13], "%Y%m%d_%H%M").replace(tzinfo=dt.timezone.utc)


def candidate_endpoints(candidate):
    track = candidate.get("track") or {}
    t0, t1 = image_time(candidate["first"]), image_time(candidate["last"])
    if t1 <= t0:
        raise ValueError("Nonpositive candidate time span")
    for k in ("first_x", "first_y", "last_x", "last_y"):
        if k not in track or not math.isfinite(float(track[k])):
            raise ValueError("Missing/invalid measured CUDA endpoint " + k)
    return t0, t1, (
        float(track["first_x"]), float(track["first_y"]),
        float(track["last_x"]), float(track["last_y"]),
    )


def extract_submission_rows(project, candidate, max_rows=32):
    """Return report rows using existing JPEGs and raw CUDA track endpoints.

    Pixel estimates need visual validation. Rows unsupported by temporal
    controls are omitted, rather than presenting interpolated paths as data.
    """
    if not 5 <= max_rows <= 64:
        raise ValueError("max_rows must be 5..64")
    t0, t1, (x0, y0, x1, y1) = candidate_endpoints(candidate)
    cache = (Path("/dev/shm") /
             ("comet_realtime_" + os.environ.get("USER", "user") + "_c3_512"))
    if not cache.is_dir():
        return []

    span = (t1-t0).total_seconds()
    def predicted(when):
        f = (when-t0).total_seconds()/span
        return (x0+(x1-x0)*f, y0+(y1-y0)*f)

    eligible = []
    for path in sorted(cache.glob("*_c3_512.jpg")):
        try:
            t = image_time(path.name)
        except ValueError:
            continue
        if t0 <= t <= t1:
            eligible.append((path, t))
    if not eligible:
        return []

    # Build controls over the complete interval, even if the output is capped.
    indexes = list(range(len(eligible)))
    if len(indexes) > max_rows:
        indexes = sorted({round(i*(len(eligible)-1)/(max_rows-1))
                          for i in range(max_rows)})
    decoded = {}

    def gray(index):
        return _decode_gray(eligible[index][0], decoded)

    def control(index, direction, point):
        for j in range(index+direction, len(eligible) if direction>0 else -1, direction):
            if math.dist(point, predicted(eligible[j][1])) >= 5.0:
                if gray(j) is not None:
                    return j
        return None

    rows = []
    for i in indexes:
        path, when = eligible[i]
        first = path.name == candidate["first"]
        last = path.name == candidate["last"]
        if first or last:
            # These are the actual endpoints emitted by the CUDA track output.
            x, y = (x0,y0) if first else (x1,y1)
            rows.append({"date": when.strftime("%Y-%m-%d"),
                         "time": when.strftime("%H:%M"),
                         "x": round(x,1), "y": round(y,1),
                         "basis": "CUDA endpoint", "filename": path.name})
            continue

        point = predicted(when)
        left = control(i, -1, point)
        right = control(i, 1, point)
        if left is None or right is None:
            continue
        current = gray(i)
        before, after = gray(left), gray(right)
        if current is None or before is None or after is None:
            continue

        cx, cy = round(point[0]), round(point[1])
        if not (9 <= cx < WIDTH-9 and 9 <= cy < HEIGHT-9):
            continue

        # Localize the moving compact source by maximizing summed 3x3
        # temporal excess around the predicted track position (within 4px).
        best = None
        for oy in range(-4, 5):
            for ox in range(-4, 5):
                if ox*ox + oy*oy > 16:
                    continue
                x, y = cx+ox, cy+oy
                score = 0
                for dy in (-1,0,1):
                    for dx in (-1,0,1):
                        index = (y+dy)*WIDTH+(x+dx)
                        score += max(0, int(current[index]) -
                                     max(int(before[index]),int(after[index])))
                score -= math.hypot(ox,oy)*0.05
                if best is None or score > best[0]:
                    best = (score,x,y)
        if best is None or best[0] <= 0:
            continue
        _, bx, by = best
        signal = _sample_signal(current, bx, by)
        earlier = _sample_signal(before, bx, by)
        later = _sample_signal(after, bx, by)
        if signal is None or earlier is None or later is None:
            continue
        excess = signal["contrast"] - max(earlier["contrast"],later["contrast"])
        if (signal["snr"] < 2.5 or signal["contrast"] < 2.0 or
                excess < max(3.0,2.0*signal["noise"])):
            continue

        # Subpixel centroid of the transient positive signal in its 3x3 core.
        numerator_x = numerator_y = denominator = 0.0
        for dy in (-1,0,1):
            for dx in (-1,0,1):
                x, y = bx+dx, by+dy
                index = y*WIDTH+x
                w = max(0, int(current[index])-max(int(before[index]),
                                                    int(after[index])))
                numerator_x += x*w
                numerator_y += y*w
                denominator += w
        if denominator < 1:
            continue
        x, y = numerator_x/denominator, numerator_y/denominator
        if math.dist((x,y),point) > 5.5:
            continue
        rows.append({"date": when.strftime("%Y-%m-%d"),
                     "time": when.strftime("%H:%M"),
                     "x": round(x,1), "y": round(y,1),
                     "basis": "temporal pixel estimate",
                     "filename": path.name,
                     "snr": round(signal["snr"],2),
                     "temporal_excess": round(excess,2)})
    return sorted(rows,key=lambda p:(p["date"],p["time"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",type=Path,required=True)
    parser.add_argument("--candidate",required=True)
    args = parser.parse_args()
    project=args.project.expanduser().resolve()
    results=project/"results"/"realtime"
    candidates=json.loads((results/"latest_candidates.json").read_text())
    tracks=json.loads((results/"latest_tracks.json").read_text())
    match=next((c for c in candidates if c.get("candidate")==args.candidate),None)
    track=next((t for t in tracks if t.get("candidate")==args.candidate),None)
    if not match or not track:
        parser.error("Candidate is not in the latest local hunter result")
    item={**match,"track":track}
    try:
        rows=extract_submission_rows(project,item)
    except ValueError as exc:
        parser.error(str(exc))
    print("Candidate:", args.candidate)
    print("C3, 512x512, origin upper left, UTC; inspect original frames")
    print("| Frame Time | X Pos | Y Pos | Basis |")
    print("|---|---:|---:|---|")
    for row in rows:
        print(f"| {row['date']} {row['time']} | {row['x']} | {row['y']} | {row['basis']} |")
    if len(rows)<5:
        print("WARNING: fewer than five supported rows. Do NOT submit guessed coordinates.")
    print("Intermediate pixel positions are estimates; all rows need visual verification.")


if __name__=="__main__":
    main()
