#!/usr/bin/env python3
"""Conservative solar-corona structure triage for LASCO C3 review candidates.

A bright broad streamer can fool the regular positive-pixel verification.
Do NOT reject bright, large, or radially moving candidates by themselves:
real comets can have bright comae and radial tails. Only downgrade when
BOTH broad, bidirectional radial image structure persists in multiple raw
frames AND the independent fixed-position motion control strongly fails.

No candidate/image is deleted, and an unavailable diagnostic fails open.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np

MIN_SOURCE_FRAMES = 4
MIN_EXTENDED_FRAMES = 3
MIN_EXTENDED_FRACTION = 0.60
MIN_INDEPENDENT_MOTION_TESTS = 4


def _time(path):
    return dt.datetime.strptime(Path(path).name[:13], "%Y%m%d_%H%M").replace(tzinfo=dt.timezone.utc)


def _median_patch(arr, x, y, radius):
    h, w = arr.shape
    ix, iy = int(round(x)), int(round(y))
    if ix-radius < 0 or iy-radius < 0 or ix+radius >= w or iy+radius >= h:
        return None
    return float(np.median(arr[iy-radius:iy+radius+1, ix-radius:ix+radius+1]))


def radial_structure_sample(gray, resolution, x, y, *, center=None):
    """Evidence of an extended continuous radial streamer, not comet identity.

    Background-independent test compares raw center pixels to both inward
    and outward radial neighborhoods. A compact comet core should have
    significantly less supporting emission in at least one direction.
    """
    if (resolution not in (512,1024) or gray is None or
            len(gray)!=resolution*resolution):
        return None
    try:
        x,y=float(x),float(y)
        if not all(math.isfinite(v) for v in (x,y)):
            return None
        cx,cy=center if center is not None else (resolution/2,resolution/2)
        dx,dy=x-cx,y-cy
        rr=math.hypot(dx,dy)
        scale=resolution/512.0
        if rr < 85*scale:
            # The occulter edge is not an evaluable diffuse-structure region.
            return None
        direction=(dx/rr,dy/rr)
        frame=np.frombuffer(gray,dtype=np.uint8).reshape(resolution,resolution)
        core=_median_patch(frame,x,y,max(1,round(scale)))
        if core is None:
            return None
        peak=int(frame[int(round(y)),int(round(x))])
        inward=[]
        outward=[]
        for signed,target in ((-1,inward),(1,outward)):
            for dist in (8,13,18):
                v=_median_patch(frame,
                    x+signed*dist*scale*direction[0],
                    y+signed*dist*scale*direction[1],
                    max(1,round(2*scale)))
                if v is None:
                    return None
                target.append(v)
        # Neither brightness nor width alone rejects a potential comet.
        # Require substantial *bidirectional* radial brightness; a nucleus
        # with a trailing tail should be asymmetric and retain eligibility.
        in_med=float(np.median(inward))
        out_med=float(np.median(outward))
        broad_ratio=min(in_med,out_med)/max(1.0,core)
        symmetric=abs(in_med-out_med)/max(1.0,core)
        broad=(core >= 42 and in_med >= 35 and out_med >= 35 and
               broad_ratio >= 0.73 and symmetric <= 0.30)
        return {"broad":bool(broad),"core":round(core,1),
                "central_pixel":peak,"inward":round(in_med,1),
                "outward":round(out_med,1),
                "broad_ratio":round(broad_ratio,3),
                "radial_asymmetry":round(symmetric,3)}
    except (ValueError,TypeError,OverflowError,IndexError):
        return None


def coronal_structure_evidence(candidate, frame_paths, gray_cache, decoder,
                               resolution, *, max_samples=12):
    """Assess persistent broad radial structure and negative motion controls."""
    try:
        t0=candidate["first"]["time"]
        t1=candidate["last"]["time"]
        if t1<=t0:
            return {"status":"UNAVAILABLE","reason":"Invalid track times"}
        frames=sorted(((Path(p),_time(p)) for p in frame_paths
                      if t0<=_time(p)<=t1), key=lambda row:row[1])
        if len(frames)>max_samples:
            indices=sorted({round(i*(len(frames)-1)/(max_samples-1))
                            for i in range(max_samples)})
            frames=[frames[i] for i in indices]
        count=0
        broad_count=0
        samples=[]
        for path,t in frames:
            gray=decoder(path,gray_cache)
            if gray is None:
                continue
            hours=(t-t0).total_seconds()/3600.
            x=float(candidate["first"]["x"])+float(candidate["vx"])*hours
            y=float(candidate["first"]["y"])+float(candidate["vy"])*hours
            sample=radial_structure_sample(gray,resolution,x,y)
            if sample is None:
                continue
            count+=1
            broad_count+=int(sample["broad"])
            samples.append({"frame":path.name,**sample})
        motion=(candidate.get("pixel_verification") or {}).get("temporal_motion") or {}
        tested=int(motion.get("tested") or 0)
        supported=int(motion.get("supported") or 0)
        motion_inconclusive=motion.get("status")!="MEASURED" or tested<MIN_INDEPENDENT_MOTION_TESTS
        if count < MIN_SOURCE_FRAMES:
            return {"status":"UNAVAILABLE","frames_checked":count,
                    "broad_frames":broad_count,"reason":"Insufficient usable image frames"}
        fraction=broad_count/count
        persistent=(broad_count>=MIN_EXTENDED_FRAMES and fraction>=MIN_EXTENDED_FRACTION)
        # A positive independent moving-source test protects a comet even
        # when it overlaps a broad streamer. Unknown motion stays eligible.
        negative_motion=not motion_inconclusive and supported==0
        status=("LIKELY_CORONAL_STRUCTURE" if persistent and negative_motion else
                "BROAD_BUT_MOVING" if persistent and supported>0 else
                "MOTION_UNASSESSABLE" if persistent and motion_inconclusive else
                "CLEAR")
        return {"status":status,"frames_checked":count,"broad_frames":broad_count,
                "broad_fraction":round(fraction,3),"motion_checks":tested,
                "motion_supported":supported,
                "min_broad_fraction":MIN_EXTENDED_FRACTION,
                "samples":samples[:6]}
    except (KeyError,ValueError,TypeError,OverflowError) as exc:
        return {"status":"UNAVAILABLE","reason":str(exc)}
