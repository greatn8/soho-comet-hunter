#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt, hashlib, json, math, re, shutil, subprocess
from pathlib import Path

FRAME_RE=re.compile(r"(?P<date>20\d{6})_(?P<hm>\d{4})_c3_512\.jpg$",re.I)

def frame_time(name):
    m=FRAME_RE.search(str(name))
    if not m: raise ValueError(f"Bad frame name: {name}")
    return dt.datetime.strptime(m.group("date")+m.group("hm"),"%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)

def iso(v): return v.isoformat().replace("+00:00","Z")

def track_signature(c):
    return {"time":iso(c["last"]["time"]),"x":float(c["last"]["x"]),"y":float(c["last"]["y"]),"vx":float(c["vx"]),"vy":float(c["vy"])}

def same_signature(a,b):
    try:
        ta=dt.datetime.fromisoformat(str(a["time"]).replace("Z","+00:00")); tb=dt.datetime.fromisoformat(str(b["time"]).replace("Z","+00:00"))
        h=(tb-ta).total_seconds()/3600.0
        px=float(a["x"])+float(a["vx"])*h; py=float(a["y"])+float(a["vy"])*h
        return abs(h)<=18 and math.hypot(px-float(b["x"]),py-float(b["y"]))<=10 and math.hypot(float(a["vx"])-float(b["vx"]),float(a["vy"])-float(b["vy"]))<=2.5
    except Exception: return False

def review_id(c):
    raw="|".join([c["first"]["file"],c["last"]["file"],f'{c["first"]["x"]:.2f}',f'{c["first"]["y"]:.2f}',f'{c["vx"]:.3f}',f'{c["vy"]:.3f}'])
    return "RT"+c["first"]["file"][:13].replace("_","T")+"_"+hashlib.sha1(raw.encode()).hexdigest()[:8].upper()

def ff(args, required=True):
    p=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",*args],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if required and p.returncode!=0: raise RuntimeError(p.stdout.strip() or f"ffmpeg exited {p.returncode}")
    return p.returncode==0

def encode_seq(d,out):
    ff(["-framerate","5","-start_number","0","-i",str(d/"frame_%04d.jpg"),"-c:v","libx264","-preset","veryfast","-crf","21","-pix_fmt","yuv420p","-movflags","+faststart",str(out)])

def find_existing(root,c):
    sig=track_signature(c)
    if not root.exists(): return None
    for p in root.glob("*/review.json"):
        try: m=json.loads(p.read_text())
        except Exception: continue
        if same_signature(m.get("signature") or {},sig): return p.parent,m
    return None

def ensure_live_review(c,cache_dir,results_dir,status="",report_id=""):
    root=Path(results_dir)/"review"; root.mkdir(parents=True,exist_ok=True)
    ex=find_existing(root,c)
    if ex:
        folder,m=ex
        changed=False
        if status and m.get("status")!=status: m["status"]=status; changed=True
        if report_id and m.get("report_id")!=report_id: m["report_id"]=report_id; changed=True
        if changed: m["updated_at"]=iso(dt.datetime.now(dt.timezone.utc)); (folder/"review.json").write_text(json.dumps(m,indent=2))
        return m
    first_t,last_t=c["first"]["time"],c["last"]["time"]
    frames=[]
    for p in sorted(Path(cache_dir).glob("*_c3_512.jpg")):
        try: t=frame_time(p.name)
        except Exception: continue
        if first_t<=t<=last_t: frames.append((p,t))
    if len(frames)<2: raise RuntimeError(f"Only {len(frames)} cached frames in track interval")
    rid=review_id(c); out=root/rid; tmp=out/".frames"
    # Keep only the two zoom videos needed for scientific review. Full-frame
    # videos are reproducible from the original SOHO frames and previously
    # doubled storage for every realtime hypothesis.
    dirs={n:tmp/n for n in ("zoom_raw","zoom_annotated")}
    for d in dirs.values(): d.mkdir(parents=True,exist_ok=True)
    crop=160; scale=640; manifest=[]
    try:
        for i,(src,t) in enumerate(frames):
            h=(t-first_t).total_seconds()/3600.0
            x=float(c["first"]["x"])+float(c["vx"])*h; y=float(c["first"]["y"])+float(c["vy"])*h
            left=max(0,min(512-crop,int(round(x-crop/2)))); top=max(0,min(512-crop,int(round(y-crop/2))))
            bx=max(0,min(494,int(round(x-9)))); by=max(0,min(494,int(round(y-9))))
            zx=max(0,min(scale-36,int(round((x-left)*scale/crop-18)))); zy=max(0,min(scale-36,int(round((y-top)*scale/crop-18))))
            zf=f"crop={crop}:{crop}:{left}:{top},scale={scale}:{scale}:flags=lanczos"
            ff(["-i",str(src),"-vf",zf,"-q:v","2",str(dirs["zoom_raw"]/f"frame_{i:04d}.jpg")])
            ff(["-i",str(src),"-vf",zf+f",drawbox=x={zx}:y={zy}:w=36:h=36:color=yellow@0.95:t=3","-q:v","2",str(dirs["zoom_annotated"]/f"frame_{i:04d}.jpg")])
            manifest.append([src.name,iso(t),f"{x:.3f}",f"{y:.3f}",left,top])
        encode_seq(dirs["zoom_raw"],out/"zoom_raw.mp4"); encode_seq(dirs["zoom_annotated"],out/"zoom_annotated.mp4")
        ff(["-framerate","1","-start_number","0","-i",str(dirs["zoom_annotated"]/"frame_%04d.jpg"),"-vf","scale=240:240,tile=4x3:nb_frames=12:padding=2:margin=2","-frames:v","1",str(out/"zoom_contact_sheet.jpg")],required=False)
        with (out/"manifest.tsv").open("w") as f:
            f.write("filename\tutc\tx512\ty512\tcrop_left\tcrop_top\n")
            for row in manifest: f.write("\t".join(map(str,row))+"\n")
        created=iso(dt.datetime.now(dt.timezone.utc))
        meta={"review_id":rid,"candidate":c["cid"],"priority":c.get("priority"),"event_group":c.get("event_group"),"event_members":c.get("event_members",1),"status":status,"report_id":report_id or None,"camera":"c3","source_resolution":512,"first":c["first"]["file"],"last":c["last"]["file"],"first_x":c["first"]["x"],"first_y":c["first"]["y"],"last_x":c["last"]["x"],"last_y":c["last"]["y"],"vx":c["vx"],"vy":c["vy"],"speed":c["speed"],"rms":c["rms"],"frames_in_review":len(frames),"signature":track_signature(c),"created_at":created,"updated_at":created,"primary_video":"zoom_annotated.mp4","raw_video":"zoom_raw.mp4","full_video":None,"thumbnail":"zoom_contact_sheet.jpg" if (out/"zoom_contact_sheet.jpg").exists() else None}
        (out/"review.json").write_text(json.dumps(meta,indent=2))
        return meta
    finally:
        shutil.rmtree(tmp,ignore_errors=True)
