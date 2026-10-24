#!/usr/bin/env python3
"""Position-blind time-shuffle negative controls for the CCOR JPEG detector.

Uses exactly the saved NOAA Level-3 JPEGs and the completed independent A/B
replays. Scrambles image order against unchanged timestamps. This destroys
ordinary time-ordered moving-object trajectories but preserves image pixels,
per-frame noise, hot pixels and field masks. Null scores are diagnostic only,
NOT validated false-positive rates or comet probabilities.

NO network access, production data writes, watcher changes or email alerts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np

import ccor_hunter as hunter
from ccor_blind_jpeg_replay import CASES, detector_fingerprint, validate_jpeg


def shuffle_indices(n, rng):
    """Reject identity and full reversal, which both preserve straight paths."""
    if n < 6:
        raise ValueError("At least six frames required")
    original = np.arange(n)
    reversed_order = original[::-1]
    for _ in range(100):
        shuffled = rng.permutation(n)
        if not (np.array_equal(shuffled, original)
                or np.array_equal(shuffled, reversed_order)):
            return [int(x) for x in shuffled]
    raise RuntimeError("Unable to obtain nontrivial frame permutation")


def evidence_tuple(row):
    """Numeric ranking fields only; excludes a timestamp tie-breaker."""
    if row is None:
        return (0, 0.0, float("-inf"))
    score = hunter.track_quality(row)
    return (int(score[0]), float(score[1]), float(score[2]))


def validate_and_load(case_name, source_dir):
    case = CASES[case_name]
    camera = case["camera"]
    saved = json.loads((source_dir / ("blind_jpeg_" + case_name + ".json"))
                       .read_text(encoding="utf-8"))
    if (saved.get("status") != "POSITION_BLIND_REPLAY_COMPLETE"
            or saved.get("camera") != camera
            or saved.get("blind_to_report_positions") is not True):
        raise ValueError("Missing valid position-blind JPEG replay")
    valid = [r for r in saved.get("images", [])
             if r.get("status") == "VALIDATED"]
    if len(valid) != saved.get("frame_count") or len(valid) < 6:
        raise ValueError("Incomplete saved source-frame manifest")
    cache = source_dir / case_name / "jpegs"
    for row in valid:
        name = row["name"]
        if (Path(name).name != name or "/" in name or "\\" in name
                or hunter.STAMP_RE.fullmatch(name) is None
                or ("_" + camera + "_") not in name.lower()):
            raise ValueError("Unsafe reference JPEG filename: " + name)
        actual = validate_jpeg(cache / name)["sha256"]
        if actual != row["sha256"]:
            raise ValueError("Source JPEG hash mismatch: " + name)
    start = dt.datetime.fromisoformat(case["from_utc"].replace("Z", "+00:00"))
    end = dt.datetime.fromisoformat(case["through_utc"].replace("Z", "+00:00"))
    frames, times, sizes = hunter.load_frames(
        cache, camera, end, hours=(end - start).total_seconds() / 3600 + .01)
    if (len(frames) != len(valid)
            or [hunter.iso(x) for x in times] != saved.get("processed_times_utc")):
        raise ValueError("Source frame order/times differ from saved blind replay")
    return frames, times, sizes, saved


def summary_of_control(tracks, stats, indices):
    top = tracks[0] if tracks else None
    return {
        "source_frame_permutation": indices,
        "scan_completed": bool(stats.get("scan_completed")),
        "hypotheses_enumerated": stats.get("distinct_hypotheses"),
        "seed_pairs_examined": stats.get("seed_pairs_examined"),
        "stop_reason": stats.get("stop_reason"),
        "best_track_frames": top.get("frames") if top else 0,
        "best_track_rms_px": top.get("rms_px") if top else None,
        "best_numeric_rank_fields": list(evidence_tuple(top)),
        "top10_frame_counts": [r.get("frames") for r in tracks[:10]],
    }


def run_case(case_name, source_dir, ab_dir, output_dir, trials, seed):
    source_dir = Path(source_dir)
    ab_dir = Path(ab_dir)
    output_dir = Path(output_dir)
    ab = json.loads((ab_dir / ("complete_search_ab_" + case_name + ".json"))
                    .read_text(encoding="utf-8"))
    if (ab.get("case") != case_name
            or ab.get("status") != "COMPLETE_AB_COMPARISON"
            or not ab.get("baseline_reproduced_exactly")
            or ab.get("scan_completed") is not True):
        raise ValueError("Must supply verified, complete A/B benchmark")
    if ab.get("current_detector_sha256") != detector_fingerprint():
        raise ValueError("Detector changed since A/B benchmark; rerun A/B first")
    frames, times, sizes, _ = validate_and_load(case_name, source_dir)
    if len(frames) != ab.get("frames"):
        raise ValueError("Frame count differs from A/B benchmark")
    real = ab.get("all_complete_search_top40", [])
    if not real:
        raise ValueError("Real benchmark has no top-40 tracks")
    real_score = evidence_tuple(real[0])
    rng = np.random.default_rng(seed)
    controls = []
    observed_null_at_least_as_strong = 0
    incomplete = 0
    for trial in range(trials):
        permutation = shuffle_indices(len(frames), rng)
        # Keep timestamps and dimensions fixed. Shuffle only image pixels.
        altered = [frames[k] for k in permutation]
        detected, stats = hunter.track_points(
            altered, times, sizes, CASES[case_name]["camera"],
            complete_search=True, return_diagnostics=True)
        if not stats["scan_completed"]:
            incomplete += 1
        elif evidence_tuple(detected[0] if detected else None) >= real_score:
            observed_null_at_least_as_strong += 1
        row = summary_of_control(detected, stats, permutation)
        row["trial_index"] = trial
        controls.append(row)
        print(f"  {case_name}: null {trial+1}/{trials} "
              f"frames_best={row['best_track_frames']} "
              f"hypotheses={row['hypotheses_enumerated']} "
              f"complete={row['scan_completed']}", flush=True)
    complete_count = trials - incomplete
    report = {
        "mode": "CCOR_POSITION_BLIND_FRAME_ORDER_SHUFFLE_NEGATIVE_CONTROL",
        "case": case_name,
        "camera": CASES[case_name]["camera"],
        "source_level": "NOAA_L3_JPEG",
        "seed": seed,
        "trials_requested": trials,
        "trials_completed": complete_count,
        "incomplete_trials": incomplete,
        "observed_real_top_track_frames": real[0]["frames"],
        "observed_real_top_track_rms_px": real[0]["rms_px"],
        "observed_real_top_track_rank_fields": list(real_score),
        "controls_at_least_real_best_numeric_rank": observed_null_at_least_as_strong,
        "controls": controls,
        "all_controls_complete": incomplete == 0,
        "status": "COMPLETE_NULL_CONTROL" if incomplete == 0
                  else "INCOMPLETE_NULL_CONTROLS",
        "reported_comet_recovered": None,
        "false_positive_rate_measured": False,
        "network_access": False,
        "production_modified": False,
        "limitations": [
            "Shuffling frame order is an artificial null, not a real-world negative sky survey.",
            "Static or repetitive instrument artifacts can survive this control.",
            "This score is not a statistical comet probability or independently measured recall.",
            "Reported-comet geometry requires independent detector/JPEG registration.",
            "CCOR1 and CCOR2 are separate cameras and must be evaluated separately.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / ("temporal_null_" + case_name + ".json")
    if dest.exists():
        raise FileExistsError("Refusing to overwrite earlier result: " + str(dest))
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{case_name}: REAL best frames={real[0]['frames']} "
          f"versus {observed_null_at_least_as_strong}/{complete_count} "
          f"completed nulls >= real ranking score. Status={report['status']}",
          flush=True)
    print("  Saved:", dest, flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-dir", type=Path, required=True)
    p.add_argument("--ab-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--case", choices=tuple(CASES), action="append")
    p.add_argument("--trials", type=int, default=4)
    p.add_argument("--seed", type=int, default=20261024)
    a = p.parse_args()
    if not 1 <= a.trials <= 32:
        p.error("Trials must be in 1..32")
    source = a.source_dir.expanduser().resolve()
    ab = a.ab_dir.expanduser().resolve()
    output = a.output_dir.expanduser().resolve()
    if (output == source or output == ab
            or source in output.parents or ab in output.parents
            or output in source.parents or output in ab.parents
            or "comet_hunter_native_cuda_v7_archive" in output.parts):
        p.error("Output must be a separate, non-production directory")
    failed = False
    for case in dict.fromkeys(a.case or CASES):
        try:
            report = run_case(case, source, ab, output, a.trials, a.seed)
            failed |= report["status"] != "COMPLETE_NULL_CONTROL"
        except Exception as exc:
            failed = True
            print(f"{case}: FAILED: {type(exc).__name__}: {exc}", flush=True)
    if failed:
        raise SystemExit(2)
    print("Position-blind temporal null controls finished. No comet recovery claim.")


if __name__ == "__main__":
    main()
