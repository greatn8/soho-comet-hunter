#!/usr/bin/env python3
"""Publish retained realtime archive clips on a REPLACED, separate Git branch.

The dashboard's main branch keeps HTML, code and JSON, never MP4/JPG realtime
archive media. On each publisher pass, prepare a parentless commit containing
only media referenced by current 48-hour archive records (including permanently
protected known/comet/locked rows). Replace the separate branch only when its
tree changes, then convert published URLs and untrack old main-branch media.

FAIL CLOSED: do not remove main-branch media unless the rolling branch push
succeeds. Historical Git blobs on main are NOT rewritten or silently deleted.
The caller must already hold .dashboard_publish.lock. Runs no hunter and does
not alter science or CUDA data.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

BRANCH="realtime-archive-media"
RAW_BASE=("https://raw.githubusercontent.com/greatn8/"
          "soho-comet-hunter/"+BRANCH+"/docs/media/realtime_archive")
SAFE_ID=re.compile(r"^RT[A-Z0-9]+$")
ALLOWED_FILES=("zoom_annotated.mp4","zoom_contact_sheet.jpg")


def git(root,*args,env=None,allow_failure=False):
    result=subprocess.run(["git",*args],cwd=root,env=env,capture_output=True,text=True)
    if result.returncode and not allow_failure:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()[:750]}")
    return result


def read_cards(path):
    data=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data,dict) or not isinstance(data.get("candidates"),list):
        raise ValueError(f"Missing candidate records: {path}")
    return data


def media_identifier(row):
    if not isinstance(row,dict) or row.get("archived_from_realtime") is not True:
        return None
    value=re.sub(r"[^A-Z0-9]+","",str(row.get("id") or "").upper())
    return value if SAFE_ID.fullmatch(value) else None


def inventory(root):
    root=Path(root).resolve()
    archive=read_cards(root/"docs/data/realtime_archive.json")
    candidates=read_cards(root/"docs/data/candidates.json")
    rows=[r for r in archive["candidates"]+candidates["candidates"]
          if isinstance(r,dict) and media_identifier(r)]
    allowed={media_identifier(r) for r in rows}
    folder=root/"docs/media/realtime_archive"
    if folder.is_symlink():
        raise ValueError("Archive media directory must not be a symlink")
    paths=[]
    by_id={}
    for ident in sorted(allowed):
        directory=folder/ident
        if directory.is_symlink():
            raise ValueError(f"Unexpected symlink: {directory}")
        if not directory.is_dir():
            continue
        for name in ALLOWED_FILES:
            file=directory/name
            if file.is_symlink():
                raise ValueError(f"Unexpected symlink: {file}")
            if file.is_file():
                paths.append(file.relative_to(root).as_posix())
                by_id.setdefault(ident,set()).add(name)
    # Fail closed if any EXISTING archived link would become invalid after
    # untracking old main assets. External, independent URLs are untouched.
    for r in rows:
        ident=media_identifier(r)
        for field,name in (("video","zoom_annotated.mp4"),
                           ("thumbnail","zoom_contact_sheet.jpg")):
            url=str(r.get(field) or "")
            if (url.startswith("./media/realtime_archive/")
                    and name not in by_id.get(ident,set())):
                raise ValueError(
                    f"Protected existing archive link has no staged file: {ident} {field}")
    return archive,candidates,paths,by_id


def update_urls(payload,available):
    changed=0
    for row in payload.get("candidates",[]):
        ident=media_identifier(row)
        if ident not in available:
            continue
        for field,name in (("video","zoom_annotated.mp4"),
                           ("thumbnail","zoom_contact_sheet.jpg")):
            if name not in available[ident]:
                continue
            old=str(row.get(field) or "")
            # Do not invent a link when no media reference existed; use the
            # candidate's actual staged media as the source of truth.
            if not old and field=="thumbnail":
                continue
            new=f"{RAW_BASE}/{ident}/{name}"
            if old!=new:
                row[field]=new
                changed+=1
    return changed


def atomic_json(path,data):
    tmp=path.with_suffix(path.suffix+".rolling_tmp")
    try:
        tmp.write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def remote_tree(root):
    remote=git(root,"ls-remote","--heads","origin",f"refs/heads/{BRANCH}")
    if not remote.stdout.strip():
        return None
    sha=remote.stdout.strip().split()[0]
    if git(root,"cat-file","-e",sha,allow_failure=True).returncode:
        # Fetch the branch metadata when this checkout lacks it. Local blobs
        # already tracked on main are reused via the shared object store.
        git(root,"fetch","-q","origin",f"refs/heads/{BRANCH}")
    return git(root,"rev-parse",f"{sha}^{{tree}}").stdout.strip()


def publish(root,apply=False):
    root=Path(root).resolve()
    archive,candidates,paths,available=inventory(root)
    print(f"Archive media: {len(available)} referenced candidate folders, "
          f"{len(paths)} approved files",flush=True)
    tracked=git(root,"ls-files","--","docs/media/realtime_archive").stdout.splitlines()
    print(f"Main-tracked archive media paths: {len(tracked)}",flush=True)
    if not apply:
        print("DRY RUN: no push, no Git-index changes, no URL rewrites.")
        return {"media_files":len(paths),"main_tracked":len(tracked),
                "published":False}
    if not paths:
        raise RuntimeError("Refusing empty media branch replacement; preserve existing assets")
    index_home=Path("/dev/shm") if Path("/dev/shm").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="ccor-rolling-media-",dir=index_home) as tmp:
        idx=Path(tmp)/"index"
        env=dict(os.environ,GIT_INDEX_FILE=str(idx))
        git(root,"read-tree","--empty",env=env)
        # Stage only the metadata-approved media files in an ALTERNATE index.
        # The main checkout's Git index is not modified by this operation.
        for i in range(0,len(paths),80):
            git(root,"add","-f","--",*paths[i:i+80],env=env)
        tree=git(root,"write-tree",env=env).stdout.strip()
    before=remote_tree(root)
    changed=before!=tree
    if changed:
        label=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        commit=git(root,"-c","user.name=Comet Archive Publisher",
                   "-c","user.email=comet-dashboard@local",
                   "commit-tree",tree,"-m",f"Replace realtime review media {label}").stdout.strip()
        # Verify this command succeeds BEFORE main media URLs or index change.
        git(root,"push","--force","origin",
            f"{commit}:refs/heads/{BRANCH}")
        print(f"Replaced {BRANCH} branch with {len(paths)} retained files.")
    else:
        print(f"{BRANCH} contents unchanged; no new push needed.")
    url_changes=update_urls(archive,available)+update_urls(candidates,available)
    if url_changes:
        atomic_json(root/"docs/data/realtime_archive.json",archive)
        atomic_json(root/"docs/data/candidates.json",candidates)
    if tracked:
        git(root,"rm","-r","-q","--cached","--","docs/media/realtime_archive")
        print(f"Untracked {len(tracked)} realtime media files from main index "
              "(working files retained; original Git history unchanged).")
    print(f"Repointed {url_changes} archive URLs to {BRANCH}.")
    return {"media_files":len(paths),"main_tracked":len(tracked),
            "published":True,"url_changes":url_changes,
            "tree_changed":changed}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",type=Path,required=True)
    p.add_argument("--apply",action="store_true")
    p.add_argument("--publisher-lock-held",action="store_true")
    a=p.parse_args()
    if a.apply and not a.publisher_lock_held:
        p.error("The publisher must hold .dashboard_publish.lock before applying.")
    root=a.project.expanduser().resolve()
    if not (root/"publish_dashboard.sh").exists():
        p.error("Expected comet dashboard publisher repository.")
    publish(root,apply=a.apply)


if __name__=="__main__":
    main()
