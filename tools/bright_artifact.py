#!/usr/bin/env python3
"""Per-frame bright-artifact proximity diagnostics for SOHO LASCO C3.

A moving white blob, streamer, or saturated region can cause many tracks to
pass pixel verification. Measure broad high-brightness material AROUND the
predicted trajectory, excluding the object's compact core. Check multiple
frames independently: the artifact is allowed to move between frames.

This is evidence for review triage, not proof of non-comet origin. Only
human-review priority may be downgraded; never delete raw tracks or videos.
"""
from __future__ import annotations
from collections import defaultdict
import math
from pathlib import Path


def neighborhood(gray, resolution, x, y, *,
                 inner_radius_512=9, outer_radius_512=43):
    """Measure spatially extended bright material away from the track core.

    Normalized 512-pixel units make the result comparable at both resolutions.
    Connected hot tiles prevent isolated stars from triggering a broad mask.
    """
    resolution=int(resolution)
    if resolution not in (512,1024) or len(gray)!=resolution*resolution:
        return {"status":"UNAVAILABLE"}
    scale=resolution/512
    step=int(3*scale)
    inner=(inner_radius_512*scale)**2
    outer=(outer_radius_512*scale)**2
    cx=int(round(x))
    cy=int(round(y))
    r=int(round(outer_radius_512*scale))
    counts=defaultdict(lambda:[0,0])
    tested=bright=white=0
    for yy in range(cy-r,cy+r+1,step):
        if not 0<=yy<resolution:
            continue
        dy=yy-cy
        row=yy*resolution
        for xx in range(cx-r,cx+r+1,step):
            if not 0<=xx<resolution:
                continue
            dx=xx-cx
            rr=dx*dx+dy*dy
            if rr<inner or rr>outer:
                continue
            value=gray[row+xx]
            tested+=1
            hot=value>=205
            bright+=hot
            white+=value>=238
            cell=(int(math.floor(dx/(13*scale))),
                  int(math.floor(dy/(13*scale))))
            counts[cell][0]+=1
            counts[cell][1]+=int(hot)
    if tested<120:
        return {"status":"UNAVAILABLE","samples":tested}
    hot_tiles={pos for pos,(n,b) in counts.items()
               if n>=6 and b>=5 and b/n>=0.42}
    # Require a coherent extended patch, not several unrelated point stars.
    remaining=set(hot_tiles)
    biggest=0
    while remaining:
        seed=remaining.pop()
        stack=[seed]
        count=1
        while stack:
            i,j=stack.pop()
            neighbors={(i+di,j+dj) for di in (-1,0,1)
                       for dj in (-1,0,1) if di or dj}
            hit=remaining & neighbors
            remaining-=hit
            stack.extend(hit)
            count+=len(hit)
        biggest=max(biggest,count)
    bright_fraction=bright/tested
    white_fraction=white/tested
    extended=(biggest>=3 and
              (bright_fraction>=0.23 or white_fraction>=0.13))
    return {"status":"EXTENDED_BRIGHT" if extended else "CLEAR",
            "samples":tested,
            "bright_fraction":round(bright_fraction,3),
            "white_fraction":round(white_fraction,3),
            "largest_hot_tile_component":biggest}


def artifact_proximity(candidate, frame_paths, gray_cache, decode, frame_time,
                       *, max_frames=7):
    """Sample a fitted trajectory over time, including moving artifact areas.

    Strong consensus requires >=3 separate affected frames and >=60% affected
    frames. Fail open if source images are missing. Do not classify a single
    saturated comet core as an artifact: only the surrounding annulus is read.
    """
    first=candidate["first"]
    last=candidate["last"]
    t0,t1=first["time"],last["time"]
    if t1<=t0:
        return {"status":"UNAVAILABLE","reason":"NONPOSITIVE_DURATION"}
    eligible=[]
    for f in frame_paths:
        try:
            t=frame_time(Path(f).name)
        except (ValueError,TypeError):
            continue
        if t0<=t<=t1:
            eligible.append((Path(f),t))
    eligible.sort(key=lambda item:item[1])
    if len(eligible)<5:
        return {"status":"UNAVAILABLE","reason":"TOO_FEW_FRAMES"}
    if len(eligible)>max_frames:
        chosen=sorted({round(i*(len(eligible)-1)/(max_frames-1))
                       for i in range(max_frames)})
        eligible=[eligible[i] for i in chosen]
    sampled=[]
    for frame,t in eligible:
        gray=decode(frame,gray_cache)
        if gray is None:
            continue
        h=(t-t0).total_seconds()/3600
        x=float(first["x"])+float(candidate["vx"])*h
        y=float(first["y"])+float(candidate["vy"])*h
        entry=neighborhood(gray,int(candidate.get("resolution",512)),x,y)
        if entry["status"]=="UNAVAILABLE":
            continue
        sampled.append(entry)
    if len(sampled)<5:
        return {"status":"UNAVAILABLE","reason":"TOO_FEW_VALID_PIXELS",
                "valid_frames":len(sampled)}
    count=sum(m["status"]=="EXTENDED_BRIGHT" for m in sampled)
    return {"status":"ARTIFACT_ADJACENT" if
                count>=3 and count/len(sampled)>=0.6 else "CLEAR",
            "tested_frames":len(sampled),
            "affected_frames":count,
            "affected_fraction":round(count/len(sampled),3),
            "peak_bright_fraction":max(m["bright_fraction"] for m in sampled),
            "peak_white_fraction":max(m["white_fraction"] for m in sampled),
            "max_hot_tile_component":max(m["largest_hot_tile_component"]
                                         for m in sampled)}
