#!/usr/bin/env python3
"""Measure sky-wide common point-source motion directly in cached C3 images.

Do not confuse an object with strong pixel support with an independently
moving comet. A confident star-field match is review triage, never deletion.
No network fetches, ephemerides, or nonstandard Python packages are used.
"""
from __future__ import annotations
import math
from collections import defaultdict


def extract_compact_sources(gray, size, max_sources=750):
    """Locate high-contrast compact peaks distributed over the image."""
    if not gray or len(gray)!=size*size:
        return []
    scale=size/512
    edge=int(12*scale)
    step=2 if size==1024 else 1
    radius=int(65*scale)
    center=size/2
    tile=int(48*scale)
    delta=int(6*scale)
    groups=defaultdict(list)
    for y in range(edge,size-edge,step):
        base=y*size
        for x in range(edge,size-edge,step):
            if (x-center)**2+(y-center)**2<radius*radius:
                continue
            i=base+x
            value=gray[i]
            if value<30:
                continue
            samples=[gray[i-delta],gray[i+delta],
                     gray[i-delta*size],gray[i+delta*size],
                     gray[i-delta*size-delta],gray[i-delta*size+delta],
                     gray[i+delta*size-delta],gray[i+delta*size+delta]]
            background=sorted(samples)[4]
            contrast=value-background
            if contrast<14:
                continue
            # Nonmaximum suppression keeps compact sources, not every pixel
            # from a large bright streamer or saturated object.
            if any(gray[i+dy*size+dx]>value for dy,dx in
                   ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1))):
                continue
            groups[(x//tile,y//tile)].append((contrast,x,y))
    result=[]
    for tile_key in sorted(groups):
        accepted=[]
        for contrast,x,y in sorted(groups[tile_key],reverse=True):
            if all((x-xx)**2+(y-yy)**2>=(4*scale)**2 for xx,yy in accepted):
                accepted.append((x,y))
                result.append((x,y,contrast))
                if len(accepted)>=5:
                    break
    if len(result)>max_sources:
        result=sorted(result,key=lambda item:item[2],reverse=True)[:max_sources]
    return result


def build_reference(frame_paths, gray_cache, decode, resolution, time_of_frame,
                    target_hours=4, end_frame=None):
    """Decode a two-frame reference, optionally ending at a candidate frame."""
    if len(frame_paths)<4:
        return {"status":"UNAVAILABLE","reason":"TOO_FEW_FRAMES"}
    if end_frame is None:
        newest=frame_paths[-1]
    else:
        newest=next((p for p in frame_paths if p.name==end_frame),None)
        if newest is None:
            return {"status":"UNAVAILABLE","reason":"END_FRAME_NOT_CACHED"}
    end=time_of_frame(newest.name)
    candidates=[(abs((end-time_of_frame(p.name)).total_seconds()/3600-target_hours),p)
                for p in frame_paths if p!=newest and
                2<=(end-time_of_frame(p.name)).total_seconds()/3600<=8]
    if not candidates:
        return {"status":"UNAVAILABLE","reason":"NO_SEPARATED_FRAME_PAIR"}
    old=min(candidates,key=lambda item:item[0])[1]
    hours=(end-time_of_frame(old.name)).total_seconds()/3600
    a=decode(old,gray_cache)
    b=decode(newest,gray_cache)
    if a is None or b is None:
        return {"status":"UNAVAILABLE","reason":"IMAGE_DECODE_FAILED"}
    first=extract_compact_sources(a,resolution)
    last=extract_compact_sources(b,resolution)
    if len(first)<12 or len(last)<12:
        return {"status":"UNAVAILABLE","reason":"INSUFFICIENT_SOURCES",
                "first_sources":len(first),"last_sources":len(last)}
    scale=resolution/512
    cell=8*scale
    index=defaultdict(list)
    for x,y,contrast in last:
        index[(int(x//cell),int(y//cell))].append((x,y))
    return {"status":"READY","hours":hours,"resolution":resolution,
            "first_file":old.name,"last_file":newest.name,
            "first_sources":len(first),"last_sources":len(last),
            "sources":first,"index":index,"cell":cell}


def _matches(reference,dx,dy,tolerance):
    """Count distinct source correspondences after one trial translation."""
    index=reference["index"]
    cell=reference["cell"]
    matches=[]
    seen=set()
    for x,y,contrast in reference["sources"]:
        tx=x+dx
        ty=y+dy
        cx=int(tx//cell)
        cy=int(ty//cell)
        best=None
        for yy in range(cy-1,cy+2):
            for xx in range(cx-1,cx+2):
                for ox,oy in index.get((xx,yy),()):
                    d=math.hypot(tx-ox,ty-oy)
                    if d<=tolerance and (best is None or d<best[0]):
                        best=(d,ox,oy)
        if best is None:
            continue
        key=(best[1],best[2])
        if key in seen:
            continue
        seen.add(key)
        matches.append((x,y))
    return matches


def evidence_for_velocity(candidate,reference):
    """Require a sky-wide velocity consensus over several null translations."""
    if reference.get("status")!="READY":
        return {"status":"UNAVAILABLE","reason":reference.get("reason","NO_REFERENCE")}
    scale=reference["resolution"]/512
    hours=reference["hours"]
    vx=float(candidate["vx"])/scale
    vy=float(candidate["vy"])/scale
    if not (0.5<=math.hypot(vx,vy)<=7):
        return {"status":"OUTSIDE_REFERENCE_RANGE"}
    dx=vx*hours*scale
    dy=vy*hours*scale
    tolerance=2.1*scale
    points=_matches(reference,dx,dy,tolerance)
    n=len(points)
    if n<8:
        return {"status":"NO_CONSENSUS","matches":n}
    # Even accidental source pairings can favor one velocity in a crowded
    # field. Compare with four nearby wrong shifts and stationary structure.
    delta=6*scale
    controls=[_matches(reference,dx+shift_x,dy+shift_y,tolerance)
              for shift_x,shift_y in
              ((delta,0),(-delta,0),(0,delta),(0,-delta),(-dx,-dy))]
    max_control=max(map(len,controls))
    spread=max((math.hypot(ax-bx,ay-by)
                for i,(ax,ay) in enumerate(points)
                for bx,by in points[i+1:]),default=0)/scale
    tiles=len({(int(x//(75*scale)),int(y//(75*scale))) for x,y in points})
    supported=(n>=12 and tiles>=4 and spread>=140
               and n>=max_control*1.6+4)
    return {"status":"STAR_LIKE" if supported else "NO_CONSENSUS",
            "matches":n,"max_control_matches":max_control,
            "field_tiles":tiles,"spread_512":round(spread,1),
            "tested_hours":round(hours,2),"method":"two_frame_compact_sources"}
