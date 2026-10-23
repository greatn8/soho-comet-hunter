#!/usr/bin/env python3
"""Realtime SOHO/LASCO C3 comet hunter supervisor."""

from __future__ import annotations
import argparse, datetime as dt, fcntl, html, json, math, os, re, shutil, signal, subprocess, tempfile, threading, time
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from live_review import ensure_live_review
from early_c3_review import process_early_tracks
import pixel_verify
from pixel_verify import classify_for_review, verify_track_pixels, verify_temporal_motion
from track_evidence import audit_track_frames, position_at
from star_field import annotate_star_field
from image_star_motion import build_reference, evidence_for_velocity
from edge_review import edge_review_evidence

SOHO_BASE="https://soho.nascom.nasa.gov/data/REPROCESSING/Completed"
SOHO_NRT_GIF_BASE="https://soho.nascom.nasa.gov/data/realtime/javagif/gifs/"
SUNGRAZER_REPORTS="https://sungrazer.nrl.navy.mil/index.php/plain-text-reports?items_per_page=100&order=field_report_date&sort=desc"
USER_AGENT="CometHunterRealtime/1.0 (SOHO comet-hunting research)"
IMAGE_RE=re.compile(r"(?P<name>20\d{6}_\d{4}_c3_(?:512|1024)\.jpg)",re.I)
NRT_GIF_RE=re.compile(r"(?P<name>20\d{6}_\d{4}_c3\.gif)",re.I)
DAY_DIR_RE=re.compile(r"(?P<day>20\d{6})/",re.I)
CANDIDATE_RE=re.compile(r"^(?P<cid>C\d+) \[(?P<priority>HIGH|MEDIUM)\].*?frames=(?P<frames>\d+).*?speed=(?P<speed>-?[0-9.]+) px/h.*?vx=(?P<vx>-?[0-9.]+).*?vy=(?P<vy>-?[0-9.]+).*?RMS=(?P<rms>-?[0-9.]+).*?brightnessCV=(?P<cv>-?[0-9.]+).*?sunward=(?P<sunward>-?[0-9.]+) px/h.*?score=(?P<score>-?[0-9.]+)",re.I)
POINT_RE=re.compile(r"(?P<file>20\d{6}_\d{4}_c3_(?:512|1024)\.jpg)\s+\((?P<x>-?[0-9.]+),\s*(?P<y>-?[0-9.]+)\)")
REPORT_ID_RE=re.compile(r"report_[a-z0-9]+_\d{14}",re.I)
REPORT_POINT_RE=re.compile(r"(?m)^\s*(?P<t>\d{1,2}:\d{2}|\d{4})\s+(?P<x>\d{1,4})\s+(?P<y>\d{1,4})\s*$")
DATE_RE=re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
STOP=False
HEARTBEAT_LOCK=threading.RLock()
# Revalidate a cached frame only if its size or modification time changes.
VALIDATED_FRAMES=set()

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

def explicit_images(index_html, resolution=512):
    suffix=f"_c3_{resolution}.jpg"
    return sorted(set(m.group("name") for m in IMAGE_RE.finditer(index_html) if m.group("name").lower().endswith(suffix)))

def explicit_nrt_gifs(index_html):
    return sorted(set(m.group("name") for m in NRT_GIF_RE.finditer(index_html)))

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

def discover_completed_frames(now,lookback_days=14,resolution=512):
    available_days=discover_available_days(now,lookback_days=lookback_days)
    if not available_days:
        return {}

    found={}
    for day in available_days:
        url=day_url(day)
        try:
            page=fetch_text(url)
        except Exception as exc:
            log(f"WARN: could not read completed C3 directory {url}: {exc}")
            continue
        for name in explicit_images(page,resolution=resolution):
            try:
                when=image_time(name)
            except Exception:
                continue
            found[name]={"time":when,"url":urljoin(url,name),"source":"completed"}
    return found

def discover_nrt_frames(now,lookback_days=7,resolution=512):
    cutoff=now-dt.timedelta(days=lookback_days)
    found={}
    url=SOHO_NRT_GIF_BASE
    try:
        page=fetch_text(url)
    except Exception as exc:
        log(f"WARN: could not read NRL near-realtime C3 index {url}: {exc}")
        return found

    for gif_name in explicit_nrt_gifs(page):
        try:
            when=image_time(gif_name)
        except Exception:
            continue
        if when<cutoff or when>now+dt.timedelta(hours=2):
            continue
        target_name=gif_name[:-4]+f"_{resolution}.jpg"
        found[target_name]={
            "time":when,
            "url":urljoin(url,gif_name),
            "source":"near_realtime",
            "remote_name":gif_name,
        }
    return found

def latest_cached_time(cache_dir,resolution=512):
    latest=None
    for p in cache_dir.glob(f"*_c3_{resolution}.jpg"):
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

def write_nrt_jpeg(data,target,resolution=512):
    """Convert the earliest official GIF, preserving native 1024 detail.

    The 512 workflow stays unchanged. 1024 is never synthesized by
    upsampling a smaller GIF: reject mismatches before decoding.
    """
    if resolution not in (512,1024):
        raise ValueError("Unsupported realtime image resolution")
    if resolution==1024:
        probe=subprocess.run(
            ["ffprobe","-v","error","-select_streams","v:0",
             "-show_entries","stream=width,height","-of","csv=p=0",
             "-i","pipe:0"],
            input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
        dimensions=probe.stdout.decode("utf-8","replace").strip().split(",")
        if probe.returncode!=0 or dimensions!=["1024","1024"]:
            raise ValueError(
                "Near-realtime SOHO GIF is not native 1024x1024; "
                f"received {probe.stdout.decode('utf-8','replace').strip()!r}. "
                "Never upscale lower-resolution quicklook images.")
    tmp=target.with_name(target.name+".tmp.jpg")
    try:
        command=["ffmpeg","-hide_banner","-loglevel","error","-y",
                 "-i","pipe:0"]
        if resolution==512:
            command.extend(["-vf","scale=512:512:flags=lanczos"])
        command.extend(["-frames:v","1","-q:v","2",str(tmp)])
        proc=subprocess.run(
            command,input=data,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,timeout=90)
        if proc.returncode!=0 or not tmp.exists() or tmp.stat().st_size<10000:
            detail=proc.stderr.decode("utf-8","replace").strip()
            raise RuntimeError(detail or "ffmpeg could not convert realtime GIF")
        validate_source_resolution(tmp,resolution)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)

def validate_source_resolution(path, resolution):
    """Refuse mislabeled, corrupt, or upscaled image input."""
    probe=subprocess.run(
        ["ffprobe","-v","error","-select_streams","v:0",
         "-show_entries","stream=width,height","-of","csv=p=0",str(path)],
        capture_output=True,text=True,timeout=20)
    if probe.returncode:
        raise ValueError("ffprobe could not decode SOHO source image")
    dims=probe.stdout.strip().split(",")
    if dims != [str(resolution),str(resolution)]:
        raise ValueError(f"Expected native {resolution}x{resolution}, found {probe.stdout.strip()}")


def prioritize_new_frames(needed,resolution,max_live_batch=90):
    """Fill the freshest native 1024 frames first without delaying CUDA.

    Existing 512 cache behaviour remains unchanged. The remaining archive
    context is filled incrementally during subsequent SOHO polling sessions,
    keeping the 12/48/336-hour search windows available as they accumulate.
    """
    if resolution==1024:
        if max_live_batch<5:
            raise ValueError("Native 1024 batch must allow >=5 frames")
        return sorted(needed,reverse=True)[:max_live_batch]
    return sorted(needed)


def download_live_frames(cache_dir,state_dir,poll_seconds,bootstrap_hours,results_dir=None,resolution=512):
    soho_gate_wait(state_dir,poll_seconds)
    if STOP:return []
    now=dt.datetime.now(dt.timezone.utc)

    # Discovery-first policy: inspect the official near-real-time quicklook
    # feed and the slower reprocessed archive in the same gated SOHO session.
    log("SOHO realtime session: checking near-real-time C3 feed and completed archive.")
    # The official near-realtime C3 GIF feed carries native 1024 imagery.
    # Probe each downloaded GIF before conversion so a lower-resolution
    # quicklook can never be mislabeled as a genuine 1024 JPEG.
    nrt=discover_nrt_frames(now,lookback_days=7,resolution=resolution)
    completed=discover_completed_frames(now,lookback_days=14,resolution=resolution)

    latest_nrt=max((v["time"] for v in nrt.values()),default=None)
    latest_completed=max((v["time"] for v in completed.values()),default=None)

    if latest_nrt:
        log(f"Latest near-real-time C3 frame: {latest_nrt:%Y-%m-%d %H:%MZ}.")
    else:
        log("WARN: no recent near-real-time C3 frames found.")
    if latest_completed:
        log(f"Latest completed/reprocessed C3 frame: {latest_completed:%Y-%m-%d %H:%MZ}.")
    else:
        log("WARN: no recent completed C3 frames found.")

    # Historical-parity realtime mode: keep the long rolling context that
    # produced the successful archive recoveries. Completed/reprocessed frames
    # form the science-quality base; only timestamps not yet present there are
    # filled from the official near-realtime feed.
    if not latest_completed and not latest_nrt:
        log("No usable C3 images found in either official source.")
        return []

    latest_available=max(x for x in (latest_completed,latest_nrt) if x is not None)
    source_name=(
        "near_realtime"
        if latest_nrt and (not latest_completed or latest_nrt>latest_completed)
        else "completed"
    )

    found=dict(completed)
    for name,meta in nrt.items():
        if name not in found:
            found[name]=meta

    keep_after=latest_available-dt.timedelta(hours=bootstrap_hours)
    selected={
        name:meta for name,meta in found.items()
        if meta["time"]>=keep_after
    }

    source_status={
        "checked_at":now.isoformat().replace("+00:00","Z"),
        "active_source":source_name,
        "resolution":resolution,
        "active_latest_frame":latest_available.isoformat().replace("+00:00","Z"),
        "near_realtime_latest":latest_nrt.isoformat().replace("+00:00","Z") if latest_nrt else None,
        "completed_latest":latest_completed.isoformat().replace("+00:00","Z") if latest_completed else None,
        "completed_lag_minutes":(
            round((latest_nrt-latest_completed).total_seconds()/60.0,1)
            if latest_nrt and latest_completed and latest_nrt>=latest_completed else 0.0
        ),
        "preliminary_input":source_name=="near_realtime",
    }
    (state_dir/"realtime_input_source.json").write_text(
        json.dumps(source_status,indent=2)+"\n",encoding="utf-8"
    )
    if results_dir is not None:
        (Path(results_dir)/"input_source.json").write_text(
            json.dumps(source_status,indent=2)+"\n",encoding="utf-8"
        )

    log(
        f"Active C3 source: {source_name}; historical-parity rolling "
        f"{bootstrap_hours}h window from {keep_after:%Y-%m-%d %H:%MZ} "
        f"({len(selected)} indexed frame(s), Completed base + newer realtime fill)."
    )

    new=[]
    needed=[]
    for name in sorted(selected):
        cached=cache_dir/name
        if cached.exists() and cached.stat().st_size>10000:
            try:
                stat=cached.stat()
                fingerprint=(str(cached),stat.st_size,stat.st_mtime_ns,resolution)
                if fingerprint not in VALIDATED_FRAMES:
                    validate_source_resolution(cached,resolution)
                    if len(VALIDATED_FRAMES)>=12000:
                        VALIDATED_FRAMES.clear()
                    VALIDATED_FRAMES.add(fingerprint)
                continue
            except (OSError,ValueError):
                cached.unlink(missing_ok=True)
        needed.append(name)
    # A fresh 14-day 1024 cache can require thousands of JPEG requests.
    # Process the newest live frames in the FIRST cycle instead of blocking
    # detection while downloading older historical frames for hours.
    total_needed=len(needed)
    needed=prioritize_new_frames(needed,resolution)
    if resolution==1024 and total_needed>len(needed):
        log(f"Native 1024 freshness priority: fetching {len(needed)} newest frame(s) "
            f"this cycle; deferring {total_needed-len(needed)} older frame(s) "
            "to later 15-minute sessions.")
    if results_dir is not None and needed:
        write_heartbeat(
            Path(results_dir),
            status="bootstrapping",
            phase="Building 14-day rolling C3 cache",
            progress={"needed":len(needed),"downloaded":0,"indexed":len(selected)},
        )

    for download_index,name in enumerate(needed,1):
        target=cache_dir/name
        try:
            meta=selected[name]
            data=fetch_bytes(meta["url"])
            if len(data)<10000:
                raise ValueError(f"response too small ({len(data)} bytes)")
            if meta["source"]=="near_realtime":
                write_nrt_jpeg(data,target,resolution=resolution)
            else:
                tmp=target.with_suffix(".part")
                tmp.write_bytes(data)
                validate_source_resolution(tmp,resolution)
                tmp.replace(target)
            new.append(target)
        except Exception as exc:
            (target.with_suffix(".part")).unlink(missing_ok=True)
            log(f"WARN: failed {name} from {source_name}: {exc}")

        if results_dir is not None and (download_index==len(needed) or download_index%25==0):
            write_heartbeat(
                Path(results_dir),
                status="bootstrapping",
                new_frames=len(new),
                phase="Building 14-day rolling C3 cache",
                progress={
                    "needed":len(needed),
                    "downloaded":download_index,
                    "successful":len(new),
                    "indexed":len(selected),
                },
            )

    log(
        f"Downloaded {len(new)} new C3 frame(s) from {source_name}."
        if new else
        f"No new C3 frames from active source {source_name} in this SOHO session."
    )
    return new

def prune_cache(cache_dir,hours,resolution=512):
    files=sorted(cache_dir.glob(f"*_c3_{resolution}.jpg"))
    newest=latest_cached_time(cache_dir,resolution)
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
        for name in ("full_raw.mp4",):
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
        review_class=str(meta.get("review_class") or "").upper()
        # A MEDIUM-priority raw CUDA track may later pass pixel verification
        # and become a STRONG_REVIEW. Protect all such videos until archival.
        important=(status=="KNOWN_REPORT" or priority=="HIGH" or review_class=="STRONG_REVIEW")
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
        # A candidate owns only its own endpoint lines. Never scan into the
        # next candidate: that previously replaced a missing C317 first point
        # with C318 first, silently creating a fictitious moving trajectory.
        for j in range(i+1,min(i+8,len(lines))):
            line=lines[j]
            if CANDIDATE_RE.search(line):
                break
            pm=POINT_RE.search(line)
            if not pm:
                continue
            # CUDA emits "first:" and "last :" (space before colon).
            # Match only the label preceding the coordinate point, allowing
            # both formats without accidentally reading another track.
            label=re.fullmatch(r"(first|last)\s*:",line[:pm.start()].strip().lower())
            if label is None:
                continue
            key=label.group(1)
            if item[key] is not None:
                continue
            point={"file":pm.group("file"),"x":float(pm.group("x")),"y":float(pm.group("y")),"time":image_time(pm.group("file"))}
            item[key]=point
            if item["first"] is not None and item["last"] is not None:
                break
        if item["first"] and item["last"]:
            # CUDA endpoint labels are not guaranteed to be chronological.
            # Correct the metadata before pixel checks, matching or video review.
            item["endpoint_order_corrected"]=False
            if item["first"]["time"]>item["last"]["time"]:
                item["first"],item["last"]=item["last"],item["first"]
                item["endpoint_order_corrected"]=True
            span=(item["last"]["time"]-item["first"]["time"]).total_seconds()/3600.0
            item["trajectory_warning"]=""
            if span<=0:
                item["trajectory_warning"]="ENDPOINTS_SAME_TIMESTAMP"
            else:
                displacement_error=math.hypot(
                    item["last"]["x"]-item["first"]["x"]-item["vx"]*span,
                    item["last"]["y"]-item["first"]["y"]-item["vy"]*span,
                )
                if displacement_error>max(12.0,span*1.5)*(1024/512 if "_c3_1024.jpg" in item["first"]["file"] else 1):
                    item["trajectory_warning"]="ENDPOINT_VELOCITY_MISMATCH"
            out.append(item)
        i+=1
    return out

def comet_filter_reasons(c):
    reasons=[]
    if c["frames"]<5: reasons.append("too_few_frames")
    if c["rms"]>1.2*c.get("resolution",512)/512.0: reasons.append("rms_too_high")
    if c["speed"]<0.25*c.get("resolution",512)/512.0: reasons.append("too_slow")
    if abs(c["vx"])>12*c.get("resolution",512)/512.0 or abs(c["vy"])>12*c.get("resolution",512)/512.0: reasons.append("velocity_too_high")
    if c["cv"]>1.2: reasons.append("brightness_cv_too_high")
    if c["sunward"]<1.0*c.get("resolution",512)/512.0: reasons.append("weak_or_outward_motion")
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
        c["motion_family_size"]=motion_family_size(c,candidates,velocity_radius * c.get("resolution",512)/512.0)
    return candidates

def candidate_quadrant(c,center_x=None,center_y=None):
    center_x = c.get("resolution",512)/2 if center_x is None else center_x
    center_y = c.get("resolution",512)/2 if center_y is None else center_y
    """Quadrant of the candidate midpoint in image coordinates."""
    try:
        x=(float(c["first"]["x"])+float(c["last"]["x"]))/2.0
        y=(float(c["first"]["y"])+float(c["last"]["y"]))/2.0
    except Exception:
        return "UNKNOWN"
    if x<center_x and y<center_y:return "UPPER_LEFT"
    if x>=center_x and y<center_y:return "UPPER_RIGHT"
    if x<center_x and y>=center_y:return "LOWER_LEFT"
    return "LOWER_RIGHT"

def quadrant_counts(candidates):
    out={"UPPER_LEFT":0,"UPPER_RIGHT":0,"LOWER_LEFT":0,"LOWER_RIGHT":0,"UNKNOWN":0}
    for c in candidates:
        q=candidate_quadrant(c)
        out[q]=out.get(q,0)+1
    return out

def velocity_sign_counts(candidates):
    out={"PLUS_PLUS":0,"PLUS_MINUS":0,"MINUS_PLUS":0,"MINUS_MINUS":0,"ZERO_AXIS":0}
    for c in candidates:
        vx=float(c.get("vx",0.0)); vy=float(c.get("vy",0.0))
        if vx==0 or vy==0:
            out["ZERO_AXIS"]+=1
        elif vx>0 and vy>0:
            out["PLUS_PLUS"]+=1
        elif vx>0 and vy<0:
            out["PLUS_MINUS"]+=1
        elif vx<0 and vy>0:
            out["MINUS_PLUS"]+=1
        else:
            out["MINUS_MINUS"]+=1
    return out

def live_candidate_rank(c):
    # Rank physical tracks identically at 512 and 1024 image resolution.
    # Coordinates and pixel/hour velocities double in 1024 imagery.
    scale=c.get("resolution",512)/512.0
    sunward=c["sunward"]/scale
    rms=c["rms"]/scale
    radial_fraction=(c["sunward"]/c["speed"]) if c["speed"]>0 else -1.0
    family_size=max(1,int(c.get("motion_family_size",1)))
    family_penalty=math.log2(family_size)*2.5
    return (
        min(c["frames"],20)*2.0
        + max(0.0,min(sunward,12.0))*4.0
        + max(0.0,min(radial_fraction,1.5))*15.0
        - rms*12.0
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
            if metrics["median_distance"]>12.0*rep.get("resolution",512)/512.0 or metrics["max_distance"]>20.0*rep.get("resolution",512)/512.0:
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
        if abs(span)<1:return (1024.0/c.get("resolution",512))*first["x"],(1024.0/c.get("resolution",512))*first["y"]
        frac=(when-first["time"]).total_seconds()/span
        return (1024.0/c.get("resolution",512))*(first["x"]+(last["x"]-first["x"])*frac),(1024.0/c.get("resolution",512))*(first["y"]+(last["y"]-first["y"])*frac)
    hours=(when-first["time"]).total_seconds()/3600
    scale=1024.0/c.get("resolution",512)
    return scale*(first["x"]+c["vx"]*hours),scale*(first["y"]+c["vy"]*hours)

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

def _heartbeat_save(results_dir,payload):
    # Process-specific temp filenames prevent the realtime worker and a
    # one-time cached recheck from clobbering one another's temp file.
    tmp=results_dir/f"heartbeat.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    tmp.replace(results_dir/"heartbeat.json")


def write_heartbeat(results_dir,status="ok",new_frames=0,error="",phase="",progress=None):
    with HEARTBEAT_LOCK:
        last_frame=""
        try:
            last_frame=(results_dir/"last_detector_frame.txt").read_text().strip()
        except Exception:
            pass
        now=dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z")
        previous=load_json(results_dir/"heartbeat.json",{})
        if not isinstance(previous,dict):
            previous={}
        same_phase=(previous.get("status")==status
                    and previous.get("phase")==(phase or None))
        payload={
            "checked_at":now,
            "status":status,
            "phase":phase or None,
            "phase_started_at":previous.get("phase_started_at",now) if same_phase else now,
            "progress":progress if isinstance(progress,dict) else None,
            "new_frames":int(new_frames),
            "last_frame":last_frame or None,
            "error":error or None,
        }
        _heartbeat_save(results_dir,payload)


def pulse_heartbeat(results_dir):
    """Show process liveness without changing last successful detector frame."""
    with HEARTBEAT_LOCK:
        previous=load_json(results_dir/"heartbeat.json",{})
        if not isinstance(previous,dict) or not previous:
            return
        # A repeatedly failing worker must not masquerade as a healthy one.
        if previous.get("status") in ("error","stopped"):
            return
        previous["checked_at"]=dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z")
        _heartbeat_save(results_dir,previous)


def heartbeat_keepalive(results_dir,stop_event,interval=120):
    while not stop_event.wait(interval):
        try:
            pulse_heartbeat(results_dir)
        except Exception as exc:
            log(f"WARN: heartbeat keepalive failed: {exc}")


def select_recent_frames(frames, window_hours):
    """Select a window relative to the newest available SOHO frame, not wall time."""
    if not frames:
        return []
    newest=image_time(frames[-1].name)
    cutoff=newest-dt.timedelta(hours=window_hours)
    return [p for p in frames if image_time(p.name)>=cutoff]


def run_multiscale_cuda(project, frames, results_dir, args, run_id, cache_dir):
    """Independent CUDA searches on the same cached input images.

    This intentionally does not change the CUDA detector or refetch SOHO data.
    Short passes cannot be crowded out by implausible multi-day tracks.
    """
    all_candidates=[]
    runs=[]
    # Explicit marker persists across tmux/watchdog restarts. The flag
    # requires the separately compiled experimental CUDA detector.
    stationary_filter=(project/"state"/"realtime_cuda_stationary_enabled").is_file()
    enhanced_filter=(project/"state"/"realtime_cuda_enhanced_enabled").is_file()
    effective_max_candidates=max(args.max_candidates,500) if enhanced_filter else args.max_candidates
    budgets=(args.short_budget_minutes,args.medium_budget_minutes,args.compute_minutes)
    for window_hours,budget in zip(args.search_windows,budgets):
        subset=select_recent_frames(frames,window_hours)
        info={"window_hours":window_hours,"budget_minutes":budget,
              "input_frames":len(subset),"parsed_tracks":0,
              "stationary_filter_enabled":stationary_filter,
              "enhanced_filter_enabled":enhanced_filter,
              "cuda_candidate_limit":effective_max_candidates}
        if len(subset)<args.min_frames:
            info["status"]="INSUFFICIENT_FRAMES"
            runs.append(info)
            log(f"Skipping {window_hours}h window: only {len(subset)} frames.")
            continue
        # Hardlinks avoid copying thousands of JPEGs; files remain locally
        # accessible as regular images for the existing CUDA binary.
        with tempfile.TemporaryDirectory(prefix="soho_window_",dir=str(cache_dir.parent)) as staged:
            stage=Path(staged)
            for original in subset:
                target=stage/original.name
                try:
                    os.link(original,target)
                except OSError:
                    target.symlink_to(original.resolve())
            cmd=[str(project/"comet_hunter_archive"),"--camera","c3",
                 "--resolution",str(getattr(args,"resolution",512)),"--input-dir",str(stage),
                 "--budget-minutes",str(budget),"--min-frames",
                 str(args.min_frames),"--max-candidates",str(effective_max_candidates)]
            if stationary_filter:
                cmd.append("--within-window-stationary-filter")
            if enhanced_filter:
                cmd.extend(["--spatial-balanced-peaks", "--trajectory-integrity-filter",
                            "--trajectory-null-control"])
            log(f"CUDA {window_hours}h pass: {len(subset)} cached frames, {budget}m budget; "
                f"stationary filter={'ON' if stationary_filter else 'OFF'}; "
                f"enhanced spatial/null={'ON' if enhanced_filter else 'OFF'}; "
                f"CUDA candidate cap={effective_max_candidates}.")
            proc=subprocess.run(cmd,cwd=project,text=True,stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        log_path=results_dir/f"detector_{run_id}_{window_hours}h.log"
        log_path.write_text(proc.stdout,encoding="utf-8")
        if proc.returncode!=0:
            raise RuntimeError(f"CUDA {window_hours}h pass failed ({proc.returncode}); "
                               f"see {log_path}. Newest frame not marked complete.")
        parsed=parse_candidates(proc.stdout)
        for c in parsed:
            c["source_window_hours"]=window_hours
            c["source_candidate_id"]=c["cid"]
            # CUDA C01 etc. is scoped to one pass. Keep global IDs numeric
            # so dashboard and alert format continue to work unchanged.
            c["cid"]=f"C{window_hours:03d}{int(c['cid'][1:]):04d}"
        # Independent early assessment, before long CUDA passes.
        # Opt-in marker persists across watchdog recovery. Never block C3.
        if window_hours==args.search_windows[0] and (project/"state"/"realtime_early_review_enabled").is_file():
            try:
                early=process_early_tracks(
                    parsed,frames,results_dir,args,run_id,
                    hunter=__import__(__name__),
                    notify_enabled=True,
                )
                log(f"Early C3 review: {early['strong_pixel_tracks']} supported, "
                    f"{early['early_alert_ready']} fresh candidate(s) eligible for manual review; "
                    f"report check={early['report_check_status']}.")
            except Exception as exc:
                log(f"WARN: early C3 review failed safely: {exc}")
        info["status"]="OK"
        info["parsed_tracks"]=len(parsed)
        info["consistent_tracks"]=sum(not c.get("trajectory_warning") for c in parsed)
        runs.append(info)
        all_candidates.extend(parsed)
    return all_candidates,runs


def run_detector(project,cache_dir,results_dir,args,force=False):
    frames=prune_cache(cache_dir,max(args.window_hours,*args.search_windows),resolution=args.resolution)
    if len(frames)<args.min_frames:
        log(f"Only {len(frames)} recent frame(s); waiting for at least {args.min_frames}."); return
    newest=frames[-1].name; last_run=results_dir/"last_detector_frame.txt"
    if not force and last_run.exists() and last_run.read_text().strip()==newest:
        log("Detector already processed newest frame."); return
    if not wait_for_gpu(project,args.gpu_wait_seconds):
        log("WARN: GPU remained busy; retry next cycle."); return
    binary=project/"comet_hunter_archive"
    run_id=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log(f"Running native {args.resolution}px multiscale CUDA detector on {len(frames)} cached frame(s).")
    raw_candidates,window_runs=run_multiscale_cuda(
        project,frames,results_dir,args,run_id,cache_dir
    )
    if not any(row["status"]=="OK" for row in window_runs):
        log("WARN: not enough frames to run any CUDA window; newest frame not marked complete.")
        return
    latest_frame = results_dir / "latest_frame.jpg"
    try:
        tmp_frame = results_dir / "latest_frame.jpg.part"
        shutil.copy2(frames[-1], tmp_frame)
        tmp_frame.replace(latest_frame)
        log(f"Saved dashboard preview for processed frame: {newest}")
    except Exception as exc:
        log(f"WARN: could not save latest processed frame preview: {exc}")

    for c in raw_candidates:
        c["resolution"]=args.resolution
        audit_track_frames(c, frames)
    rejection_counts={}
    for c in raw_candidates:
        for reason in comet_filter_reasons(c):
            rejection_counts[reason]=rejection_counts.get(reason,0)+1

    candidates=[c for c in raw_candidates if comet_like(c)]
    annotate_motion_families(candidates)
    # Prefer local, coherent short tracks as event representatives. Keep the
    # 14-day candidates separately for verification and diagnostic visibility.
    candidates=sorted(candidates,key=lambda c:(
        bool(c.get("trajectory_warning")),
        c.get("source_window_hours",336),
        -live_candidate_rank(c),
    ))
    event_groups=group_event_hypotheses(candidates)
    event_representatives=[g["representative"] for g in event_groups]

    # Historical-style second-stage verification: inspect the actual pixels
    # along each grouped event trajectory before it reaches review/alerting.
    gray_cache={}
    verified_representatives=[]
    rejected_representatives=[]
    for c in event_representatives:
        try:
            pixel=verify_track_pixels(c,frames,gray_cache=gray_cache)
            review_class,historical_like=classify_for_review(c,pixel)
        except Exception as exc:
            # Discovery must fail open: a verifier problem must never erase a
            # CUDA event or abort the whole realtime pass.
            log(f"WARN: pixel verification failed for {c['cid']}: {exc}; retaining for human review.")
            pixel={
                "status":"UNAVAILABLE",
                "samples":0,
                "hits":0,
                "hit_fraction":0.0,
                "median_snr":0.0,
                "peak_snr":0.0,
                "median_contrast":0.0,
                "median_compactness":0.0,
                "longest_hit_run":0,
                "evidence_score":0.0,
                "error":str(exc),
            }
            review_class="SECONDARY"
            historical_like=False

        # Do not award strong/secondary pixel support to incoherent endpoints.
        # Still retain the candidate for explicit human review.
        if c.get("trajectory_warning"):
            pixel=dict(pixel)
            pixel["status"]="UNAVAILABLE"
            pixel["reason"]=c["trajectory_warning"]
            review_class="VISUAL_REJECT"
            historical_like=False
        # Preserve endpoint-interpolation evidence separately from velocity.
        # A velocity mismatch does not by itself prove that no object exists.
        if c.get("trajectory_warning")=="ENDPOINT_VELOCITY_MISMATCH" and c.get("available_source_frames",0)>=5:
            try:
                alternative=verify_track_pixels(c,frames,gray_cache=gray_cache,model="endpoints")
                pixel["endpoint_model"]=alternative
            except Exception as exc:
                pixel["endpoint_model_error"]=str(exc)
        # Independent negative control: does a compact source move through
        # successive image positions, or persist at fixed pixels?
        # This is diagnostic until benchmarked against known SOHO comets.
        try:
            control_model="endpoints" if c.get("trajectory_warning") else "velocity"
            pixel["temporal_motion"]=verify_temporal_motion(
                c,frames,gray_cache=gray_cache,model=control_model)
        except Exception as exc:
            pixel["temporal_motion"]={"status":"UNAVAILABLE","error":str(exc)}
        c["pixel_verification"]=pixel
        c["review_class"]=review_class
        c["historical_recovery_profile"]=historical_like
        if review_class=="VISUAL_REJECT":
            rejected_representatives.append(c)
        else:
            verified_representatives.append(c)

    # 1024 CUDA still uses a 24-pixel edge mask from the 512 profile.
    # Border-confined tracks are retained for human review but must not
    # issue automatic high-priority comet alerts without independent evidence.
    edge_downgraded=0
    for c in event_representatives:
        c["edge_review"]=edge_review_evidence(c)
        if (c.get("review_class")=="STRONG_REVIEW"
                and c["edge_review"]["status"]=="BORDER_CONFINED"
                and args.resolution==1024):
            c["review_class"]="SECONDARY"
            c["edge_review_downgraded"]=True
            edge_downgraded+=1
    log(f"1024 edge-parity triage: downgraded {edge_downgraded} border-confined "
        "strong review events; all detections preserved.")

    # Compare independent pixel-supported tracks to find shared star motion.
    # Keep raw hypotheses and videos; only star-like strong alerts are triaged.
    annotate_star_field(event_representatives)
    # Compare compact sources in cached SOHO images without suppressing
    # candidates until this approach is validated on known comets.
    try:
        image_reference=build_reference(
            frames,gray_cache,pixel_verify._decode_gray,args.resolution,image_time)
    except Exception as exc:
        image_reference={"status":"UNAVAILABLE","reason":str(exc)}
    log(f"Cached-image star-motion reference: {image_reference.get('status')} "
        f"first={image_reference.get('first_sources',0)} "
        f"last={image_reference.get('last_sources',0)}.")
    for c in event_representatives:
        try:
            c["image_star_motion"]=evidence_for_velocity(c,image_reference)
        except Exception as exc:
            c["image_star_motion"]={"status":"UNAVAILABLE","reason":str(exc)}
    star_like_count=0
    for c in event_representatives:
        if (c.get("star_field",{}).get("status")=="STAR_LIKE"
                and c.get("review_class")=="STRONG_REVIEW"):
            c["review_class"]="SECONDARY"
            c["star_field_review_downgraded"]=True
            star_like_count+=1
    if star_like_count:
        log(f"Star-field consensus: downgraded {star_like_count} strong events "
            "to SECONDARY; retained every original track and video.")

    # Discovery-first safety net: the pixel verifier has not yet been
    # benchmarked against all historical known-comet recoveries. Retain the
    # highest-ranked visually weak event hypotheses for human review instead
    # of allowing an uncalibrated verifier to hide every marginal candidate.
    low_support_review_limit=16
    # Long-window artifact candidates must not consume all available review
    # slots ahead of short, recent tracks.
    recent_cutoff=image_time(newest)-dt.timedelta(hours=36)
    rejected_representatives.sort(key=lambda c:(
        bool(c.get("trajectory_warning")),
        c["last"]["time"]<recent_cutoff,
        c.get("source_window_hours",336),
        -(c.get("pixel_verification",{}).get("temporal_motion",{}).get("support_fraction") or 0),
        -live_candidate_rank(c),
    ))
    low_support_review=rejected_representatives[:low_support_review_limit]
    review_representatives=verified_representatives+low_support_review
    for c in low_support_review:
        c["review_class"]="LOW_SUPPORT"

    stats={
        "run_id":run_id,
        "newest_frame":newest,
        "frames_scanned":len(frames),
        "window_runs":window_runs,
        "multiscale_window_hours":list(args.search_windows),
        "source_resolution":args.resolution,
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
        "quadrants":{
            "raw_tracks":quadrant_counts(raw_candidates),
            "comet_like_tracks":quadrant_counts(candidates),
            "event_representatives":quadrant_counts(event_representatives),
            "pixel_supported":quadrant_counts(verified_representatives),
            "low_support":quadrant_counts(rejected_representatives),
        },
        "velocity_signs":{
            "raw_tracks":velocity_sign_counts(raw_candidates),
            "comet_like_tracks":velocity_sign_counts(candidates),
            "event_representatives":velocity_sign_counts(event_representatives),
            "pixel_supported":velocity_sign_counts(verified_representatives),
            "low_support":velocity_sign_counts(rejected_representatives),
        },
        "pixel_verified_events":len(verified_representatives),
        "star_field_consensus_events":sum(c.get("star_field",{}).get("status")=="STAR_LIKE" for c in event_representatives),
        "image_star_reference":{k:v for k,v in image_reference.items() if k not in ("sources","index")},
        "image_star_motion_events":sum(c.get("image_star_motion",{}).get("status")=="STAR_LIKE" for c in event_representatives),
        "image_star_motion_downgraded":0,
        "star_field_review_downgraded":star_like_count,
        "edge_review_downgraded":edge_downgraded,
        "temporal_motion_measured":sum(c.get("pixel_verification",{}).get("temporal_motion",{}).get("status")=="MEASURED" for c in event_representatives),
        "temporal_motion_supported":sum(c.get("pixel_verification",{}).get("temporal_motion",{}).get("supported",0) >= 3 for c in event_representatives),
        "pixel_rejected_events":len(rejected_representatives),
        "pixel_strong_events":sum(1 for c in verified_representatives if c.get("review_class")=="STRONG_REVIEW"),
        "pixel_secondary_events":sum(1 for c in verified_representatives if c.get("review_class")=="SECONDARY"),
        "low_support_review_events":len(low_support_review),
        "consistent_raw_tracks":sum(not c.get("trajectory_warning") for c in raw_candidates),
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
            f"Pixel verification: {len(verified_representatives)} supported review event(s), "
            f"{len(rejected_representatives)} low-support event(s); "
            f"retaining top {len(low_support_review)} low-support event(s) for human review."
        )
        log(
            "Quadrant diagnostics: "
            f"raw={quadrant_counts(raw_candidates)} "
            f"filtered={quadrant_counts(candidates)} "
            f"events={quadrant_counts(event_representatives)} "
            f"supported={quadrant_counts(verified_representatives)}."
        )
        log(
            "Velocity-sign diagnostics: "
            f"raw={velocity_sign_counts(raw_candidates)} "
            f"filtered={velocity_sign_counts(candidates)} "
            f"events={velocity_sign_counts(event_representatives)}."
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
            "resolution":args.resolution,
            "source_window_hours":c.get("source_window_hours"),
            "source_candidate_id":c.get("source_candidate_id"),
            "trajectory_warning":c.get("trajectory_warning",""),
            "quality_issues":c.get("quality_issues",[]),
            "available_source_frames":c.get("available_source_frames",0),
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
            "resolution":args.resolution,
            "source_window_hours":c.get("source_window_hours"),
            "priority":c["priority"],
            "review_class":c.get("review_class"),
            "historical_recovery_profile":c.get("historical_recovery_profile",False),
            "pixel_verification":c.get("pixel_verification",{}),
            "star_field":c.get("star_field",{}),
            "image_star_motion":c.get("image_star_motion",{}),
            "edge_review":c.get("edge_review",{}),
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
    seen_file=results_dir/("seen_tracks_1024.json" if args.resolution==1024 else "seen_tracks.json"); seen=load_json(seen_file,[])
    if not isinstance(seen,list):seen=[]
    rows=[]
    newest_time=image_time(newest)
    for c in review_representatives:
        # A 14-day detection window is for science quality, not a live alert
        # about an object that was last observed many days ago.
        edge_age_hours=(newest_time-c["last"]["time"]).total_seconds()/3600.0
        edge_relevant=(-1.0<=edge_age_hours<=36.0)
        sig=candidate_signature(c); dup=any(same_track(old,sig) for old in seen[-500:]); match=match_recent_report(c,reports) if reports and not c.get("trajectory_warning") else None
        status="KNOWN_REPORT" if match else ("SEEN_ALREADY" if dup else "UNMATCHED")
        rid=match["report_id"] if match else ""; err=f"{match['median_error']:.1f}" if match else ""
        review_id=""
        review_error=""
        # Even previously-seen events need a visible review clip. The review
        # generator reuses an existing matching package instead of reencoding.
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
            review_error=str(exc)
            log(f"WARN: live review media failed for {c['cid']}: {exc}")
        rows.append({
            "candidate":c["cid"],
            "resolution":args.resolution,
            "source_window_hours":c.get("source_window_hours"),
            "status":status,
            "report_id":rid,
            "match_error_1024":err,
            "review_class":c.get("review_class"),
            "historical_recovery_profile":c.get("historical_recovery_profile",False),
            "pixel_verification":c.get("pixel_verification",{}),
            "star_field":c.get("star_field",{}),
            "image_star_motion":c.get("image_star_motion",{}),
            "edge_review":c.get("edge_review",{}),
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
            "review_error":review_error,
            "trajectory_warning":c.get("trajectory_warning",""),
            "quality_issues":c.get("quality_issues",[]),
            "available_source_frames":c.get("available_source_frames",0),
            "endpoint_order_corrected":c.get("endpoint_order_corrected",False),
            "edge_relevant":edge_relevant,
            "hours_behind_latest":round(edge_age_hours,2),
        })
        if match:
            append_tsv(results_dir/"known_matches.tsv",["utc","candidate","first","last","report_id","median_error_1024"],[run_id,c["cid"],c["first"]["file"],c["last"]["file"],rid,err])
            log(f"Known-report match: {c['cid']} -> {rid} ({err}px @1024, {match.get('path_model','?')}, {match.get('report_size','?')}, {match.get('report_origin','?')})."); continue
        if dup: continue
        seen.append(sig)
        if not edge_relevant:
            log(f"Historical-window review only: {c['cid']} is {edge_age_hours:.1f}h behind newest C3 frame; no realtime alert.")
            continue
        if c.get("review_class")!="STRONG_REVIEW" or str(c.get("priority","")).upper()!="HIGH":
            pixel=c.get("pixel_verification",{})
            append_tsv(
                results_dir/"review_events.tsv",
                ["utc","candidate","raw_priority","review_class","frames","rms","speed","sunward","pixel_hits","pixel_samples","pixel_peak_snr","event_group","first","last"],
                [run_id,c["cid"],c["priority"],c.get("review_class"),c["frames"],c["rms"],c["speed"],c["sunward"],pixel.get("hits",0),pixel.get("samples",0),pixel.get("peak_snr",0),c.get("event_group"),c["first"]["file"],c["last"]["file"]]
            )
            log(
                f"Realtime review event: {c.get('event_group')} rep={c['cid']} "
                f"members={c.get('event_members',1)} raw_class={c['priority']} "
                f"review_class={c.get('review_class')} frames={c['frames']} "
                f"RMS={c['rms']:.2f} pixel_hits={pixel.get('hits',0)}/{pixel.get('samples',0)} "
                f"pixel_peak_snr={pixel.get('peak_snr',0)}. "
                "Retained for review; not escalated to comet alert."
            )
            continue
        append_tsv(
            results_dir/"alerts.tsv",
            ["utc","candidate","priority","frames","speed","vx","vy","rms","sunward","score","first","first_x","first_y","last","last_x","last_y","duplicate_check"],
            [run_id,c["cid"],c["priority"],c["frames"],c["speed"],c["vx"],c["vy"],c["rms"],c["sunward"],c["score"],c["first"]["file"],c["first"]["x"],c["first"]["y"],c["last"]["file"],c["last"]["x"],c["last"]["y"],"no_recent_match" if reports else "check_unavailable"]
        )
        pixel=c.get("pixel_verification",{})
        alert="\n"+"!"*72+"\nREALTIME COMET ALERT - PIXEL-VERIFIED HIGH-PRIORITY EVENT\n"+f"Candidate: {c['cid']} raw_class={c['priority']} review_class={c.get('review_class')} frames={c['frames']} RMS={c['rms']:.2f}\n"+f"Motion: speed={c['speed']:.2f} px/h vx={c['vx']:.2f} vy={c['vy']:.2f} sunward={c['sunward']:.2f}\n"+f"Pixel evidence: hits={pixel.get('hits',0)}/{pixel.get('samples',0)} hit_fraction={pixel.get('hit_fraction',0):.2f} peak_snr={pixel.get('peak_snr',0):.2f} longest_run={pixel.get('longest_hit_run',0)}\n"+f"Event group: {c.get('event_group')} members={c.get('event_members',1)}\n"+f"Motion family: {c.get('motion_family_size',1)} track(s) within 0.35 px/h\n"+f"First: {c['first']['file']} ({c['first']['x']:.1f},{c['first']['y']:.1f}) [{args.resolution}]\n"+f"Last : {c['last']['file']} ({c['last']['x']:.1f},{c['last']['y']:.1f}) [{args.resolution}]\nACTION: visually inspect and verify independently before reporting.\n"+"!"*72
        log(alert); (results_dir/"latest_alert.txt").write_text(alert+"\n",encoding="utf-8")
    seen_file.write_text(json.dumps(seen[-1000:],indent=2),encoding="utf-8")
    (results_dir/"latest_candidates.json").write_text(json.dumps(rows,indent=2),encoding="utf-8")
    # Mark the newest frame complete only after the full detector, verifier,
    # duplicate check, review generation and result writes have succeeded.
    last_run.write_text(newest)

def main():
    p=argparse.ArgumentParser(description="Realtime SOHO C3 CUDA comet hunter")
    p.add_argument("--project",required=True); p.add_argument("--poll-seconds",type=int,default=905); p.add_argument("--window-hours",type=int,default=336); p.add_argument("--bootstrap-hours",type=int,default=336); p.add_argument("--compute-minutes",type=int,default=8); p.add_argument("--min-frames",type=int,default=5); p.add_argument("--max-candidates",type=int,default=100); p.add_argument("--gpu-wait-seconds",type=int,default=720); p.add_argument("--once",action="store_true")
    p.add_argument("--reprocess-cache-once",action="store_true",help="Rerun cached CUDA frames without any SOHO requests; then exit.")
    p.add_argument("--search-windows",default="12,48,336",
                   help="Comma-separated independent CUDA search windows in hours.")
    p.add_argument("--resolution",type=int,choices=(512,1024),default=512)
    p.add_argument("--short-budget-minutes",type=int,default=2)
    p.add_argument("--medium-budget-minutes",type=int,default=3)
    args=p.parse_args()
    if args.poll_seconds<900:p.error("--poll-seconds must be at least 900")
    try:
        args.search_windows=tuple(int(v) for v in args.search_windows.split(","))
    except ValueError:
        p.error("--search-windows must contain three integer hours, e.g. 12,48,336")
    if len(args.search_windows)!=3 or sorted(set(args.search_windows))!=list(args.search_windows):
        p.error("--search-windows must be three strictly increasing hour values")
    if min(args.search_windows)<1 or max(args.search_windows)>args.window_hours:
        p.error("Search windows must be within --window-hours")
    if min(args.short_budget_minutes,args.medium_budget_minutes,args.compute_minutes)<1:
        p.error("All CUDA budgets must be positive")
    project=Path(args.project).expanduser().resolve(); state_dir=project/"state"; results_dir=project/"results"/"realtime"; cache_dir=Path("/dev/shm")/f"comet_realtime_{os.environ.get('USER','user')}_c3_{args.resolution}"
    pixel_verify.configure_resolution(args.resolution)
    for d in (state_dir,results_dir,cache_dir):d.mkdir(parents=True,exist_ok=True)
    signal.signal(signal.SIGINT,on_signal); signal.signal(signal.SIGTERM,on_signal)
    heartbeat_stop=threading.Event()
    heartbeat_thread=threading.Thread(
        target=heartbeat_keepalive,args=(results_dir,heartbeat_stop),
        name="soho-heartbeat",daemon=True
    )
    heartbeat_thread.start()
    log("Realtime hunter starting."); log(f"Project: {project}"); log(f"Native C3 resolution: {args.resolution}")
    log("Experimental CUDA stationary filter: "
        + ("ENABLED" if (state_dir/"realtime_cuda_stationary_enabled").is_file() else "disabled")); log(f"Rolling cache: {cache_dir}"); log(f"Multiscale CUDA windows={args.search_windows}h budgets={args.short_budget_minutes},{args.medium_budget_minutes},{args.compute_minutes}m max_candidates_per_pass={args.max_candidates}."); log("Do not run an independent SOHO archive/verifier fetcher in parallel.")
    cleanup=prune_realtime_reviews(results_dir)
    reclaimed=cleanup["removed_bytes"]+cleanup["removed_redundant_bytes"]
    if cleanup["removed_dirs"] or reclaimed:
        log(f"Realtime review retention cleanup: removed {cleanup['removed_dirs']} stale review package(s), reclaimed {reclaimed/(1024*1024):.1f} MiB.")
    if args.reprocess_cache_once:
        write_heartbeat(results_dir,status="running",phase="Rechecking cached SOHO frames")
        run_detector(project,cache_dir,results_dir,args,force=True)
        write_heartbeat(results_dir,status="ok",new_frames=0)
        log("Cached-frame reprocessing complete.")
        heartbeat_stop.set()
        return 0
    while not STOP:
        start=time.time()
        try:
            write_heartbeat(results_dir,status="checking",phase="Checking official SOHO C3 sources")
            new=download_live_frames(cache_dir,state_dir,args.poll_seconds,args.bootstrap_hours,results_dir=results_dir,resolution=args.resolution)
            prune_cache(cache_dir,max(args.window_hours,args.bootstrap_hours),resolution=args.resolution)
            if new:
                write_heartbeat(
                    results_dir,
                    status="running",
                    new_frames=len(new),
                    phase="Running multiscale CUDA detector",
                )
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
    heartbeat_stop.set()
    log("Realtime hunter stopped."); return 0
if __name__=="__main__": raise SystemExit(main())
