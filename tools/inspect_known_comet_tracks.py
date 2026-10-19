#!/usr/bin/env python3
"""Read-only diagnostics for a known-comet CUDA replay log."""
import argparse
import json
import math
from collections import Counter
from pathlib import Path
from benchmark_known_comets import parse_output


def inspect(text):
    rows=parse_output(text)
    headers=sum(" | pass=" in line and line.lstrip().startswith("C")
                for line in text.splitlines())
    failures=Counter()
    locations=Counter()
    ranked=[]
    for c in rows:
        hours=(c["last"]["time"]-c["first"]["time"]).total_seconds()/3600
        if hours<=0:
            failures["nonpositive_time_span"]+=1
            continue
        delta_x=c["last"]["x"]-c["first"]["x"]
        delta_y=c["last"]["y"]-c["first"]["y"]
        # This is displacement, not pixel verification or discovery proof.
        dist=math.hypot(delta_x,delta_y)
        if dist<1:
            failures["near_stationary_endpoints"]+=1
        if c["rms"]>1.2:
            failures["rms_gt_1.2"]+=1
        ranked.append((dist,c["id"],c["frames"],c["priority"],c["rms"],
                       [c["first"]["x"],c["first"]["y"]],
                       [c["last"]["x"],c["last"]["y"]]))
        # Split field into a coarse, transparent quadrant for audit.
        locations[(int(c["first"]["x"]//128),int(c["first"]["y"]//128))]+=1
    return {"cuda_headers":headers,"parsed_tracks":len(rows),
            "parse_loss":max(0,headers-len(rows)),
            "candidate_cap_hit":len(rows)>=500,
            "quality_warnings":dict(failures),
            "starting_grid_top":locations.most_common(8),
            "sample_tracks":ranked[:5]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report-dir",required=True,type=Path)
    p.add_argument("--event-id",default="EV20260730T0530_1E9C7C")
    args=p.parse_args()
    folder=args.report_dir.expanduser()
    for resolution in (512,1024):
        logfile=folder/f"{args.event_id}_{resolution}.log"
        if not logfile.is_file():
            print(f"{resolution}: no saved log ({logfile})")
            continue
        result=inspect(logfile.read_text(errors="replace"))
        print(f"\n==== {resolution} original pixels ====")
        print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
