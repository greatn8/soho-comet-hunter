"""Independent CCOR continuity and compact point-source evidence for review triage.

All comparisons use cached processed NOAA JPEG pixels. Bright temporal
residuals are not independent astronomical confirmations: no discovery
identity or native FITS astrometry is asserted.
"""
from __future__ import annotations
import datetime as dt
import math
import numpy as np

MIN_COVERAGE = .70
MAX_MISSING_RUN = 2
MIN_MATCHED_FRAMES = 4
MIN_PIXEL_PASS_FRACTION = .75
MIN_PIXEL_SNR = 3.0


def timestamp(value):
    return dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))


def frame_continuity(track,times):
    """Count unique measured times over *available* source frames within the track."""
    try:
        first,last=timestamp(track["first"]),timestamp(track["last"])
        relevant=sorted({timestamp(t) for t in times if first<=timestamp(t)<=last})
        detected={timestamp(p["time"]) for p in track.get("points",())}
    except (KeyError,TypeError,ValueError,OverflowError):
        return {"status":"UNASSESSABLE","reason":"Invalid timestamps"}
    if not relevant or len(relevant)<MIN_MATCHED_FRAMES:
        return {"status":"UNASSESSABLE","reason":"Insufficient source frames"}
    hits=[t in detected for t in relevant]
    matched=sum(hits)
    misses=0
    longest=0
    for hit in hits:
        misses=0 if hit else misses+1
        longest=max(longest,misses)
    coverage=matched/len(relevant)
    return {"status":"PASS" if matched>=MIN_MATCHED_FRAMES and coverage>=MIN_COVERAGE and longest<=MAX_MISSING_RUN else "FAIL",
            "matched":matched,"available":len(relevant),"coverage":round(coverage,3),
            "longest_missing_run":longest,"minimum_coverage":MIN_COVERAGE,
            "maximum_missing_run":MAX_MISSING_RUN}


def pixel_at(frame,baseline,x,y):
    """Require a compact, locally contrasted, positive temporal residual peak."""
    residual=np.asarray(frame,dtype=np.float32)-np.asarray(baseline,dtype=np.float32)
    h,w=residual.shape
    x=int(round(float(x)));y=int(round(float(y)))
    if x<8 or y<8 or x>=w-8 or y>=h-8:
        return {"pass":False,"reason":"Outside safe source area"}
    patch=residual[y-7:y+8,x-7:x+8]
    yy,xx=np.ogrid[-7:8,-7:8]
    rr=xx**2+yy**2
    ring=patch[(rr>=25)&(rr<=49)]
    local_median=float(np.median(ring))
    noise=max(1.0,1.4826*float(np.median(np.abs(ring-local_median))))
    center=patch[5:10,5:10]
    peak_idx=np.unravel_index(np.argmax(center),center.shape)
    px=x+int(peak_idx[1])-2
    py=y+int(peak_idx[0])-2
    peak=float(center[peak_idx])
    snr=(peak-local_median)/noise
    # An extended band or an occulter edge is not an isolated point source.
    bright=(patch-local_median)>max(5.0,3*noise)
    count=int(np.count_nonzero(bright & (rr<=16)))
    return {"pass":bool(snr>=MIN_PIXEL_SNR and peak-local_median>=8 and 1<=count<=18),
            "snr":round(snr,2),"peak_residual":round(peak,2),
            "compact_pixels":count,"peak_xy":[px,py]}


def pixel_evidence(track,frame_by_time,baseline):
    """Require point-source pixels at most of the detector's observed positions."""
    passes=0
    evaluated=0
    samples=[]
    for point in track.get("points") or ():
        image=frame_by_time.get(point.get("time"))
        if image is None:
            continue
        result=pixel_at(image,baseline,point["x"],point["y"])
        evaluated+=1
        passes+=bool(result["pass"])
        samples.append({"time":point["time"],**result})
    if evaluated<MIN_MATCHED_FRAMES:
        return {"status":"UNASSESSABLE","evaluated":evaluated,"passed":passes,"samples":samples}
    fraction=passes/evaluated
    return {"status":"PASS" if fraction>=MIN_PIXEL_PASS_FRACTION else "FAIL",
            "evaluated":evaluated,"passed":passes,"pass_fraction":round(fraction,3),
            "minimum_fraction":MIN_PIXEL_PASS_FRACTION,"samples":samples}


def assess(track,times,frame_by_time,baseline):
    continuity=frame_continuity(track,times)
    pixels=pixel_evidence(track,frame_by_time,baseline)
    return {"status":"PASS" if continuity["status"]=="PASS" and pixels["status"]=="PASS" else
            ("UNASSESSABLE" if "UNASSESSABLE" in (continuity["status"],pixels["status"]) else "FAIL"),
            "continuity":continuity,"pixels":pixels}
