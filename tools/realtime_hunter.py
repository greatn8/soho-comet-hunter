#!/usr/bin/env python3
"""Realtime SOHO/LASCO C3 comet hunter supervisor."""

from __future__ import annotations
import argparse, datetime as dt, fcntl, html, json, math, os, re, shutil, signal, subprocess, time
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from live_review import ensure_live_review

SOHO_BASE="https://soho.nascom.nasa.gov/data/REPROCESSING/Completed"
SUNGRAZER_REPORTS="https://sungrazer.nrl.navy.mil/index.php/plain-text-reports?items_per_page=100&order=field_report_date&sort=desc"
USER_AGENT="CometHunterRealtime/1.0 (SOHO comet-hunting research)"
IMAGE_RE=re.compile(r"(?P<name>20\d{6}_\d{4}_c3_512\.jpg)",re.I)
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

def explicit_images(index_html):
    return sorted(set(m.group("name") for m in IMAGE_RE.finditer(index_html)))

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
    days=[now.date()]
    if not list(cache_dir.glob("*_c3_512.jpg")) or now.hour<max(2,bootstrap_hours//2):
        days.append((now-dt.timedelta(days=1)).date())
    found={}
    keep_after=now-dt.timedelta(hours=bootstrap_hours)
    log("SOHO realtime session: fetching explicit C3 directory listing(s).")
    for day in days:
        url=day_url(day)
        try:page=fetch_text(url)
        except Exception as exc:
            log(f"WARN: could not read {url}: {exc}"); continue
        for name in explicit_images(page):
            try:
                if image_time(name)<keep_after:
                    continue
            except Exception:
                continue
            found[name]=urljoin(url,name)
    new=[]
    for name in sorted(found):
        target=cache_dir/name
        if target.exists() and target.stat().st_size>10000: continue
        try:
            data=fetch_bytes(found[name])
            if len(data)<10000: raise ValueError(f"response too small ({len(data)} bytes)")
            tmp=target.with_suffix(".part"); tmp.write_bytes(data); tmp.replace(target); new.append(target)
        except Exception as exc: log(f"WARN: failed {name}: {exc}")
    log(f"Downloaded {len(new)} new C3 frame(s)." if new else "No new C3 frames in this SOHO session.")
    return new

def prune_cache(cache_dir,hours):
    now=dt.datetime.now(dt.timezone.utc); keep_after=now-dt.timedelta(hours=hours); kept=[]
    for p in sorted(cache_dir.glob("*_c3_512.jpg")):
        try:when=image_time(p.name)
        except Exception:continue
        if when<keep_after:p.unlink(missing_ok=True)
        else:kept.append(p)
    return kept

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
        pts=[]
        for pm in REPORT_POINT_RE.finditer(text):
            try:when=report_time(dm.group(1),pm.group("t"))
            except Exception:continue
            pts.append((when,float(pm.group("x")),float(pm.group("y"))))
        if len(pts)>=2: reports.append({"id":rid,"points":pts})
    return reports

def candidate_xy_1024(c,when):
    first=c["first"]; hours=(when-first["time"]).total_seconds()/3600
    return 2*(first["x"]+c["vx"]*hours),2*(first["y"]+c["vy"]*hours)

def match_recent_report(c,reports,max_error=25):
    best=None
    for r in reports:
        errs=[]
        for when,x,y in r["points"]:
            if abs((when-c["first"]["time"]).total_seconds())/3600>48: continue
            px,py=candidate_xy_1024(c,when); errs.append(math.hypot(px-x,py-y))
        if len(errs)<2: continue
        errs.sort(); med=errs[len(errs)//2]; worst=max(errs)
        if med<=max_error and worst<=max_error*1.8 and (best is None or med<best["median_error"]):
            best={"report_id":r["id"],"median_error":med,"max_error":worst,"points":len(errs)}
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
    if rejection_counts:
        detail=", ".join(f"{k}={v}" for k,v in sorted(rejection_counts.items(),key=lambda kv:(-kv[1],kv[0])))
        log(f"Realtime filter rejection counts (multi-label): {detail}")
    if not candidates:
        (results_dir/"latest_candidates.json").write_text("[]\n", encoding="utf-8")
        return
    try:
        reports=parse_reports(fetch_text(SUNGRAZER_REPORTS,90))
        log(f"Parsed {len(reports)} recent C3 Sungrazer reports.")
    except Exception as exc:
        reports=[]; log(f"WARN: Sungrazer duplicate check unavailable: {exc}")
    seen_file=results_dir/"seen_tracks.json"; seen=load_json(seen_file,[])
    if not isinstance(seen,list):seen=[]
    rows=[]
    for c in candidates:
        sig=candidate_signature(c); dup=any(same_track(old,sig) for old in seen[-500:]); match=match_recent_report(c,reports) if reports else None
        status="KNOWN_REPORT" if match else ("SEEN_ALREADY" if dup else "UNMATCHED")
        rid=match["report_id"] if match else ""; err=f"{match['median_error']:.1f}" if match else ""
        review_id=""
        if not dup:
            try:
                review=ensure_live_review(c,cache_dir,results_dir,status=status,report_id=rid)
                review_id=str(review.get("review_id") or "")
                if review_id: log(f"Live review media ready: {review_id}")
            except Exception as exc:
                log(f"WARN: live review media failed for {c['cid']}: {exc}")
        rows.append({"candidate":c["cid"],"status":status,"report_id":rid,"match_error_1024":err,"first":c["first"]["file"],"last":c["last"]["file"],"frames":c["frames"],"rms":c["rms"],"speed":c["speed"],"vx":c["vx"],"vy":c["vy"],"sunward":c["sunward"],"score":c["score"],"motion_family_size":c.get("motion_family_size",1),"review_id":review_id})
        if match:
            append_tsv(results_dir/"known_matches.tsv",["utc","candidate","first","last","report_id","median_error_1024"],[run_id,c["cid"],c["first"]["file"],c["last"]["file"],rid,err])
            log(f"Known-report match: {c['cid']} -> {rid} ({err}px @1024)."); continue
        if dup: continue
        seen.append(sig)
        append_tsv(results_dir/"alerts.tsv",["utc","candidate","priority","frames","speed","vx","vy","rms","sunward","score","first","first_x","first_y","last","last_x","last_y","duplicate_check"],[run_id,c["cid"],c["priority"],c["frames"],c["speed"],c["vx"],c["vy"],c["rms"],c["sunward"],c["score"],c["first"]["file"],c["first"]["x"],c["first"]["y"],c["last"]["file"],c["last"]["x"],c["last"]["y"],"no_recent_match" if reports else "check_unavailable"])
        alert="\n"+"!"*72+"\nREALTIME COMET ALERT - NO MATCH IN RECENT SUNGRAZER REPORTS\n"+f"Candidate: {c['cid']} class={c['priority']} frames={c['frames']} RMS={c['rms']:.2f}\n"+f"Motion: speed={c['speed']:.2f} px/h vx={c['vx']:.2f} vy={c['vy']:.2f} sunward={c['sunward']:.2f}\n"+f"Motion family: {c.get('motion_family_size',1)} track(s) within 0.35 px/h\n"+f"First: {c['first']['file']} ({c['first']['x']:.1f},{c['first']['y']:.1f}) [512]\n"+f"Last : {c['last']['file']} ({c['last']['x']:.1f},{c['last']['y']:.1f}) [512]\nACTION: visually inspect immediately before reporting.\n"+"!"*72
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
    while not STOP:
        start=time.time()
        try:
            new=download_live_frames(cache_dir,state_dir,args.poll_seconds,args.bootstrap_hours)
            prune_cache(cache_dir,max(args.window_hours,args.bootstrap_hours))
            if new: run_detector(project,cache_dir,results_dir,args)
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
