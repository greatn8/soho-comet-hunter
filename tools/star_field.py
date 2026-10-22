#!/usr/bin/env python3
"""Conservative, resolution-independent common star-field-motion diagnostic.

Tracks with strong pixel support may still be stars. Compare motion only with
OTHER, spatially separated and temporally overlapping, pixel-supported event
representatives. Never suppress raw CUDA tracks. A matching velocity by itself
is not proof of a star, so this should be used for review triage only.
"""
from __future__ import annotations

import math
import statistics


def _scale(c):
    resolution=int(c.get("resolution",512))
    if resolution not in (512,1024):
        raise ValueError("Unsupported C3 resolution")
    return resolution / 512.0


def _point(c):
    scale=_scale(c)
    first,last=c["first"],c["last"]
    return ((float(first["x"])+float(last["x"]))/(2*scale),
            (float(first["y"])+float(last["y"]))/(2*scale))


def _velocity(c):
    scale=_scale(c)
    return float(c["vx"])/scale,float(c["vy"])/scale


def _valid_reference(c):
    if c.get("trajectory_warning") or c.get("quality_issues"):
        return False
    pixel=c.get("pixel_verification") or {}
    if str(pixel.get("status","")).upper()!="STRONG":
        return False
    if int(pixel.get("hits") or 0)<6 or float(pixel.get("hit_fraction") or 0)<0.6:
        return False
    try:
        hours=(c["last"]["time"]-c["first"]["time"]).total_seconds()/3600
        vx,vy=_velocity(c)
        x,y=_point(c)
        return (hours>=1 and math.isfinite(vx) and math.isfinite(vy)
                and math.isfinite(x) and math.isfinite(y)
                and 0.5<=math.hypot(vx,vy)<=9.0)
    except (KeyError,ValueError,TypeError,OverflowError):
        return False


def _overlap_hours(a,b):
    start=max(a["first"]["time"],b["first"]["time"])
    end=min(a["last"]["time"],b["last"]["time"])
    return (end-start).total_seconds()/3600


def star_field_evidence(candidate,references, *,
                        velocity_tolerance_512=0.45,
                        min_neighbors=5,
                        min_separation_512=35.0,
                        min_spread_512=115.0):
    """Estimate whether a strongly detected track follows crowded sky motion.

    All geometry and velocity are normalized to 512 pixels, so a uniform
    change of source resolution does NOT change the decision. At least five
    independent, substantially separated bright tracks must share motion,
    overlap in time, and collectively span much of the field. Duplicated
    hypotheses from the same object cannot manufacture a consensus.

    The resulting STAR_LIKE label is statistical evidence for review triage,
    not an astronomical identification. No candidates are discarded.
    """
    result={"status":"NO_CONSENSUS","neighbors":0,
            "independent_neighbors":0,"spread_512":0.0,
            "velocity_tolerance_512":velocity_tolerance_512}
    if not _valid_reference(candidate):
        result["status"]="INSUFFICIENT_EVIDENCE"
        return result
    x,y=_point(candidate)
    vx,vy=_velocity(candidate)
    neighbors=[]
    for other in references:
        if other is candidate or not _valid_reference(other):
            continue
        if other.get("cid")==candidate.get("cid"):
            continue
        if _overlap_hours(candidate,other)<1.0:
            continue
        ox,oy=_point(other)
        if math.hypot(x-ox,y-oy)<min_separation_512:
            continue
        ovx,ovy=_velocity(other)
        if math.hypot(vx-ovx,vy-ovy)>velocity_tolerance_512:
            continue
        neighbors.append((other,ox,oy,ovx,ovy))
    result["neighbors"]=len(neighbors)

    # Track hypotheses can be duplicates of the same star in multiple CUDA
    # windows. Collapse positions closer than 35 normalized pixels.
    distinct=[]
    for item in sorted(neighbors,
                       key=lambda item:(int(item[0].get("source_window_hours",336)),
                                        str(item[0].get("cid","")))):
        if all(math.hypot(item[1]-prev[1],item[2]-prev[2])
               >=min_separation_512 for prev in distinct):
            distinct.append(item)
    result["independent_neighbors"]=len(distinct)
    if len(distinct)<min_neighbors:
        return result
    pts=[(item[1],item[2]) for item in distinct]
    spread=max(math.hypot(ax-bx,ay-by)
               for i,(ax,ay) in enumerate(pts)
               for bx,by in pts[i+1:])
    result["spread_512"]=round(spread,2)
    if spread<min_spread_512:
        return result

    result["status"]="STAR_LIKE"
    result["reference_velocity_512"]={
        "vx":round(statistics.median(item[3] for item in distinct),3),
        "vy":round(statistics.median(item[4] for item in distinct),3),
    }
    result["motion_offset_512"]=round(math.hypot(
        vx-result["reference_velocity_512"]["vx"],
        vy-result["reference_velocity_512"]["vy"]),3)
    return result


def annotate_star_field(representatives):
    """Assign independent sky-motion diagnostics before any review downgrades."""
    for c in representatives:
        c["star_field"]=star_field_evidence(c,representatives)
    return representatives
