#!/usr/bin/env python3
"""Download only independently reported CCOR FITS time samples from NOAA.

Read-only, limited scientific reference downloader. Does not access C3 input,
CCOR live caches, dashboard, email configuration or any running process.
NOAA SWPC products have short-lived availability; if files were rotated away
use NCEI archives rather than silently substituting quicklook JPEG images.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from benchmark_ccor_reported_comets import CASES

BASE = "https://services.swpc.noaa.gov/products/"
NAME_RE = re.compile(r"(?P<name>CCOR(?P<camera>[12])_[A-Za-z0-9_-]+_(?P<stamp>20\d{6}T\d{6})_[A-Za-z0-9_-]+\.fits(?:\.gz)?)$",re.I)
MAX_FILE_BYTES = 24_000_000
MAX_INDEX_BYTES = 2_000_000

class Hrefs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=="a":
            self.items.extend(value for key,value in attrs if key.lower()=="href" and value)

def get_small_index(url, fetch=None):
    if fetch:
        return fetch(url)
    request=Request(url,headers={"User-Agent":"CCOR-Independent-Reference-FITS/1.0"})
    with urlopen(request,timeout=35) as response:
        data=response.read(MAX_INDEX_BYTES+1)
    if len(data)>MAX_INDEX_BYTES:
        raise ValueError("Remote index too large")
    return data

def enumerate_index(camera,index_html,base=BASE):
    url=base.rstrip("/")+"/"+camera+"/fits/"
    parser=Hrefs()
    parser.feed(index_html.decode("utf-8","replace") if isinstance(index_html,bytes) else index_html)
    candidates=[]
    for href in parser.items:
        name=href.split("?",1)[0].rsplit("/",1)[-1]
        m=NAME_RE.fullmatch(name)
        if not m or m["camera"]!=camera[-1]:
            continue
        absolute=urljoin(url,href)
        parsed=urlsplit(absolute)
        if parsed.scheme!="https" or parsed.netloc!=urlsplit(url).netloc or parsed.path.rsplit("/",1)[0]!=urlsplit(url).path.rstrip("/"):
            continue
        when=dt.datetime.strptime(m["stamp"],"%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc)
        candidates.append({"name":name,"url":absolute,"time":when})
    return sorted(candidates,key=lambda row:(row["time"],row["name"]))

def needed_times(case,context=1):
    out=set()
    for stamp,_,_ in case["points"]:
        t=dt.datetime.fromisoformat(stamp.replace("Z","+00:00"))
        for minutes in range(-context,context+1):
            out.add(t+dt.timedelta(minutes=minutes*15))
    return sorted(out)

def pick_samples(index,case,context=1,max_delta_seconds=90):
    chosen={}
    missing=[]
    for wanted in needed_times(case,context):
        alternatives=sorted(index,key=lambda row:abs((row["time"]-wanted).total_seconds()))
        if not alternatives or abs((alternatives[0]["time"]-wanted).total_seconds())>max_delta_seconds:
            missing.append(wanted.isoformat().replace("+00:00","Z"))
            continue
        best=alternatives[0]
        chosen[best["name"]]=best
    return sorted(chosen.values(),key=lambda row:row["time"]),missing

def fits_header_signature_ok(first):
    # FITS HDU header starts with an 80-character SIMPLE or XTENSION card.
    # Full checksum/HDU integrity is checked by astropy during audit.
    return first.startswith((b"SIMPLE  =",b"XTENSION="))

def bounded_download(url,destination,max_bytes=MAX_FILE_BYTES):
    tmp=destination.with_suffix(destination.suffix+f".{os.getpid()}.part")
    request=Request(url,headers={"User-Agent":"CCOR-Independent-Reference-FITS/1.0"})
    h=hashlib.sha256()
    size=0
    try:
        with urlopen(request,timeout=65) as response, tmp.open("wb") as fp:
            while True:
                chunk=response.read(1024*1024)
                if not chunk: break
                size+=len(chunk)
                if size>max_bytes:
                    raise ValueError(f"FITS exceeds {max_bytes} byte safety cap")
                h.update(chunk)
                fp.write(chunk)
        first=tmp.open("rb").read(80)
        if not fits_header_signature_ok(first) and not (first[:2]==b"\x1f\x8b" and destination.name.endswith(".gz")):
            raise ValueError("Downloaded file does not begin with FITS header; likely error page")
        if size<2880:
            raise ValueError("Downloaded file too small to contain a FITS image")
        tmp.replace(destination)
        return {"bytes":size,"sha256":h.hexdigest()}
    finally:
        tmp.unlink(missing_ok=True)

def download_cases(cases,root,context=1,max_files=24,fetch_index=None,download=bounded_download):
    root.mkdir(parents=True,exist_ok=True)
    report={"mode":"CCOR_NATIVE_FITS_REFERENCE_DOWNLOAD",
            "production_modified":False,"cases":[],"downloaded":0,
            "source":"NOAA SWPC public CCOR FITS directories",
            "source_format":"FITS scientific arrays, not JPEG quicklooks"}
    total=0
    indexes={}
    for name in cases:
        case=CASES[name]
        camera=case["camera"]
        row={"case":name,"camera":camera,"report":case["report"],"files":[]}
        report["cases"].append(row)
        try:
            if camera not in indexes:
                page=get_small_index(BASE+camera+"/fits/",fetch=fetch_index)
                indexes[camera]=enumerate_index(camera,page)
            index=indexes[camera]
            selected,missing=pick_samples(index,case,context)
            row["listed_fits_total"]=len(index)
            row["selected"]=len(selected)
            row["missing_times"]=missing
            row["matches_exact_report_times"]=len(set(
                t for t,_,_ in case["points"]
                if any(abs((item["time"]-dt.datetime.fromisoformat(t.replace("Z","+00:00"))).total_seconds())<=90 for item in selected)))
            if total+len(selected)>max_files:
                raise ValueError(f"Refusing >{max_files} requested FITS files")
            for item in selected:
                target=root/camera/item["name"]
                target.parent.mkdir(parents=True,exist_ok=True)
                record={"filename":item["name"],"timestamp_utc":item["time"].isoformat().replace("+00:00","Z"),
                        "url":item["url"],"path":str(target)}
                row["files"].append(record)
                if target.exists() and target.stat().st_size>=2880 and fits_header_signature_ok(target.open("rb").read(80)):
                    record["status"]="REUSED_EXISTING"
                    record["bytes"]=target.stat().st_size
                else:
                    try:
                        record.update(download(item["url"],target))
                        record["status"]="DOWNLOADED"
                        report["downloaded"]+=1
                    except Exception as exc:
                        record["status"]="FAILED"
                        record["error"]=f"{type(exc).__name__}: {exc}"
                total+=1
        except Exception as exc:
            row["error"]=f"{type(exc).__name__}: {exc}"
        print(name,":",row.get("selected",0),"selected,",row.get("matches_exact_report_times",0),
              "report times indexed;",len(row.get("missing_times",[])),"missing",flush=True)
    dest=root/"download_manifest.json"
    dest.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("Manifest:",dest,flush=True)
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--case",choices=tuple(CASES),action="append")
    p.add_argument("--context",type=int,default=1)
    p.add_argument("--max-files",type=int,default=24)
    args=p.parse_args()
    if not 0<=args.context<=2:
        p.error("--context must be 0..2")
    if not 1<=args.max_files<=24:
        p.error("--max-files must be 1..24")
    report=download_cases(list(dict.fromkeys(args.case or list(CASES))),
                          args.output_dir.expanduser().resolve(),
                          context=args.context,max_files=args.max_files)
    if any(case.get("error") or any(i["status"]=="FAILED" for i in case["files"])
           or case.get("matches_exact_report_times",0)<len(CASES[case["case"]]["points"])
           for case in report["cases"]):
        print("WARNING: Incomplete FITS retrieval. Use NCEI archive if SWPC was rotated away.",flush=True)
        raise SystemExit(2)
if __name__=="__main__":
    main()
