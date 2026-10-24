"""Conservative multi-track background drift triage for NOAA CCOR.

This uses independently located image tracks as a proxy for common stellar
motion. Without FITS astrometric calibration it cannot certify that any
individual point is a star. A motion-consistent background is only accepted
with multiple spatially separated peers and well-overlapping observation
times. No objects are deleted, and failure to identify background motion
does NOT prove an object is a comet.
"""
from __future__ import annotations
import datetime as dt
import math

MIN_INDEPENDENT_TRACKS = 5
MIN_HORIZONTAL_SPREAD_PX = 130.0
MIN_VERTICAL_SPREAD_PX = 60.0
MIN_BACKGROUND_SPEED_PX_H = 2.0
CLUSTER_RADIUS_PX_H = 1.35
CO_MOVING_RADIUS_PX_H = 0.85
CELL_PX = 56
MAX_RESIDUAL_PX_H = 0.8


def _eligible(row, camera):
    if row.get("camera") != camera:
        return False
    if row.get("motion_consistency", {}).get("status") == "IRREGULAR_MOTION":
        return False
    try:
        x, y = float(row["x_first"]), float(row["y_first"])
        vx, vy = float(row["vx_px_h"]), float(row["vy_px_h"])
        duration = (dt.datetime.fromisoformat(row["last"].replace("Z","+00:00")) -
                    dt.datetime.fromisoformat(row["first"].replace("Z","+00:00"))).total_seconds()/3600
        frames = int(row["frames"])
        if not all(map(math.isfinite,(x,y,vx,vy,duration))):
            return False
        if duration < 1.25 or frames < 5:
            return False
        if row.get("review_motion_evidence",{}).get("status") == "LIMITED_MOTION_EVIDENCE":
            return False
        return True
    except (ValueError,KeyError,TypeError,AttributeError):
        return False


def _overlaps(a,b):
    return a["first"] <= b["last"] and b["first"] <= a["last"]


def estimate_common_motion(tracks,camera):
    """Return field-wide velocity consensus or None when evidence is weak."""
    eligible = [t for t in tracks if _eligible(t,camera)]
    candidates=[]
    for seed in eligible:
        pool=[p for p in eligible if _overlaps(seed,p) and
              math.hypot(float(p["vx_px_h"])-float(seed["vx_px_h"]),
                         float(p["vy_px_h"])-float(seed["vy_px_h"])) <= CLUSTER_RADIUS_PX_H]
        # Tracks at the same site can represent dozens of hypotheses for one
        # artifact. Do not count them as independent background stars.
        tiles={}
        for p in pool:
            cell=(int(float(p["x_first"])//CELL_PX),int(float(p["y_first"])//CELL_PX))
            old=tiles.get(cell)
            score=(int(p.get("frames") or 0),-float(p.get("rms_px") or 100))
            if old is None or score>(int(old.get("frames") or 0),-float(old.get("rms_px") or 100)):
                tiles[cell]=p
        peers=list(tiles.values())
        if len(peers)<MIN_INDEPENDENT_TRACKS:
            continue
        xs=[float(p["x_first"]) for p in peers]
        ys=[float(p["y_first"]) for p in peers]
        if max(xs)-min(xs)<MIN_HORIZONTAL_SPREAD_PX or max(ys)-min(ys)<MIN_VERTICAL_SPREAD_PX:
            continue
        vx=sorted(float(p["vx_px_h"]) for p in peers)[len(peers)//2]
        vy=sorted(float(p["vy_px_h"]) for p in peers)[len(peers)//2]
        residuals=[math.hypot(float(p["vx_px_h"])-vx,float(p["vy_px_h"])-vy) for p in peers]
        if math.hypot(vx,vy)<MIN_BACKGROUND_SPEED_PX_H:
            continue
        if sorted(residuals)[len(residuals)//2]>MAX_RESIDUAL_PX_H:
            continue
        candidates.append((len(peers),-sum(residuals)/len(residuals),vx,vy,max(xs)-min(xs),max(ys)-min(ys)))
    if not candidates:
        return None
    count,_,vx,vy,xspread,yspread=max(candidates)
    return {"status":"COMMON_MOTION_DETECTED","camera":camera,
            "vx_px_h":round(vx,2),"vy_px_h":round(vy,2),
            "independent_regions":count,"x_spread_px":round(xspread,1),
            "y_spread_px":round(yspread,1),
            "warning":"Common detector motion is not a verified stellar identity."}


def annotate_background_motion(tracks,camera,common):
    """Return copied hypotheses; classify only strong co-motion as likely star."""
    result=[]
    for row in tracks:
        t=dict(row)
        if row.get("camera")!=camera:
            result.append(t)
            continue
        status="BACKGROUND_UNDETERMINED"
        delta=None
        if common and _eligible(row,camera):
            delta=math.hypot(float(row["vx_px_h"])-common["vx_px_h"],
                             float(row["vy_px_h"])-common["vy_px_h"])
            if delta<=CO_MOVING_RADIUS_PX_H:
                status="LIKELY_BACKGROUND_STAR_MOTION"
            else:
                status="DIFFERENT_FROM_COMMON_MOTION"
        t["background_motion"]={"status":status,
            "velocity_difference_px_h":round(delta,2) if delta is not None else None,
            "background_confidence":"MOTION_ONLY",
            "note":"Co-motion suggests a star; independently verify image and catalog before identification."}
        result.append(t)
    return result
