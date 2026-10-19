#!/usr/bin/env python3
"""Reanalyse completed known-comet CUDA logs without rerunning the GPU."""
import argparse
import json
from pathlib import Path
from benchmark_known_comets import controls, evaluate, parse_output, save_report, truth_rows

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report-dir",type=Path,required=True)
    p.add_argument("--dashboard",type=Path,default=Path(__file__).resolve().parents[1]/"docs/data/candidates.json")
    p.add_argument("--truth",type=Path)
    a=p.parse_args()
    path=a.report_dir.expanduser().resolve()
    original=json.loads((path/"report.json").read_text())
    known={c["event_id"]:c for c in controls(a.dashboard)}
    updated=json.loads(json.dumps(original))
    for row in updated["runs"]:
        if row.get("cuda_returncode") != 0 or not row.get("log_file"):
            continue
        logfile=path / row["log_file"]
        if not logfile.is_file():
            row["reparse_error"]="ORIGINAL_LOG_MISSING"
            continue
        case=known[row["event_id"]]
        parsed=parse_output(logfile.read_text(errors="replace"))
        row.update(evaluate(parsed,case,row["resolution"],
                            truth_rows(a.truth,case["event_id"])))
        row["status"]="COMPLETE_REPARSED"
        row["raw_log_track_headers"]=sum(line.lstrip().startswith("C") and " | pass=" in line
                                      for line in logfile.read_text(errors="replace").splitlines())
        print(f'{row["control"]} {row["resolution"]}: parsed={row["raw_tracks"]}, '
              f'overlap={row["temporal_hypotheses"]}, '
              f'positional={row["positional_recovered"]}')
    output=path/"reparsed"
    save_report(output,updated)
    print("Original report preserved:",path/"report.json")
    print("Reparsed report:",output/"report.json")

if __name__=="__main__":
    main()
