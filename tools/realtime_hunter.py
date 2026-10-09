#!/usr/bin/env python3
"""Realtime SOHO/LASCO C3 comet hunter supervisor."""

from __future__ import annotations
import argparse, datetime as dt, fcntl, html, json, math, os, re, shutil, signal, subprocess, time
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from live_review import ensure_live_review
from pixel_verify import classify_for_review, verify_track_pixels

SOHO_BASE="https://soho.nascom.nasa.gov/data/REPROCESSING/Completed"
SUNGRAZER_REPORTS="https://sungrazer.nrl.navy.mil/index.php/plain-text-reports?items_per_page=100&order=field_report_date&sort=desc"
USER_AGENT="CometHunterRealtime/1.0 (SOHO comet-hunting research)"
IMAGE_RE=re.compile(r"(?P<name>20\d{6}_\d{4}_c3_512\.jpg)",re.I)
DAY_DIR_RE=re.compile(r"(?P<day>20\d{6})/",re.I)
CANDIDATE_RE=re.compile(r"^(?P<cid>C\d+) \[(?P<priority>HIGH|MEDIUM)\].*?frames=(?P<frames>\d+).*?speed=(?P<speed>-?[0-9.]+) px/h.*?vx=(?P<vx>-?[0-9.]+).*?vy=(?P<vy>-?[0-9.]+).*?RMS=(?P<rms>-?[0-9.]+).*?brightnessCV=(?P<cv>-?[0-9.]+).*?sunward=(?P<sunward>-?[0-9.]+) px/h.*?score=(?P<score>-?[0-9.]+)",re.I)
POINT_RE=re.compile(r"(?P<file>20\d{6}_\d{4}_c3_512\.jpg)\s+\((?P<x>-?[0-9.]+),\s*(?P<y>-?[0-9.]+)\)")
REPORT_ID_RE=re.compile(r"report_[a-z0-9]+_\d{14}",re.I)
REPORT_POINT_RE=re.compile(r"(?m)^\s*(?P<t>\d{1,2}:\d{2}|\d{4})\s+(?P<x>\d{1,4})\s+(?P<y>\d{1,4})\s*$")
DATE_RE=re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
STOP=False

def log(msg):
    stamp=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {msg}",flush=True)

def on_signal(signum,frame):
    global STOP
    STOP=True
    log(f"Received signal {signum}; stopping after current step.")

def fetch_text(url,timeout=120):
    req=Request(url,headers={"User-Agent":USER_AGENT})
    with urlopen(req,timeout=timeout) as r:
        return r.read().decode("utf-8","replace")

def fetch_bytes(url,timeout=180):
    req=Request(url,headers={"User-Agent":USER_AGENT})
    with urlopen(req,timeout=timeout) as r:
        return r.read()

def image_time(name):
    return dt.datetime.strptime(name[:13],"%Y%m%d_%H%M").replace(tzinfo=dt.timezone.utc)

def day_url(day):
    key=day.strftime("%Y%m%d")
    return f"{SOHO_BASE}/{day:%Y}/c3/{key}/"

def year_c3_url(year):
    return f"{SOHO_BASE}/{int(year)}/c3/"

def explicit_images(index_html):
    return sorted(set(m.group("name") for m in IMAGE_RE.finditer(index_html)))

def discover_available_days(now,lookback_days=14):
    cutoff=(now-dt.timedelta(days=lookback_days)).date()
    years=sorted({now.year,cutoff.year},reverse=True)
    found=set()
    for year in years:
        url=year_c3_url(year)
        try:
            page=fetch_text(url)
        except Exception as exc:
            log(f"WARN: could not read C3 year index {url}: {exc}")
            continue
        for m in DAY_DIR_RE.finditer(page):
            token=m.group("day")
            try:
                day=dt.datetime.strptime(token,"%Y%m%d").date()
            except ValueError:
                continue
            if cutoff<=day<=now.date():
                found.add(day)
    return sorted(found)

def latest_cached_time(cache_dir):
    latest=None
    for p in cache_dir.glob("*_c3_512.jpg"):
        try:
            when=image_time(p.name)
        except Exception:
            continue
        if latest is None or when>latest:
            latest=when
    return latest

def soho_gate_wait(state_dir,minimum_seconds):
    gate_file=state_dir/"realtime_soho_gate.lock"
    stamp_file=state_dir/"realtime_last_soho_session_epoch.txt"
    gate_file.touch(exist_ok=True)
    with gate_file.open("r+") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        try:last=int(stamp_file.read_text().strip())
        except Exception:last=0
        wait=minimum_seconds-(int(time.time())-last)
        if wait>0:
            log(f"SOHO cooldown: waiting {wait}s to preserve >=15 min between sessions.")
            while wait>0 and not STOP:
                nap=min(wait,30); time.sleep(nap); wait-=nap
        if STOP:return
        stamp_file.write_text(str(int(time.time())))
        fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

def download_live_frames(cache_dir,state_dir,poll_seconds,bootstrap_hours):
    soho_gate_wait(state_dir,poll_seconds)
    if STOP:return []
    now=dt.datetime.now(dt.timezone.utc)

    log("SOHO realtime session: discovering latest published C3 day.")
    available_days=discover_available_days(now,lookback_days=14)
    if not available_days:
        log("WARN: no recent C3 day directories found in the SOHO year index.")
        return []

    latest_day=available_days[-1]
    lag=(now-dt.datetime.combine(latest_day,dt.time(0,0),tzinfo=dt.timezone.utc)).total_seconds()/86400.0
    log(f"Latest published C3 day: {latest_day:%Y%m%d} (archive lag about {lag:.1f} day(s) by date).")

    # Read the newest published day plus preceding available days. Two days
    # normally cover a 12 hour detector window across midnight; three gives
    # margin for partial/late SOHO publication without scanning many folders.
    days=available_days[-3:]
    found={}
    log(f"SOHO realtime session: fetching {len(days)} recent C3 directory listing(s).")
    for day in days:
        url=day_url(day)
        try:
            page=fetch_text(url)
        except Exception as exc:
            log(f"WARN: could not read {url}: {exc}")
            continue
        for name in explicit_images(page):
            try:
                when=image_time(name)
            except Exception:
                continue
            found[name]=(when,urljoin(url,name))

    if not found:
        log("No C3 images found in the latest published SOHO directories.")
        return []

    latest_available=max(when for when,_ in found.values())
    keep_after=latest_available-dt.timedelta(hours=bootstrap_hours)
    selected={
        name:url
        for name,(when,url) in found.items()
        if when>=keep_after
    }
    log(
        f"Latest available C3 frame is {latest_available:%Y-%m-%d %H:%MZ}; "
        f"using rolling {bootstrap_hours}h window from {keep_after:%Y-%m-%d %H:%MZ}."
    )

    new=[]
    for name in sorted(selected):
        target=cache_dir/name
        if target.exists() and target.stat().st_size>10000:
            continue
        try:
            data=fetch_bytes(selected[name])
            if len(data)<10000:
                raise ValueError(f"response too small ({len(data)} bytes)")
            tmp=target.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(target)
            new.append(target)
        except Exception as exc:
            log(f"WARN: failed {name}: {exc}")

    log(f"Downloaded {len(new)} new C3 frame(s)." if new else "No new C3 frames in this SOHO session.")
    return new

def prune_cache(cache_dir,hours):
    files=sorted(cache_dir.glob("*_c3_512.jpg"))
    newest=latest_cached_time(cache_dir)
    if newest is None:
        return []
    keep_after=newest-dt.timedelta(hours=hours)
    kept=[]
    for p in files:
        try:
            when=image_time(p.name)
        except Exception:
            continue
        if when<keep_after:
            p.unlink(missing_ok=True)
        else:
            kept.append(p)
    return kept

def prune_realtime_reviews(results_dir,max_transient=100,max_age_days=14):
    root=Path(results_dir)/"review"
    if not root.exists():
        return {"removed_dirs":0,"removed_bytes":0,"removed_redundant_bytes":0}

    removed_dirs=0
    removed_bytes=0
    removed_redundant_bytes=0
    transient=[]

    for folder in root.iterdir():
        if not folder.is_dir():
            continue

        # Older review packages contained full-frame videos in addition to the
        # zoom products. They are reproducible from SOHO source imagery and are
        # not needed for realtime review, so remove them even from retained
        # packages.
        for name in ("full_raw.mp4","full_annotated.mp4"):
            p=folder/name
            if p.exists():
                try:
                    removed_redundant_bytes+=p.stat().st_size
                    p.unlink()
                except OSError:
                    pass

        meta={}
        try:
            meta=json.loads((folder/"review.json").read_text(encoding="utf-8"))
        except Exception:
            pass

        status=str(meta.get("status") or "").upper()
        priority=str(meta.get("priority") or "").upper()
        important=(status=="KNOWN_REPORT" or priority=="HIGH")
        if important:
            continue

        try:
            stamp=(folder/"review.json").stat().st_mtime
        except OSError:
            stamp=folder.stat().st_mtime
        transient.append((stamp,folder))

    # Retain only a bounded set of transient MEDIUM/duplicate reviews. Keep the
    # newest max_transient and anything newer than max_age_days only if it fits
    # within that cap. HIGH and KNOWN_REPORT evidence is preserved.
    transient.sort(key=lambda x:x[0],reverse=True)
    cutoff=time.time()-max_age_days*86400
    keep=set(folder for stamp,folder in transient[:max_transient] if stamp>=cutoff)

    for stamp,folder in transient:
        if folder in keep:
            continue
        try:
            size=sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
        except OSError:
            size=0
        shutil.rmtree(folder,ignore_errors=True)
        if not folder.exists():
            removed_dirs+=1
            removed_bytes+=size

    return {
        "removed_dirs":removed_dirs,
        "removed_bytes":removed_bytes,
        "removed_redundant_bytes":removed_redundant_bytes,
    }

def gpu_busy(project):
    try:out=subprocess.check_output(["pgrep","-af","comet_hunter_archive"],text=True)
    except subprocess.CalledProcessError:return False
    me=os.getpid()
    for line in out.splitlines():
        if not line.strip(): continue
        try: pid=int(line.split(None,1)[0])
        except Exception: pid=-1
        if pid!=me:return True
    return False

def wait_for_gpu(project,max_wait):
    deadline=time.time()+max_wait; warned=False
    while gpu_busy(project) and time.time()<deadline and not STOP:
        if not warned: log("GPU detector already running; realtime job waiting."); warned=True
        time.sleep(10)
    return not gpu_busy(project)

def parse_candidates(output):
    lines=output.splitlines(); out=[]; i=0
    while i<len(lines):
        m=CANDIDATE_RE.search(lines[i])
        if not m: i+=1; continue
        item={k:m.group(k) for k in m.groupdict()}; item["frames"]=int(item["frames"])
        for k in ("speed","vx","vy","rms","cv","sunward","score"): item[k]=float(item[k])
        item["first"]=item["last"]=None
        for j in range(i+1,min(i+5,len(lines))):
            pm=POINT_RE.search(lines[j])
            if not pm: continue
            point={"file":pm.group("file"),"x":float(pm.group("x")),"y":float(pm.group("y")),"time":image_time(pm.group("file"))}
            if "first:" in lines[j]: item["first"]=point
            elif "last" in lines[j]: item["last"]=point
        if item["first"] and item["last"]: out.append(item)
        i+=1
    return out

def comet_filter_reasons(c):
    reasons=[]
    if c["frames"]<5: reasons.append("too_few_frames")
    if c["rms"]>1.2: reasons.append("rms_too_high")
    if c["speed"]<0.25: reasons.append("too_slow")
    if abs(c["vx"])>12 or abs(c["vy"])>12: reasons.append("velocity_too_high")
    if c["cv"]>1.2: reasons.append("brightness_cv_too_high")
    if c["sunward"]<1.0: reasons.append("weak_or_outward_motion")
    radial_fraction=(c["sunward"]/c["speed"]) if c["speed"]>0 else -999.0
    if radial_fraction<0.35: reasons.append("weak_sunward_fraction")
    return reasons

def comet_like(c):
    return not comet_filter_reasons(c)

def motion_family_size(c,candidates,velocity_radius=0.35):
    return sum(
        1
        for other in candidates
        if math.hypot(c["vx"]-other["vx"],c["vy"]-other["vy"])<=velocity_radius
    )

def annotate_motion_families(candidates,velocity_radius=0.35):
    for c in candidates:
        c["motion_family_size"]=motion_family_size(c,candidates,velocity_radius)
    return candidates

def live_candidate_rank(c):
    radial_fraction=(c["sunward"]/c["speed"]) if c["speed"]>0 else -1.0
    family_size=max(1,int(c.get("motion_family_size",1)))
    family_penalty=math.log2(family_size)*2.5
    return (
        min(c["frames"],20)*2.0
        + max(0.0,min(c["sunward"],12.0))*4.0
        + max(0.0,min(radial_fraction,1.5))*15.0
        - c["rms"]*12.0
        - c["cv"]*6.0
        - family_penalty
    )

def endpoint_xy_512(c,when):
    first=c["first"]; last=c["last"]
    span=(last["time"]-first["time"]).total_seconds()
    if abs(span)<1:
        return first["x"],first["y"]
    frac=(when-first["time"]).total_seconds()/span
    return (
        first["x"]+(last["x"]-first["x"])*frac,
        first["y"]+(last["y"]-first["y"])*frac,
    )

def event_overlap_metrics(a,b,min_overlap_minutes=60.0,min_overlap_fraction=0.45):
    start=max(a["first"]["time"],b["first"]["time"])
    end=min(a["last"]["time"],b["last"]["time"])
    overlap=(end-start).total_seconds()
    if overlap<min_overlap_minutes*60.0:
        return None

    da=max(1.0,(a["last"]["time"]-a["first"]["time"]).total_seconds())
    db=max(1.0,(b["last"]["time"]-b["first"]["time"]).total_seconds())
    if overlap/min(da,db)<min_overlap_fraction:
        return None

    middle=start+(end-start)/2
    times=(start,middle,end)
    distances=[]
    for when in times:
        ax,ay=endpoint_xy_512(a,when)
        bx,by=endpoint_xy_512(b,when)
        distances.append(math.hypot(ax-bx,ay-by))
    ordered=sorted(distances)
    return {
        "median_distance":ordered[len(ordered)//2],
        "max_distance":max(distances),
        "overlap_hours":overlap/3600.0,
    }

def same_event_hypothesis(a,b,max_median_distance=12.0,max_distance=20.0):
    metrics=event_overlap_metrics(a,b)
    if not metrics:
        return False
    return (
        metrics["median_distance"]<=max_median_distance
        and metrics["max_distance"]<=max_distance
    )

def group_event_hypotheses(candidates):
    groups=[]
    for c in candidates:
        best=None
        for group in groups:
            rep=group["representative"]
            metrics=event_overlap_metrics(rep,c)
            if not metrics:
                continue
            if metrics["median_distance"]>12.0 or metrics["max_distance"]>20.0:
                continue
            key=(metrics["median_distance"],metrics["max_distance"])
            if best is None or key<best[0]:
                best=(key,group)
        if best is None:
            groups.append({"representative":c,"members":[c]})
        else:
            best[1]["members"].append(c)

    for idx,group in enumerate(groups,1):
        rep=group["representative"]
        group_id=f"LEG{idx:03d}_{rep['cid']}"
        member_ids=[m["cid"] for m in group["members"]]
        for member in group["members"]:
            member["event_group"]=group_id
            member["event_members"]=len(group["members"])
            member["event_representative"]=rep["cid"]
            member["event_member_ids"]=member_ids
            member["event_is_representative"]=(member is rep)
    return groups

def strip_html(block):
    block=re.sub(r"(?i)<br\s*/?>","\n",block)
    block=re.sub(r"(?i)</(?:p|div|li|tr|td|h\d)>","\n",block)
    block=re.sub(r"<[^>]+>"," ",block)
    block=html.unescape(block).replace("\xa0"," ")
    return "\n".join(x for x in (re.sub(r"\s+"," ",s).strip() for s in block.splitlines()) if x)

def report_time(date_text,time_text):
    hh,mm=(time_text.split(":",1) if ":" in time_text else (time_text[:-2],time_text[-2:]))
    return dt.datetime.strptime(f"{date_text} {int(hh):02d}:{int(mm):02d}","%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)

def parse_reports(page):
    matches=list(REPORT_ID_RE.finditer(page)); reports=[]; seen=set()
    for idx,m in enumerate(matches):
        rid=m.group(0)
        if rid in seen: continue
        seen.add(rid); end=matches[idx+1].start() if idx+1<len(matches) else len(page)
        text=strip_html(page[m.start():end]); dm=DATE_RE.search(text)
        if not dm or not re.search(r"\bC3\b",text,re.I): continue

        size_match=re.search(r"\b(512|1024)\s*[xX]\s*\1\b",text,re.I)
        image_size=int(size_match.group(1)) if size_match else None
        origin=None
        if re.search(r"\bLower\s*[- ]?Left\b",text,re.I): origin="lower_left"
        elif re.search(r"\bUpper\s*[- ]?Left\b",text,re.I): origin="upper_left"

        pts=[]; previous_when=None
        for pm in REPORT_POINT_RE.finditer(text):
            try:when=report_time(dm.group(1),pm.group("t"))
            except Exception:continue
            if previous_when and when<previous_when-dt.timedelta(hours=12):
                when+=dt.timedelta(days=1)
            previous_when=when
            pts.append((when,float(pm.group("x")),float(pm.group("y"))))
        if len(pts)>=2:
            reports.append({"id":rid,"points":pts,"image_size":image_size,"origin":origin})
    return reports

def candidate_xy_1024(c,when,model="velocity"):
    first=c["first"]; last=c["last"]
    if model=="endpoints":
        span=(last["time"]-first["time"]).total_seconds()
        if abs(span)<1:return 2*first["x"],2*first["y"]
        frac=(when-first["time"]).total_seconds()/span
        return 2*(first["x"]+(last["x"]-first["x"])*frac),2*(first["y"]+(last["y"]-first["y"])*frac)
    hours=(when-first["time"]).total_seconds()/3600
    return 2*(first["x"]+c["vx"]*hours),2*(first["y"]+c["vy"]*hours)

def report_transforms_1024(r):
    if r.get("image_size") in (512,1024):
        sizes=[int(r["image_size"])]
    else:
        max_coord=max((max(x,y) for _,x,y in r.get("points",[])),default=0)
        sizes=[1024] if max_coord>512 else [512,1024]
    origins=[r["origin"]] if r.get("origin") in ("upper_left","lower_left") else ["upper_left","lower_left"]
    return [(size,origin) for size in sizes for origin in origins]

def report_xy_1024(x,y,size,origin):
    scale=1024.0/float(size)
    yy=(float(size)-y) if origin=="lower_left" else y
    return x*scale,yy*scale

def match_recent_report(c,reports,max_error=25):
    best=None
    first_t=c["first"]["time"]; last_t=c["last"]["time"]; pad=dt.timedelta(hours=6)
    for r in reports:
        for model in ("endpoints","velocity"):
            for size,origin in report_transforms_1024(r):
                errs=[]
                for when,x,y in r["points"]:
                    if when<first_t-pad or when>last_t+pad: continue
                    px,py=candidate_xy_1024(c,when,model=model)
                    rx,ry=report_xy_1024(x,y,size,origin)
                    errs.append(math.hypot(px-rx,py-ry))
                if len(errs)<2: continue
                errs.sort(); med=errs[len(errs)//2]; worst=max(errs)
                if med<=max_error and worst<=max_error*1.8 and (best is None or med<best["median_error"]):
                    best={
                        "report_id":r["id"],
                        "median_error":med,
                        "max_error":worst,
                        "points":len(errs),
                        "path_model":model,
                        "report_size":size,
                        "report_origin":origin,
                    }
    return best

def candidate_signature(c):
    return {"time":c["last"]["time"].isoformat(),"x":c["last"]["x"],"y":c["last"]["y"],"vx":c["vx"],"vy":c["vy"]}

def same_track(a,b):
    try:
        ta=dt.datetime.fromisoformat(a["time"]); tb=dt.datetime.fromisoformat(b["time"]); h=(tb-ta).total_seconds()/3600
        px=a["x"]+a["vx"]*h; py=a["y"]+a["vy"]*h
        return math.hypot(px-b["x"],py-b["y"])<=8 and math.hypot(a["vx"]-b["vx"],a["vy"]-b["vy"])<=2 and abs(h)<=18
    except Exception:return False

def load_json(path,default):
    try:return json.loads(path.read_text())
    except Exception:return default

def append_tsv(path,header,row):
    exists=path.exists()
    with path.open("a",encoding="utf-8") as f:
        if not exists:f.write("\t".join(header)+"\n")
        f.write("\t".join(str(x) for x in row)+"\n")

def write_heartbeat(results_dir,status="ok",new_frames=0,error=""):
    last_frame=""
    try:
        last_frame=(results_dir/"last_detector_frame.txt").read_text().strip()
    except Exception:
        pass
    payload={
        "checked_at":dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z"),
        "status":status,
        "new_frames":int(new_frames),
        "last_frame":last_frame or None,
        "error":error or None,
    }
    tmp=results_dir/"heartbeat.json.tmp"
    tmp.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    tmp.replace(results_dir/"heartbeat.json")

def run_detector(project,cache_dir,results_dir,args):
    frames=prune_cache(cache_dir,args.window_hours)
    if len(frames)<args.min_frames:
        log(f"Only {len(frames)} recent frame(s); waiting for at least {args.min_frames}."); return
    newest=frames[-1].name; last_run=results_dir/"last_detector_frame.txt"
    if last_run.exists() and last_run.read_text().strip()==newest:
        log("Detector already processed newest frame."); return
    if not wait_for_gpu(project,args.gpu_wait_seconds):
        log("WARN: GPU remained busy; retry next cycle."); return
    binary=project/"comet_hunter_archive"
    run_id=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path=results_dir/f"detector_{run_id}.log"
    cmd=[str(binary),"--camera","c3","--resolution","512","--input-dir",str(cache_dir),"--budget-minutes",str(args.compute_minutes),"--min-frames",str(args.min_frames),"--max-candidates",str(args.max_candidates)]
    log(f"Running realtime CUDA detector on {len(frames)} frame(s).")
    proc=subprocess.run(cmd,cwd=project,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    log_path.write_text(proc.stdout,encoding="utf-8")
    if proc.returncode!=0:
        log(f"WARN: CUDA detector exited {proc.returncode}; see {log_path}"); return
    last_run.write_text(newest)

    latest_frame = results_dir / "latest_frame.jpg"
    try:
        tmp_frame = results_dir / "latest_frame.jpg.part"
        shutil.copy2(frames[-1], tmp_frame)
        tmp_frame.replace(latest_frame)
        log(f"Saved dashboard preview for processed frame: {newest}")
    except Exception as exc:
        log(f"WARN: could not save latest processed frame preview: {exc}")

    raw_candidates=parse_candidates(proc.stdout)
    rejection_counts={}
    for c in raw_candidates:
        for reason in comet_filter_reasons(c):
            rejection_counts[reason]=rejection_counts.get(reason,0)+1

    candidates=[c for c in raw_candidates if comet_like(c)]
    annotate_motion_families(candidates)
    candidates=sorted(candidates,key=live_candidate_rank,reverse=True)
    event_groups=group_event_hypotheses(candidates)
    event_representatives=[g["representative"] for g in event_groups]

    # Historical-style second-stage verification: inspect the actual pixels
    # along each grouped event trajectory before it reaches review/alerting.
    gray_cache={}
    verified_representatives=[]
    rejected_representatives=[]
    for c in verified_representatives:
        pixel=verify_track_pixels(c,frames,gray_cache=gray_cache)
        review_class,historical_like=classify_for_review(c,pixel)
        c["pixel_verification"]=pixel
        c["review_class"]=review_class
        c["historical_recovery_profile"]=historical_like
        if review_class=="VISUAL_REJECT":
            rejected_representatives.append(c)
        else:
            verified_representatives.append(c)

    stats={
        "run_id":run_id,
        "newest_frame":newest,
        "frames_scanned":len(frames),
        "raw_tracks_parsed":len(raw_candidates),
        "raw_high":sum(1 for c in raw_candidates if str(c.get("priority","")).upper()=="HIGH"),
        "raw_medium":sum(1 for c in raw_candidates if str(c.get("priority","")).upper()=="MEDIUM"),
        "comet_like_tracks":len(candidates),
        "motion_family_radius_px_per_hour":0.35,
        "motion_family_crowded_tracks":sum(1 for c in candidates if c.get("motion_family_size",1)>1),
        "motion_family_max_size":max((c.get("motion_family_size",1) for c in candidates),default=0),
        "event_groups":len(event_groups),
        "event_grouped_tracks":sum(max(0,len(g["members"])-1) for g in event_groups),
        "event_largest_group":max((len(g["members"]) for g in event_groups),default=0),
        "pixel_verified_events":len(verified_representatives),
        "pixel_rejected_events":len(rejected_representatives),
        "pixel_strong_events":sum(1 for c in verified_representatives if c.get("review_class")=="STRONG_REVIEW"),
        "pixel_secondary_events":sum(1 for c in verified_representatives if c.get("review_class")=="SECONDARY"),
        "event_grouping":{
            "min_overlap_minutes":60.0,
            "min_overlap_fraction":0.45,
            "max_median_distance_px_512":12.0,
            "max_distance_px_512":20.0,
            "path_model":"first_to_last_endpoint_interpolation",
        },
        "rejection_counts":rejection_counts,
        "compute_minutes":args.compute_minutes,
        "max_candidates":args.max_candidates,
        "filter":{
            "min_frames":5,
            "max_rms":1.2,
            "min_speed":0.25,
            "max_abs_v":12.0,
            "max_brightness_cv":1.2,
            "min_sunward":1.0,
            "min_sunward_fraction":0.35,
        },
        "updated_at":dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z"),
    }
    (results_dir/"detector_stats.json").write_text(json.dumps(stats,indent=2)+"\n",encoding="utf-8")
    log(f"Realtime detector parsed {len(raw_candidates)} moving track(s); {len(candidates)} survived comet-like filtering.")
    if candidates:
        crowded=sum(1 for c in candidates if c.get("motion_family_size",1)>1)
        largest=max(c.get("motion_family_size",1) for c in candidates)
        log(f"Motion-family diagnostics: radius=0.35 px/h, crowded_tracks={crowded}, largest_family={largest}.")
        grouped=sum(max(0,len(g["members"])-1) for g in event_groups)
        largest_event=max((len(g["members"]) for g in event_groups),default=0)
        log(f"Realtime event grouping: {len(candidates)} tracks -> {len(event_groups)} event group(s); grouped_members={grouped}, largest_event={largest_event}.")
        log(
            f"Pixel verification: {len(verified_representatives)} review event(s), "
            f"{len(rejected_representatives)} visually unsupported event(s)."
        )
    if rejection_counts:
        detail=", ".join(f"{k}={v}" for k,v in sorted(rejection_counts.items(),key=lambda kv:(-kv[1],kv[0])))
        log(f"Realtime filter rejection counts (multi-label): {detail}")
    if not candidates:
        (results_dir/"latest_tracks.json").write_text("[]\n", encoding="utf-8")
        (results_dir/"latest_candidates.json").write_text("[]\n", encoding="utf-8")
        return

    track_rows=[]
    for c in candidates:
        track_rows.append({
            "candidate":c["cid"],
            "priority":c["priority"],
            "frames":c["frames"],
            "rms":c["rms"],
            "speed":c["speed"],
            "vx":c["vx"],
            "vy":c["vy"],
            "sunward":c["sunward"],
            "score":c["score"],
            "motion_family_size":c.get("motion_family_size",1),
            "event_group":c.get("event_group"),
            "event_members":c.get("event_members",1),
            "event_representative":c.get("event_representative",c["cid"]),
            "event_is_representative":bool(c.get("event_is_representative",False)),
            "first":c["first"]["file"],
            "first_x":c["first"]["x"],
            "first_y":c["first"]["y"],
            "last":c["last"]["file"],
            "last_x":c["last"]["x"],
            "last_y":c["last"]["y"],
        })
    (results_dir/"latest_tracks.json").write_text(json.dumps(track_rows,indent=2),encoding="utf-8")

    rejected_rows=[]
    for c in rejected_representatives:
        rejected_rows.append({
            "candidate":c["cid"],
            "priority":c["priority"],
            "review_class":c.get("review_class"),
            "historical_recovery_profile":c.get("historical_recovery_profile",False),
            "pixel_verification":c.get("pixel_verification",{}),
            "event_group":c.get("event_group"),
            "members":c.get("event_members",1),
            "member_ids":c.get("event_member_ids",[c["cid"]]),
            "frames":c["frames"],
            "rms":c["rms"],
            "speed":c["speed"],
            "vx":c["vx"],
            "vy":c["vy"],
            "sunward":c["sunward"],
            "first":c["first"]["file"],
            "last":c["last"]["file"],
        })
    (results_dir/"latest_visual_rejects.json").write_text(
        json.dumps(rejected_rows,indent=2),encoding="utf-8"
    )
    try:
        reports=parse_reports(fetch_text(SUNGRAZER_REPORTS,90))
        log(f"Parsed {len(reports)} recent C3 Sungrazer reports.")
    except Exception as exc:
        reports=[]; log(f"WARN: Sungrazer duplicate check unavailable: {exc}")
    seen_file=results_dir/"seen_tracks.json"; seen=load_json(seen_file,[])
    if not isinstance(seen,list):seen=[]
    rows=[]
    for c in event_representatives:
        sig=candidate_signature(c); dup=any(same_track(old,sig) for old in seen[-500:]); match=match_recent_report(c,reports) if reports else None
        status="KNOWN_REPORT" if match else ("SEEN_ALREADY" if dup else "UNMATCHED")
        rid=match["report_id"] if match else ""; err=f"{match['median_error']:.1f}" if match else ""
        review_id=""
        if not dup:
            try:
                review=ensure_live_review(c,cache_dir,results_dir,status=status,report_id=rid)
                review_id=str(review.get("review_id") or "")
                reused_candidate=str(review.get("candidate") or "")
                if reused_candidate and reused_candidate!=c["cid"] and not match:
                    dup=True; status="SEEN_ALREADY"
                    log(f"Reused live review {review_id} for {c['cid']}; suppressing duplicate alert.")
                elif review_id:
                    log(f"Live review media ready: {review_id}")
            except Exception as exc:
                log(f"WARN: live review media failed for {c['cid']}: {exc}")
        rows.append({
            "candidate":c["cid"],
            "status":status,
            "report_id":rid,
            "match_error_1024":err,
            "review_class":c.get("review_class"),
            "historical_recovery_profile":c.get("historical_recovery_profile",False),
            "pixel_verification":c.get("pixel_verification",{}),
            "first":c["first"]["file"],
            "last":c["last"]["file"],
            "frames":c["frames"],
            "rms":c["rms"],
            "speed":c["speed"],
            "vx":c["vx"],
            "vy":c["vy"],
            "sunward":c["sunward"],
            "score":c["score"],
            "motion_family_size":c.get("motion_family_size",1),
            "event_group":c.get("event_group"),
            "members":c.get("event_members",1),
            "member_ids":c.get("event_member_ids",[c["cid"]]),
            "review_id":review_id,
        })
        if match:
            append_tsv(results_dir/"known_matches.tsv",["utc","candidate","first","last","report_id","median_error_1024"],[run_id,c["cid"],c["first"]["file"],c["last"]["file"],rid,err])
            log(f"Known-report match: {c['cid']} -> {rid} ({err}px @1024, {match.get('path_model','?')}, {match.get('report_size','?')}, {match.get('report_origin','?')})."); continue
        if dup: continue
        seen.append(sig)
        append_tsv(results_dir/"alerts.tsv",["utc","candidate","priority","frames","speed","vx","vy","rms","sunward","score","first","first_x","first_y","last","last_x","last_y","duplicate_check"],[run_id,c["cid"],c["priority"],c["frames"],c["speed"],c["vx"],c["vy"],c["rms"],c["sunward"],c["score"],c["first"]["file"],c["first"]["x"],c["first"]["y"],c["last"]["file"],c["last"]["x"],c["last"]["y"],"no_recent_match" if reports else "check_unavailable"])
        if c.get("review_class")!="STRONG_REVIEW" or str(c.get("priority","")).upper()!="HIGH":
            pixel=c.get("pixel_verification",{})
            log(
                f"Realtime review event: {c.get('event_group')} rep={c['cid']} "
                f"members={c.get('event_members',1)} raw_class={c['priority']} "
                f"review_class={c.get('review_class')} frames={c['frames']} "
                f"RMS={c['rms']:.2f} pixel_hits={pixel.get('hits',0)}/{pixel.get('samples',0)} "
                f"pixel_peak_snr={pixel.get('peak_snr',0)}. "
                "Retained for review; not escalated to comet alert."
            )
            continue
        pixel=c.get("pixel_verification",{})
        alert="\n"+"!"*72+"\nREALTIME COMET ALERT - PIXEL-VERIFIED HIGH-PRIORITY EVENT\n"+f"Candidate: {c['cid']} raw_class={c['priority']} review_class={c.get('review_class')} frames={c['frames']} RMS={c['rms']:.2f}\n"+f"Motion: speed={c['speed']:.2f} px/h vx={c['vx']:.2f} vy={c['vy']:.2f} sunward={c['sunward']:.2f}\n"+f"Pixel evidence: hits={pixel.get('hits',0)}/{pixel.get('samples',0)} hit_fraction={pixel.get('hit_fraction',0):.2f} peak_snr={pixel.get('peak_snr',0):.2f} longest_run={pixel.get('longest_hit_run',0)}\n"+f"Event group: {c.get('event_group')} members={c.get('event_members',1)}\n"+f"Motion family: {c.get('motion_family_size',1)} track(s) within 0.35 px/h\n"+f"First: {c['first']['file']} ({c['first']['x']:.1f},{c['first']['y']:.1f}) [512]\n"+f"Last : {c['last']['file']} ({c['last']['x']:.1f},{c['last']['y']:.1f}) [512]\nACTION: visually inspect and verify independently before reporting.\n"+"!"*72
        log(alert); (results_dir/"latest_alert.txt").write_text(alert+"\n",encoding="utf-8")
    seen_file.write_text(json.dumps(seen[-1000:],indent=2),encoding="utf-8")
    (results_dir/"latest_candidates.json").write_text(json.dumps(rows,indent=2),encoding="utf-8")

def main():
    p=argparse.ArgumentParser(description="Realtime SOHO C3 CUDA comet hunter")
    p.add_argument("--project",required=True); p.add_argument("--poll-seconds",type=int,default=905); p.add_argument("--window-hours",type=int,default=12); p.add_argument("--bootstrap-hours",type=int,default=12); p.add_argument("--compute-minutes",type=int,default=8); p.add_argument("--min-frames",type=int,default=5); p.add_argument("--max-candidates",type=int,default=1000); p.add_argument("--gpu-wait-seconds",type=int,default=720); p.add_argument("--once",action="store_true")
    args=p.parse_args()
    if args.poll_seconds<900:p.error("--poll-seconds must be at least 900")
    project=Path(args.project).expanduser().resolve(); state_dir=project/"state"; results_dir=project/"results"/"realtime"; cache_dir=Path("/dev/shm")/f"comet_realtime_{os.environ.get('USER','user')}_c3_512"
    for d in (state_dir,results_dir,cache_dir):d.mkdir(parents=True,exist_ok=True)
    signal.signal(signal.SIGINT,on_signal); signal.signal(signal.SIGTERM,on_signal)
    log("Realtime hunter starting."); log(f"Project: {project}"); log(f"Rolling cache: {cache_dir}"); log("Do not run an independent SOHO archive/verifier fetcher in parallel.")
    cleanup=prune_realtime_reviews(results_dir)
    reclaimed=cleanup["removed_bytes"]+cleanup["removed_redundant_bytes"]
    if cleanup["removed_dirs"] or reclaimed:
        log(f"Realtime review retention cleanup: removed {cleanup['removed_dirs']} stale review package(s), reclaimed {reclaimed/(1024*1024):.1f} MiB.")
    while not STOP:
        start=time.time()
        try:
            new=download_live_frames(cache_dir,state_dir,args.poll_seconds,args.bootstrap_hours)
            prune_cache(cache_dir,max(args.window_hours,args.bootstrap_hours))
            if new:
                run_detector(project,cache_dir,results_dir,args)
                cleanup=prune_realtime_reviews(results_dir)
                reclaimed=cleanup["removed_bytes"]+cleanup["removed_redundant_bytes"]
                if cleanup["removed_dirs"] or reclaimed:
                    log(f"Realtime review retention cleanup: removed {cleanup['removed_dirs']} stale review package(s), reclaimed {reclaimed/(1024*1024):.1f} MiB.")
            write_heartbeat(results_dir,status="ok",new_frames=len(new))
        except Exception as exc:
            write_heartbeat(results_dir,status="error",error=str(exc))
            log(f"ERROR: realtime cycle failed: {exc}")
        if args.once:break
        sleep_for=max(5,args.poll_seconds-(time.time()-start)); log(f"Next realtime check in about {int(sleep_for)}s.")
        end=time.time()+sleep_for
        while time.time()<end and not STOP: time.sleep(min(10,end-time.time()))
    log("Realtime hunter stopped."); return 0
if __name__=="__main__": raise SystemExit(main())
