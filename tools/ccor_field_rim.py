"""Conservative frame-dependent exclusion of the CCOR outer field rim.

The rendered NOAA CCOR JPEG has a black exterior surrounding the useful
illuminated field. Residual contrast at that moving black/blue edge produces
point-source false positives. Use each ORIGINAL raw grayscale frame to
locate the outer field, not a fixed circular sky mask.

Mask only a narrow strip inside the measured boundary. This is a JPEG
artifact filter, not an astrometric field-of-view calibration.
"""
from __future__ import annotations

import math
import numpy as np
from PIL import Image, ImageFilter

SMOOTH_RADIUS_512 = 4
BOUNDARY_MARGIN_512 = 8
FIRST_SEARCH_PIXEL_512 = 24
ILLUMINATED_LEVEL = 4.0


def outer_rim_mask(frame, *, margin_512=BOUNDARY_MARGIN_512):
    """Mask the exterior and up to 8 pixels inward from the observed rim.

    The black exterior must extend from the edge of the JPEG; a compact
    point or narrow dark gap within the field is not treated as a border.
    Ignore the first 24 JPEG pixels containing metadata/labels. Require
    three bright smoothed pixels in a row to reject random isolated stars.
    """
    arr=np.asarray(frame)
    if arr.ndim!=2:
        raise ValueError("CCOR field-rim mask requires a grayscale image")
    h,w=arr.shape
    if min(h,w)<96:
        raise ValueError("CCOR image too small for rim detection")
    sx=w/512.0
    sy=h/512.0
    smooth=np.asarray(
        Image.fromarray(np.clip(arr,0,255).astype(np.uint8)).filter(
            ImageFilter.BoxBlur(max(1,round(SMOOTH_RADIUS_512*min(sx,sy))))),
        dtype=np.float32)
    lit=smooth>ILLUMINATED_LEVEL
    result=np.zeros((h,w),dtype=bool)
    margin_x=max(2,round(margin_512*sx))
    margin_y=max(2,round(margin_512*sy))
    start_x=max(12,round(FIRST_SEARCH_PIXEL_512*sx))
    start_y=max(12,round(FIRST_SEARCH_PIXEL_512*sy))
    # Three-pixel confirmation prevents isolated outside speckles / text
    # from being mistaken for the beginning of the illuminated field.
    def first_run(values):
        indices=np.flatnonzero(values[:-2] & values[1:-1] & values[2:])
        return int(indices[0]) if indices.size else None
    # A horizontal scan from each image edge captures the circular outside
    # rim at left/right; vertical scans capture the upper/lower rim.
    for x in range(w):
        offset=first_run(lit[start_y:h//2,x])
        if offset is not None:
            stop=min(h,start_y+offset+margin_y+1)
            result[:stop,x]=True
        bottom_section=lit[h//2:h-start_y,x][::-1]
        offset=first_run(bottom_section)
        if offset is not None:
            first=max(0,h-start_y-1-offset-margin_y)
            result[first:,x]=True
    for y in range(h):
        offset=first_run(lit[y,start_x:w//2])
        if offset is not None:
            result[y,:min(w,start_x+offset+margin_x+1)]=True
        offset=first_run(lit[y,w//2:w-start_x][::-1])
        if offset is not None:
            first=max(0,w-start_x-1-offset-margin_x)
            result[y,first:]=True
    # Do not let a dark internal patch deep within the usable field become
    # a new exclusion: a rim position must be on the outer half of the
    # image radius. The existing occulter/arm masks handle inner artifacts.
    yy,xx=np.ogrid[:h,:w]
    radius=np.sqrt(((xx-w/2)/(w/2))**2+((yy-h/2)/(h/2))**2)
    result &= radius>=0.68
    return result


def track_rim_evidence(track,mask_by_time):
    """Annotate a retained hypothesis touching the actual frame boundary."""
    try:
        shape=track.get("processing_shape") or [512,512]
        width,height=int(shape[0]),int(shape[1])
        if width<=0 or height<=0:
            raise ValueError("Invalid detector dimensions")
        points=track.get("points") or []
        tested=0
        hits=0
        for point in points:
            mask=mask_by_time.get(point.get("time"))
            if mask is None:
                continue
            mh,mw=mask.shape
            x=round(float(point["x"])*mw/width)
            y=round(float(point["y"])*mh/height)
            tested+=1
            if not(0<=x<mw and 0<=y<mh) or bool(mask[y,x]):
                hits+=1
        # At least two independently measured frames must touch the rim;
        # one crossing by a genuine entering comet remains eligible.
        status=("BOUNDARY_CONTACT" if hits>=2 else
                "CLEAR" if tested else "UNASSESSABLE")
        return {"status":status,"checked":tested,"rim_hits":hits,
                "margin_px_512":BOUNDARY_MARGIN_512}
    except (KeyError,ValueError,TypeError,OverflowError,IndexError) as exc:
        return {"status":"UNASSESSABLE","reason":str(exc)}
