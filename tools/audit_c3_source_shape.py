#!/usr/bin/env python3
"""Offline compact-source vs extended-edge audit on ORIGINAL 512 C3 images.

This does not replace the native CUDA detector, download data, or change the
hunter. Pixel morphology is NOT calibrated as a rejection classifier.

Compare pixel shapes along all real returned 12h/48h CUDA trajectories,
plus optional independent official Sungrazer comet coordinates at 512px.
The comet training GIFs are downscaled in-memory for this diagnostic only.

  python3 tools/audit_c3_source_shape.py --project ~/comethunting/comet_hunter_native_cuda_v7_archive --known-controls

Physical motivation: edges of a broad dark streak can produce positive local
maxima that CUDA links. Image-shape anisotropy and core concentration offer
a separate cue from motion or persistent-coordinate suppression.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import subprocess
import zipfile

import numpy as np

from diagnose_c3_shadow_streak import SIZE, latest_complete_logs, load_gray
from realtime_hunter import parse_candidates, candidate_quadrant, image_time

QUADRANTS=("UPPER_LEFT","UPPER_RIGHT","LOWER_LEFT","LOWER_RIGHT","UNKNOWN")
KNOWN=("soho732","soho1264","soho1510")
# Fixed geometry is intentionally NOT fitted to the three known comets.
WINDOWS=(12,48)
SHIFTED_CONTROLS=((-16,0),(16,0),(0,-16),(0,16))


def source_shape(gray,x,y):
    """Compact positive emission vs elongated emission near predicted x/y.

    The 7-pixel-radius foreground and 8..11-pixel reference annulus sample
    the ORIGINAL image. Report both elongation and core-energy concentration
    rather than claiming that either distinguishes comets perfectly.
    """
    ix,iy=int(round(x)),int(round(y))
    if (not np.isfinite(x) or not np.isfinite(y)
            or ix<12 or iy<12 or ix>=SIZE-12 or iy>=SIZE-12):
        return None
    patch=gray[iy-11:iy+12,ix-11:ix+12].astype(np.float64)
    yy,xx=np.mgrid[-11:12,-11:12]
    rr=xx*xx+yy*yy
    annulus=patch[(rr>=64)&(rr<=121)]
    baseline=float(np.median(annulus))
    noise=max(1.5,float(np.median(np.abs(annulus-baseline)))*1.4826)
    # Bright-source contrast measured at the location of the track itself.
    core=patch[rr<=4]
    signal=float(np.mean(np.sort(core)[-5:]))
    snr=(signal-baseline)/noise
    foreground=(rr<=49)
    weights=np.maximum(0,patch-baseline-max(3.0,noise))
    weights*=foreground
    total=float(np.sum(weights))
    if total<5:
        return {"snr":round(snr,3),"elongation":None,
                "core_fraction":None,"compact_hit":False}
    mean_x=float(np.sum(weights*xx)/total)
    mean_y=float(np.sum(weights*yy)/total)
    cov_xx=float(np.sum(weights*(xx-mean_x)**2)/total)
    cov_yy=float(np.sum(weights*(yy-mean_y)**2)/total)
    cov_xy=float(np.sum(weights*(xx-mean_x)*(yy-mean_y))/total)
    trace=cov_xx+cov_yy
    disc=math.sqrt(max(0,(cov_xx-cov_yy)**2+4*cov_xy*cov_xy))
    # Fixed regularizer avoids infinite anisotropy for single bright pixel.
    elongation=(trace+disc+1.0)/(trace-disc+1.0)
    core_fraction=float(weights[rr<=9].sum()/total)
    # Exploratory thresholds only; no production classifier adopted.
    compact_hit=bool(snr>=2.5 and elongation<=4 and core_fraction>=0.5)
    return {
        "snr":round(snr,3),
        "elongation":round(elongation,3),
        "core_fraction":round(core_fraction,3),
        "compact_hit":compact_hit,
    }


def shape_summary(rows):
    rows=[x for x in rows if x is not None]
    measured=[x for x in rows if x["core_fraction"] is not None]
    if not rows:
        return {"samples":0}
    def median(key):
        return round(float(np.median([r[key] for r in measured])),3) if measured else None
    return {
        "samples":len(rows),
        "bright_fraction":round(sum(x["snr"]>=2.5 for x in rows)/len(rows),4),
        "compact_fraction":round(sum(x["compact_hit"] for x in rows)/len(rows),4),
        "median_elongation":median("elongation"),
        "median_core_fraction":median("core_fraction"),
        "measured_shapes":len(measured),
    }


def summarize_cohort(rows):
    if not rows:
        return {"tracks":0,"evaluated":0,"compact_fraction_median":None,
                "bright_fraction_median":None,"median_elongation":None}
    usable=[r for r in rows if r["samples"]>=3]
    def median(key):
        vals=[r[key] for r in usable if r.get(key) is not None]
        return round(float(np.median(vals)),4) if vals else None
    return {
        "tracks":len(rows),"evaluated":len(usable),
        "compact_fraction_median":median("compact_fraction"),
        "bright_fraction_median":median("bright_fraction"),
        "median_elongation":median("median_elongation"),
        "tracks_compact_in_half_frames":sum(
            r.get("compact_fraction",0)>=0.5 for r in usable),
    }


def shape_at_fitted_track(track,frames,decode):
    """Sample predicted endpoint line on actual source frames within interval."""
    t0,t1=track["first"]["time"],track["last"]["time"]
    duration=(t1-t0).total_seconds()
    if duration<=0:
        return [],[]
    observed=[]
    controls=[]
    # Cap to 20 times but include both ends when possible.
    eligible=[f for f in frames if t0<=image_time(f.name)<=t1]
    if len(eligible)>20:
        picks=sorted(set(int(round(i*(len(eligible)-1)/19)) for i in range(20)))
        eligible=[eligible[i] for i in picks]
    for frame in eligible:
        when=image_time(frame.name)
        frac=(when-t0).total_seconds()/duration
        x=track["first"]["x"]+frac*(track["last"]["x"]-track["first"]["x"])
        y=track["first"]["y"]+frac*(track["last"]["y"]-track["first"]["y"])
        gray=decode(frame)
        result=source_shape(gray,x,y)
        if result: observed.append(result)
        shifted=[source_shape(gray,x+dx,y+dy) for dx,dy in SHIFTED_CONTROLS]
        controls.extend([row for row in shifted if row is not None])
    return observed,controls


def decode_gif_as_512(blob):
    """One native 1024 official practice GIF to grayscale 512. No staging."""
    p=subprocess.run([
        "ffmpeg","-hide_banner","-loglevel","error",
        "-i","pipe:0","-frames:v","1","-vf","scale=512:512:flags=area",
        "-f","rawvideo","-pix_fmt","gray","pipe:1",
    ],input=blob,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
    if p.returncode or len(p.stdout)!=SIZE*SIZE:
        raise RuntimeError("FFmpeg failed to decode an official GIF into 512px "
                           +p.stderr.decode(errors="replace")[-200:])
    return np.frombuffer(p.stdout,np.uint8).reshape(SIZE,SIZE)


def known_comet_controls(project,samples_per_case=14):
    """Independently labelled Sungrazer C3 x/y, converted 1024 -> 512.

    No CUDA rerun and no hypothesis fitting to official positions.
    This is a morphology sanity check at genuine observed comet pixels,
    NOT a detector-recall benchmark.
    """
    from benchmark_official_practice_c3 import identify_c3_training
    root=project/"results"/"official_sungrazer_controls"
    results={}
    for name in KNOWN:
        archive=root/f"{name}.zip"
        if not archive.exists():
            results[name]={"status":"MISSING_ARCHIVE"}
            continue
        case=identify_c3_training(archive)
        entries=case["selected"]
        if not entries:
            results[name]={"status":"NO_LABELLED_C3"}
            continue
        if len(entries)>samples_per_case:
            ids=sorted(set(int(round(i*(len(entries)-1)/(samples_per_case-1)))
                           for i in range(samples_per_case)))
            entries=[entries[i] for i in ids]
        observed=[]
        controls=[]
        with zipfile.ZipFile(archive) as z:
            for filename,truth in entries:
                gray=decode_gif_as_512(z.read(filename))
                x,y=truth["x"]/2.,truth["y"]/2.
                measured=source_shape(gray,x,y)
                if measured:observed.append(measured)
                controls.extend(filter(None,
                    (source_shape(gray,x+dx,y+dy)
                     for dx,dy in SHIFTED_CONTROLS)))
        results[name]={
            "status":"OK",
            "original_c3_images":case["matched_c3_gifs"],
            "sampled_labelled_frames":len(entries),
            "comet_positions":shape_summary(observed),
            "shifted_controls":shape_summary(controls),
        }
        print(f"  {name} official C3 source shapes: "
              f"{results[name]['comet_positions']['compact_fraction']:.2%} compact "
              f"vs {results[name]['shifted_controls']['compact_fraction']:.2%} controls",
              flush=True)
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",type=Path,required=True)
    p.add_argument("--known-controls",action="store_true")
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    project=args.project.expanduser().resolve()
    results_dir=project/"results"/"realtime"
    runid,logs=latest_complete_logs(results_dir)
    run_time=dt.datetime.strptime(runid,"%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    cache=Path("/dev/shm")/f"comet_realtime_{os.environ.get('USER','user')}_c3_512"
    if not cache.is_dir():
        raise SystemExit(f"Missing {cache}. Nothing downloaded.")
    available=[f for f in sorted(cache.glob("*_c3_512.jpg"))
               if image_time(f.name)<=run_time]
    if not available:
        raise SystemExit("No pre-existing C3 512px JPEGs before CUDA run")
    last_source=image_time(available[-1].name)
    decoded={}
    def decode(frame):
        key=str(frame)
        if key not in decoded:
            decoded[key]=load_gray(frame)
        return decoded[key]
    report={
        "cuda_run_id":runid,
        "resolution":SIZE,
        "method":"positive compact-source energy and second-moment anisotropy on endpoint-interpolated paths",
        "limitations":"Exploratory; endpoint interpolation can miss real faint sources; diffuse comets can be elongated; CPU ffmpeg grayscale differs from CUDA NVJPEG.",
        "windows":{},
    }
    print(f"Offline shape audit {runid} (up to {available[-1].name}); "
          "no image download or CUDA processing.",flush=True)
    for hours in WINDOWS:
        frames=[f for f in available
                if image_time(f.name)>=last_source-dt.timedelta(hours=hours)]
        tracks=parse_candidates(logs[hours].read_text(encoding="utf-8",errors="replace"))
        shape_rows={q:[] for q in QUADRANTS}
        control_rows={q:[] for q in QUADRANTS}
        for i,track in enumerate(tracks,1):
            q=candidate_quadrant(track)
            observed,controls=shape_at_fitted_track(track,frames,decode)
            shape_rows[q].append(shape_summary(observed))
            control_rows[q].append(shape_summary(controls))
        report["windows"][str(hours)]={
            "native_tracks":len(tracks),
            "source_frames_available":len(frames),
            "quadrants":{
                q:{"track_shapes":summarize_cohort(shape_rows[q]),
                   "shifted_controls":summarize_cohort(control_rows[q])}
                for q in QUADRANTS
            },
        }
        print(f"\n=== {hours}h | {len(frames)} images | {len(tracks)} native tracks ===")
        for q in QUADRANTS[:4]:
            row=report["windows"][str(hours)]["quadrants"][q]
            v=row["track_shapes"]
            ctrl=row["shifted_controls"]
            print(f"  {q:<12} tracks {v['tracks']:>3} | usable {v['evaluated']:>3} "
                  f"| median compact fraction {v['compact_fraction_median']} "
                  f"| control {ctrl['compact_fraction_median']} "
                  f"| median elongation {v['median_elongation']}",flush=True)
    if args.known_controls:
        print("\n=== INDEPENDENTLY LABELLED HISTORICAL C3 COMET POSITIONS ===",flush=True)
        report["known_comet_controls"]=known_comet_controls(project)
    dest=(args.output or results_dir/"candidate_source_shape_audit.json").expanduser().resolve()
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,indent=2)+"\n")
    print("\nSaved:",dest)
    print("No candidate deleted, no live detector settings or production code changed.")


if __name__=="__main__":
    main()
