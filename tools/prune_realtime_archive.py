#!/usr/bin/env python3
"""Bounded 48-hour retention for unconfirmed realtime dashboard review media.

Default DRY RUN; --apply deletes only expired *realtime-derived* dashboard
records and their specifically owned docs/media/realtime_archive/RT* assets.
Known report matches, manually confirmed comets and user-starred/locked records
are protected regardless of age. Other historical verified comet archives,
science FITS, source footage, GPU results and running hunters are untouched.

Publisher calls this AFTER generating dashboard JSON and BEFORE git add -A docs,
with --publisher-lock-held. Standalone invocation acquires the existing
publisher flock to avoid racing with active publication.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
from pathlib import Path

UTC=dt.timezone.utc
ARCHIVE_NAME=re.compile(r"^RT(\d{8}T\d{4})[A-Z0-9]+$")
SAFE_ID=re.compile(r"^RT[A-Z0-9]+$")
ALLOWED_FILES={"zoom_annotated.mp4","zoom_contact_sheet.jpg"}
# Deliberately do not infer comet status from generic words "SOHO" or "comet"
# in source descriptions or titles.
PROTECTED_LABELS={
    "KNOWN_REPORT","CONFIRMED_COMET","KNOWN_COMET","CONFIRMED",
    "VERIFIED_COMET","KNOWN_OBJECT","COMET_CONFIRMED",
}
DEFAULT_HOURS=48


def valid_label(value):
    return str(value or "").strip().upper().replace("-","_").replace(" ","_")


def valued(value):
    return bool(value is not None and str(value).strip()
                and str(value).strip().lower() not in {"null","none","nan","false","0","-"})


def is_protected(row,overrides=None):
    """Preserve independently matched or manually protected records."""
    if not isinstance(row,dict):
        return True
    if row.get("archived_from_realtime") is not True:
        return True
    labels=(row.get("review_class"),row.get("realtime_status"),
            row.get("review_state"),row.get("classification"),
            row.get("status"))
    if any(valid_label(v) in PROTECTED_LABELS for v in labels):
        return True
    if any(valued(row.get(k)) for k in
           ("known_object","report_id","matched_report","comet_designation")):
        return True
    if row.get("starred") is True or row.get("review_locked") is True or row.get("star_locked") is True:
        return True
    rid=str(row.get("id") or "").upper()
    event=str(row.get("event_id") or "").upper()
    override=(overrides or {}).get(rid) or (overrides or {}).get(event) or {}
    if isinstance(override,dict):
        if any(valued(override.get(k)) for k in
               ("known_object","report_id","comet_designation")):
            return True
        if any(override.get(k) is True for k in
               ("starred","star_locked","review_locked")):
            return True
        if any(valid_label(override.get(k)) in PROTECTED_LABELS for k in
               ("review_state","review_class","classification")):
            return True
    return False


def as_utc(value):
    if not value:
        return None
    try:
        parsed=dt.datetime.fromisoformat(str(value).strip().replace("Z","+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)
    except (ValueError,TypeError,OverflowError):
        return None


def expired_unconfirmed(row,cutoff,overrides=None):
    if not isinstance(row,dict) or row.get("archived_from_realtime") is not True:
        return False
    if is_protected(row,overrides):
        return False
    date=as_utc(row.get("last_seen"))
    # Fail closed: do not expire records without a reliable UTC last timestamp.
    return date is not None and date<=cutoff


def safe_media_id(candidate_id):
    ident=re.sub(r"[^A-Z0-9]+","",str(candidate_id or "").upper())
    return ident if SAFE_ID.fullmatch(ident) else None


def media_timestamp(folder):
    m=ARCHIVE_NAME.fullmatch(folder)
    if not m:
        return None
    return dt.datetime.strptime(m.group(1),"%Y%m%dT%H%M").replace(tzinfo=UTC)


def read_json(path):
    if not path.exists():
        raise FileNotFoundError(f"Required archive metadata absent: {path}")
    data=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data,dict):
        raise ValueError(f"Invalid JSON root: {path}")
    return data


def write_atomic(path,data):
    temp=path.with_suffix(path.suffix+".retention_tmp")
    try:
        temp.write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def tracked_expired_media(docs,expired_ids,protected_ids,cutoff):
    """Find index-only archive files omitted by sparse checkout.

    Only rows explicitly selected as expired in the archive metadata qualify.
    This avoids deleting the known-comet videos still hosted from main.
    """
    root=docs.parent
    result=subprocess.run(
        ["git","-C",str(root),"ls-files","-z","--",
         "docs/media/realtime_archive/"],
        capture_output=True,check=False,
    )
    if result.returncode:
        if b"not a git repository" in result.stderr:
            return []
        raise RuntimeError("Cannot inspect tracked realtime archive media: "
                           +result.stderr.decode("utf-8","replace"))
    matches=[]
    for raw in result.stdout.split(bytes([0])):
        if not raw:
            continue
        name=raw.decode("utf-8","surrogateescape")
        path=Path(name)
        parts=path.parts
        if (len(parts)!=5 or parts[:3] !=
                ("docs","media","realtime_archive") or
                parts[4] not in ALLOWED_FILES):
            continue
        ident=parts[3]
        stamp=media_timestamp(ident)
        if (stamp is not None and stamp<=cutoff
                and ident in expired_ids
                and ident not in protected_ids):
            matches.append(name)
    return matches


def plan(docs,now,age_hours=DEFAULT_HOURS):
    docs=Path(docs).resolve()
    data=docs/"data"
    archive_path=data/"realtime_archive.json"
    candidates_path=data/"candidates.json"
    overrides_path=data/"review_overrides.json"
    archive=read_json(archive_path)
    entries=archive.get("candidates")
    if not isinstance(entries,list):
        raise ValueError("realtime_archive.json missing candidates list")
    candidates=read_json(candidates_path)
    cards=candidates.get("candidates")
    if not isinstance(cards,list):
        raise ValueError("candidates.json missing candidates list")
    overrides={}
    if overrides_path.exists():
        overrides=json.loads(overrides_path.read_text(encoding="utf-8"))
        if not isinstance(overrides,dict):
            raise ValueError("Invalid review overrides JSON")
        overrides={str(k).upper():v for k,v in overrides.items()}
    cutoff=now-dt.timedelta(hours=age_hours)
    expired=lambda row:expired_unconfirmed(row,cutoff,overrides)
    drop_archive=[r for r in entries if expired(r)]
    drop_cards=[r for r in cards if expired(r)]
    archive_keep=[r for r in entries if not expired(r)]
    cards_keep=[r for r in cards if not expired(r)]
    # Include any retained archive-derived card as a protection if its media
    # folder is still in use. Never erase a shared or manually protected clip.
    protected_ids={safe_media_id(r.get("id")) for r in archive_keep+cards_keep
                   if isinstance(r,dict)}
    expired_ids={safe_media_id(r.get("id")) for r in drop_archive+drop_cards
                 if isinstance(r,dict)}
    root=docs/"media"/"realtime_archive"
    delete=[]
    retained_orphan_count=0
    if root.exists():
        if root.is_symlink():
            raise ValueError("Refusing symlinked realtime archive root")
        for folder in root.iterdir():
            if folder.is_symlink() or not folder.is_dir():
                continue
            parsed=media_timestamp(folder.name)
            if (parsed is None or parsed>cutoff or
                    folder.name in protected_ids or folder.name not in expired_ids):
                continue
            # Fail closed: never remove unlinked folders, which could have
            # been protected by metadata unavailable in this snapshot.
            for f in folder.iterdir():
                if f.is_symlink() or not f.is_file() or f.name not in ALLOWED_FILES:
                    retained_orphan_count+=1
                    continue
                delete.append(f)
    index_paths=tracked_expired_media(docs,expired_ids,protected_ids,cutoff)
    return {
        "docs":docs,"archive":archive,"candidates":candidates,
        "git_index_removals":index_paths,
        "archive_path":archive_path,"candidates_path":candidates_path,
        "archive_keep":archive_keep,"cards_keep":cards_keep,
        "archive_deleted":drop_archive,"cards_deleted":drop_cards,
        "assets_to_delete":delete,
        "asset_bytes":sum(f.stat().st_size for f in delete),
        "cutoff":cutoff,"retained_unrecognized_assets":retained_orphan_count,
        "protected_rows":sum(is_protected(r,overrides) for r in entries
                             if isinstance(r,dict) and r.get("archived_from_realtime")),
    }


def execute(docs,*,apply=False,now=None,age_hours=DEFAULT_HOURS):
    now=now or dt.datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not 24<=age_hours<=720:
        raise ValueError("Retention window outside allowed 24..720 hours")
    p=plan(docs,now,age_hours)
    print(f"Realtime dashboard retention: {age_hours}h, cutoff {p['cutoff'].isoformat()}")
    print(f"Expired unconfirmed archive records: {len(p['archive_deleted'])}")
    print(f"Expired unconfirmed historical cards: {len(p['cards_deleted'])}")
    print(f"Protected archive records: {p['protected_rows']}")
    print(f"Old archive media files: {len(p['assets_to_delete'])}")
    print(f"Tracked Git media paths to remove: {len(p['git_index_removals'])}")
    print(f"Approximate reclaim from working files: {p['asset_bytes']/1024**2:.1f} MiB")
    if p["retained_unrecognized_assets"]:
        print(f"Preserved unrecognized/special files: {p['retained_unrecognized_assets']}")
    if not apply:
        print("DRY RUN: no files or records changed. Supply --apply to enact.")
        return p
    # Delete media FIRST to make space for atomic JSON writes on tight quota.
    for f in p["assets_to_delete"]:
        # A repeated safety test at deletion time; no path traversal/symlink.
        if f.is_symlink() or f.name not in ALLOWED_FILES:
            continue
        if f.parent.parent.resolve()!=p["docs"]/"media"/"realtime_archive":
            raise ValueError("Refusing media outside scoped archive directory")
        f.unlink(missing_ok=True)
        try:
            f.parent.rmdir()
        except OSError:
            pass
    # Sparse clones do not have media files in their working directory.
    # Stage removals explicitly in the index so GitHub actually loses the
    # expired files from the current tree. Never rewrite history.
    if p["git_index_removals"]:
        subprocess.run(
            ["git","-C",str(p["docs"].parent),"rm","--cached","--sparse",
             "-f","--",*p["git_index_removals"]],
            check=True,stdout=subprocess.DEVNULL,
        )
    if p["archive_deleted"]:
        payload=dict(p["archive"])
        payload["candidates"]=p["archive_keep"]
        payload["retention_hours"]=age_hours
        write_atomic(p["archive_path"],payload)
    if p["cards_deleted"]:
        payload=dict(p["candidates"])
        payload["candidates"]=p["cards_keep"]
        write_atomic(p["candidates_path"],payload)
    print("APPLIED: retained known/confirmed/manual-protected and all non-realtime archive data.")
    return p


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--docs-dir",type=Path,default=Path("docs"))
    p.add_argument("--hours",type=int,default=DEFAULT_HOURS)
    p.add_argument("--apply",action="store_true",
                   help="Delete expired realtime dashboard material")
    p.add_argument("--publisher-lock-held",action="store_true",
                   help="ONLY for publisher already holding .dashboard_publish.lock")
    a=p.parse_args()
    docs=a.docs_dir.expanduser().resolve()
    if docs.name!="docs" or not (docs/"data").is_dir():
        p.error("Expected project docs/ directory with data/ metadata")
    root=docs.parent
    if a.publisher_lock_held:
        execute(docs,apply=a.apply,age_hours=a.hours)
    else:
        lock=root/".dashboard_publish.lock"
        with lock.open("a+b") as fd:
            if a.apply:
                # Blocking lock, so a running publisher cannot race our edits.
                fcntl.flock(fd.fileno(),fcntl.LOCK_EX)
            execute(docs,apply=a.apply,age_hours=a.hours)


if __name__=="__main__":
    main()
