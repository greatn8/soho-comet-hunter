#!/usr/bin/env python3
"""Conservative border-artifact review triage with 512/1024 geometry parity.

CUDA's --edge-mask default stays 24 px regardless of input size. Its 1024
field therefore admits a physically thinner border strip than its 512 field.
Mark events living entirely in that strip as SECONDARY, *not* as non-comets.
No raw CUDA track, pixel measurement, or review media is removed.
"""
from __future__ import annotations

import math


def edge_review_evidence(candidate, *, mask_512=24.0):
    """Check whether the complete fitted endpoint path is confined to one edge.

    The apparent physical pixel scale doubles from 512 to 1024. Strong
    review candidates confined to this near-edge band merit cautious human
    review because they would have fallen inside the 512 CUDA edge mask.
    One-frame entries at an edge are NOT flagged if the object moves inward.
    """
    resolution=int(candidate.get("resolution",512))
    if resolution not in (512,1024):
        return {"status":"UNAVAILABLE","reason":"INVALID_RESOLUTION"}
    scale=resolution/512.0
    width=float(resolution)
    threshold=mask_512*scale
    try:
        first=candidate["first"]
        last=candidate["last"]
        x0,y0=float(first["x"]),float(first["y"])
        x1,y1=float(last["x"]),float(last["y"])
    except (KeyError,TypeError,ValueError):
        return {"status":"UNAVAILABLE","reason":"MISSING_ENDPOINTS"}
    if not all(math.isfinite(t) for t in (x0,y0,x1,y1)):
        return {"status":"UNAVAILABLE","reason":"NONFINITE_COORDINATES"}
    # Reject malformed coordinates from decisions, but leave their review
    # elsewhere to the existing trajectory-integrity gate.
    if not all(0<=v<width for v in (x0,y0,x1,y1)):
        return {"status":"UNAVAILABLE","reason":"OUTSIDE_IMAGE"}
    edges={
        "TOP":(y0,y1),
        "BOTTOM":(width-1-y0,width-1-y1),
        "LEFT":(x0,x1),
        "RIGHT":(width-1-x0,width-1-x1),
    }
    matching=[(edge,max(distances)) for edge,distances in edges.items()
              if max(distances)<threshold]
    if matching:
        edge,furthest=min(matching,key=lambda item:item[1])
        return {"status":"BORDER_CONFINED",
                "edge":edge,"max_edge_distance_512":round(furthest/scale,2),
                "original_mask_512":mask_512,
                "effective_mask_px":int(threshold),
                "reason":"Entire candidate endpoint path remains inside the original 512-scale border mask"}
    return {"status":"CLEAR","effective_mask_px":int(threshold)}
