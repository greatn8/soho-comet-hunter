#!/usr/bin/env python3
"""Frame-specific bright horizontal streak triage for LASCO C3 reviews.

A saturated planet/diffraction/blooming streak can create plausible moving
CUDA tracks and apparently strong local pixel contrast. This module detects
long bright horizontal runs independently on each source frame. It only
downgrades tracks repeatedly adjacent to such runs. No permanent sky mask
or comet identity inference is made.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import math


def _frame_time(path):
    return datetime.strptime(Path(path).name[:13], "%Y%m%d_%H%M").replace(tzinfo=timezone.utc)


def near_horizontal_streak(gray, width, x, y, *,
                           margin_512=18, min_length_fraction=0.18,
                           brightness_threshold=230):
    """Find a genuinely extended bright row near x,y, not an isolated star.

    The byte buffer must be an actual grayscale frame. A few dark JPEG pixels
    in a saturated line are tolerated; no planetary ephemeris is assumed.
    Geometry scales identically at 512px and 1024px.
    """
    if width not in (512,1024) or gray is None or len(gray)!=width*width:
        return None
    if not all(math.isfinite(float(v)) for v in (x,y)):
        return None
    scale=width/512
    margin=round(margin_512*scale)
    min_length=max(64,round(width*min_length_fraction))
    tolerated_gap=max(2,round(3*scale))
    px=int(round(float(x)))
    py=int(round(float(y)))
    if not (0<=px<width and 0<=py<width):
        return None
    best=None
    # Only inspect rows near the predicted object position. The position of
    # the Venus streak can move between frames; no fixed y-band is assumed.
    for yy in range(max(0,py-margin),min(width,py+margin+1)):
        row=gray[yy*width:(yy+1)*width]
        first=last=None
        def candidate_at(start,end):
            if start is None or end is None or end-start+1<min_length:
                return None
            if not start-tolerated_gap<=px<=end+tolerated_gap:
                return None
            return {"line_y":yy,"start_x":start,"end_x":end,
                    "length":end-start+1,"distance_px":abs(yy-py)}
        for xx,val in enumerate(row):
            if val>=brightness_threshold:
                if first is None:
                    first=xx
                last=xx
            elif first is not None and xx-last>tolerated_gap:
                result=candidate_at(first,last)
                if result and (best is None or
                               result["distance_px"]<best["distance_px"] or
                               (result["distance_px"]==best["distance_px"] and
                                result["length"]>best["length"])):
                    best=result
                first=last=None
        result=candidate_at(first,last)
        if result and (best is None or
                       result["distance_px"]<best["distance_px"] or
                       (result["distance_px"]==best["distance_px"] and
                        result["length"]>best["length"])):
            best=result
    return best


def bright_streak_review_evidence(candidate, paths, gray_cache, decoder,
                                  resolution, *, max_samples=16):
    """Assess repeat streak proximity along the fitted C3 candidate track.

    Returns a review downgrade only after >=3 independent available frames
    and >=50% of sampled frames have a long saturated line near the candidate.
    A comet briefly crossing a bright line is not automatically downgraded.
    """
    try:
        first=candidate["first"]
        last=candidate["last"]
        t0,t1=first["time"],last["time"]
        eligible=sorted(
            ((Path(path),_frame_time(path)) for path in paths
             if t0<=_frame_time(path)<=t1),
            key=lambda a:a[1])
        if len(eligible)>max_samples:
            selected=sorted({round(i*(len(eligible)-1)/(max_samples-1))
                             for i in range(max_samples)})
            eligible=[eligible[i] for i in selected]
        matched=0
        checked=0
        lines=[]
        for path,when in eligible:
            gray=decoder(path,gray_cache)
            if gray is None or len(gray)!=resolution*resolution:
                continue
            hours=(when-t0).total_seconds()/3600
            x=float(first["x"])+float(candidate["vx"])*hours
            y=float(first["y"])+float(candidate["vy"])*hours
            evidence=near_horizontal_streak(gray,resolution,x,y)
            checked+=1
            if evidence:
                matched+=1
                lines.append({"frame":path.name,**evidence})
        if checked<3:
            return {"status":"UNAVAILABLE","frames_checked":checked,
                    "frames_near_streak":matched}
        fraction=matched/checked
        status=("LIKELY_STREAK_ARTIFACT" if matched>=3 and fraction>=0.5
                else "CLEAR")
        return {"status":status,"frames_checked":checked,
                "frames_near_streak":matched,
                "near_fraction":round(fraction,3),
                "margin_px_512":18,"min_run_fraction":0.18,
                "line_samples":lines[:6]}
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        return {"status":"UNAVAILABLE","reason":str(exc)}
