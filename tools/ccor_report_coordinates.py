"""CCOR coordinates for reporting and historical JPEG-geometry diagnostics.

NOAA CCOR Level 3 JPEG is 1024x960 from 2048x1920 native L2
by a factor-of-two median rebin. The CCOR hunter independently
resizes that JPEG to 512x480. These are CCD/IMAGE coordinates,
not astronomical RA/Dec. The native projection is explicitly
PROVISIONAL until report pixel origin and yaw/orientation are
verified for both cameras with independent instrument landmarks.
"""
from __future__ import annotations

from datetime import datetime
import math

NATIVE_SHAPE=(2048,1920)
JPEG_SHAPE=(1024,960)
VALIDATION_STATUS="PROVISIONAL_ORIENTATION_NOT_VERIFIED"


def image_to_native(x,y,processing_shape,source_jpeg_shape, *,
                    flip_x=False,flip_y=False):
    """Scale a position along the full JPEG field, never assuming a square crop."""
    pw,ph=map(int,processing_shape)
    jw,jh=map(int,source_jpeg_shape)
    if pw<=0 or ph<=0 or (jw,jh)!=JPEG_SHAPE:
        raise ValueError("Expected full 1024x960 NOAA CCOR Level 3 JPEG")
    x=float(x);y=float(y)
    if not math.isfinite(x) or not math.isfinite(y) or not(0<=x<pw and 0<=y<ph):
        raise ValueError("Image position outside processed field")
    # Centre of the source-image pixel group; avoids a systematic one
    # to two-native-pixel rounding bias at downsampling boundaries.
    u=(x+.5)*jw/pw-.5
    v=(y+.5)*jh/ph-.5
    nx=(u+.5)*NATIVE_SHAPE[0]/jw-.5
    ny=(v+.5)*NATIVE_SHAPE[1]/jh-.5
    if flip_x: nx=NATIVE_SHAPE[0]-1-nx
    if flip_y: ny=NATIVE_SHAPE[1]-1-ny
    return (nx,ny)


def native_to_image(x,y,processing_shape,source_jpeg_shape, *,
                    flip_x=False,flip_y=False):
    pw,ph=map(int,processing_shape)
    jw,jh=map(int,source_jpeg_shape)
    if pw<=0 or ph<=0 or (jw,jh)!=JPEG_SHAPE:
        raise ValueError("Expected full 1024x960 NOAA CCOR Level 3 JPEG")
    x=float(x);y=float(y)
    if flip_x: x=NATIVE_SHAPE[0]-1-x
    if flip_y: y=NATIVE_SHAPE[1]-1-y
    if not(0<=x<NATIVE_SHAPE[0] and 0<=y<NATIVE_SHAPE[1]):
        raise ValueError("Native position out of bounds")
    return ((x+.5)*pw/NATIVE_SHAPE[0]-.5,
            (y+.5)*ph/NATIVE_SHAPE[1]-.5)


def prepare_reporting_coordinates(track, *, geometry_verified=False):
    """Export only *measured* native position estimates, never fitted frames."""
    if track.get("camera") not in ("ccor1","ccor2"):
        raise ValueError("Not a CCOR camera")
    samples=[]
    for p in track.get("points") or []:
        a,b=image_to_native(p["x"],p["y"],
                            track["processing_shape"],
                            track["source_jpeg_shape"])
        moment=datetime.fromisoformat(p["time"].replace("Z","+00:00"))
        samples.append({"time_utc":moment.isoformat(timespec="minutes").replace("+00:00","Z"),
                        "image_xy_512":[p["x"],p["y"]],
                        "native_xy_estimate":[round(a),round(b)],
                        "report_line_provisional":f"{moment:%H%M} {round(a)} {round(b)}"})
    return {"camera":track["camera"].upper(),
            "candidate_id":track.get("id"),
            "source_resolution":list(track["source_jpeg_shape"]),
            "native_resolution":list(NATIVE_SHAPE),
            "coordinate_kind":"CCD_IMAGE_PIXELS_NOT_CELESTIAL",
            "coordinate_validation":("GEOMETRY_VERIFIED" if geometry_verified else
                                     VALIDATION_STATUS),
            "report_ready":False,
            "observations":samples,
            "warning":"Numbers are provisional image coordinates. Verify the original report image orientation and a physically moving point source before reporting."}
