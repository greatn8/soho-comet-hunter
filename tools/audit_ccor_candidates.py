#!/usr/bin/env python3
"""Read-only CCOR review: compare shortlisted trails and cached NOAA JPEGs.

This diagnostic is NOT astrometry, comet identification or star-catalog matching.
Does not modify detector JSON, media, cache, state, tasks or alerting.
"""
import argparse
import json
import math
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit
import numpy as np
from PIL import Image,ImageDraw

def point(track,t):
    from datetime import datetime
    date=lambda v:datetime.fromisoformat(v.replace("Z","+00:00"))
    h=(date(t)-date(track["first"])).total_seconds()/3600
    return (track["x_first"]+track["vx_px_h"]*h,
            track["y_first"]+track["vy_px_h"]*h)

def overlap(a,b):
    if a["camera"]!=b["camera"]: return None
    from datetime import datetime
    date=lambda v:datetime.fromisoformat(v.replace("Z","+00:00"))
    lo=max(a["first"],b["first"]);hi=min(a["last"],b["last"])
    hours=(date(hi)-date(lo)).total_seconds()/3600
    if hours<.75:return None
    v=math.hypot(a["vx_px_h"]-b["vx_px_h"],a["vy_px_h"]-b["vy_px_h"])
    if v>.8:return None
    mid=(date(lo)+(date(hi)-date(lo))/2).isoformat()
    distances=[math.dist(point(a,t),point(b,t)) for t in (lo,mid,hi)]
    if max(distances)>5:return None
    return {"first":a["id"],"second":b["id"],"overlap_h":round(hours,2),
            "velocity_difference_px_h":round(v,2),"separations_px":[round(v,1) for v in distances],
            "interpretation":"POSSIBLY_SAME_OR_ADJACENT_SOURCE"}

def cached_image(project,track,obs):
    from datetime import datetime
    name=Path(urlsplit(obs.get("image_url","")).path).name
    prefix=datetime.fromisoformat(obs["time"].replace("Z","+00:00")).strftime("%Y%m%d_%H%M")
    if not name.startswith(prefix+"_") or not name.endswith("_"+track["camera"]+"_1024by960.jpg"):
        return None
    path=project/"cache"/"ccor"/track["camera"]/name
    if not path.is_file():return None
    with Image.open(path) as im:
        return im.convert("RGB").resize(tuple(track.get("processing_shape") or [512,512]),Image.Resampling.BOX)

def contrast(image,x,y):
    a=np.asarray(image.convert("L"),dtype=float)
    x=int(round(x));y=int(round(y))
    if x<13 or y<13 or x+13>=a.shape[1] or y+13>=a.shape[0]:return None
    yy,xx=np.mgrid[-12:13,-12:13]
    r=xx**2+yy**2
    tile=a[y-12:y+13,x-12:x+13]
    ring=tile[(r>=49)&(r<=144)]
    bg=np.median(ring);noise=np.median(abs(ring-bg))*1.4826
    return round(float((np.max(tile[r<=4])-bg)/max(2,noise)),2)

def run(project,out):
    d=json.loads((project/"results"/"ccor"/"ccor.json").read_text())
    if d.get("mode")!="EXPERIMENTAL_REVIEW_ONLY":raise ValueError("Invalid CCOR source")
    by_id={t["id"]:t for t in d["tracks"]}
    tracks=[by_id[ident] for ident in d["review_shortlist_ids"] if ident in by_id]
    pairs=[e for i,a in enumerate(tracks) for b in tracks[i+1:]
           if (e:=overlap(a,b)) is not None]
    out.mkdir(parents=True,exist_ok=True)
    results=[]
    for t in tracks:
        points=sorted(t.get("points") or [],key=lambda p:p["time"])
        if len(points)>8:points=[points[i] for i in np.linspace(0,len(points)-1,8,dtype=int)]
        measures=[];samples=[]
        for p in points:
            image=cached_image(project,t,p)
            if image is None:continue
            x=float(p["x"]);y=float(p["y"])
            measures.append({"time":p["time"],"contrast":contrast(image,x,y)})
            crop=image.crop((round(x)-20,round(y)-20,round(x)+20,round(y)+20))
            crop=crop.resize((192,192),Image.Resampling.NEAREST)
            samples.append((p["time"],crop))
        sheet=None
        if samples:
            cols=min(4,len(samples));height=218
            canvas=Image.new("RGB",(cols*192,math.ceil(len(samples)/cols)*height),(10,20,30))
            draw=ImageDraw.Draw(canvas)
            for i,(date,im) in enumerate(samples):
                xx=(i%cols)*192;yy=(i//cols)*height
                canvas.paste(im,(xx,yy+26));draw.text((xx+6,yy+5),date[11:16]+" UTC",fill="white")
            sheet=t["id"]+"_evidence.png";canvas.save(out/sheet)
        results.append({"id":t["id"],"camera":t["camera"],
                        "frames_inspected":len(samples),"frames_requested":len(points),
                        "contrast_samples":measures,"contact_sheet":sheet,
                        "motion":t.get("background_motion"),
                        "warning":"JPEG brightness is not proof of comet identity."})
    result={"generated_from":d.get("generated_at"),"shortlisted":len(tracks),
            "suspected_duplicate_pairs":pairs,"results":results,
            "comets_independently_confirmed":0,
            "warning":"Native CCD-to-JPEG astrometric calibration has NOT been established. No comet identifications can be inferred."}
    (out/"audit.json").write_text(json.dumps(result,indent=2)+"\n")
    print("CCOR audit:",len(tracks),"tracks;",len(pairs),"possible duplicate pairs")
    for p in pairs:print("PAIR:",p["first"],p["second"])
    for row in results:print(row["id"],"cached JPEGs",row["frames_inspected"],"/",row["frames_requested"])
    print("Output:",out/"audit.json")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--project",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();run(a.project.expanduser(),a.output.expanduser())
