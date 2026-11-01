#!/usr/bin/env python3
"""Replay reference UTC windows blind to report positions, with both seed modes.

Windows were selected from Sungrazer reports. Their coordinates and endorsement
labels are deliberately kept out of this module. Original JPEGs and fixed blind
outputs are retained for a separate registration and recovery audit.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import ccor_hunter as ch
from ccor_blind_jpeg_replay import acquire_frames, parse_time, detector_fingerprint

# Time-selected, position-blind. No report coordinates enter detection.
WINDOWS = {
    'ccor1-20261022-ruan': ('ccor1', '2026-10-22T11:30:00Z', '2026-10-22T14:30:00Z'),
    'ccor2-20261022-wage': ('ccor2', '2026-10-22T22:45:00Z', '2026-10-23T02:15:00Z'),
    'ccor1-20261026-wage': ('ccor1', '2026-10-26T15:45:00Z', '2026-10-26T19:15:00Z'),
    'ccor1-20261029-wage': ('ccor1', '2026-10-29T19:15:00Z', '2026-10-29T23:30:00Z'),
    'ccor2-20261028-boonplod': ('ccor2', '2026-10-28T00:45:00Z', '2026-10-28T04:00:00Z'),
    'ccor2-20261029-wage': ('ccor2', '2026-10-29T19:15:00Z', '2026-10-29T23:30:00Z'),
}


def add_review_evidence(rows, frames, times):
    available = [ch.iso(t) for t in times]
    by_time = dict(zip(available, frames))
    baseline = np.median(np.stack(frames), axis=0)
    rims = {t: ch.outer_rim_mask(f) for t, f in by_time.items()}
    result = []
    for row in rows:
        row = ch.annotated_track(row)
        row['review_validation'] = ch.assess_review_evidence(row, available, by_time, baseline)
        row['field_rim_review'] = ch.track_rim_evidence(row, rims)
        row['review_stage'] = ch.early_motion_review(row)
        result.append(row)
    return result


def replay(name, output, no_download=False):
    camera, start, end = WINDOWS[name]
    cache = output / name / 'jpegs'
    spec = dict(camera=camera, from_utc=start, through_utc=end)
    manifest, errors = acquire_frames(spec, cache, no_download=no_download)
    frames, times, sizes = ch.load_frames(cache, camera, parse_time(end),
        hours=(parse_time(end)-parse_time(start)).total_seconds()/3600 + .001)
    row = dict(case=name, camera=camera, window=spec, images=manifest,
        errors=errors, frames=len(frames), processed_times=[ch.iso(t) for t in times],
        detector_source_sha256=detector_fingerprint(),
        blind_to_report_positions=True, blind_to_report_times=False,
        production_modified=False, same_in_memory_frames=True, runs=[],
        reported_object_recovered=None, status='INSUFFICIENT_IMAGES')
    if len(frames) >= 6:
        # Any source errors remain visible; do not silently call them misses.
        for enhanced in (False, True):
            found, diag = ch.track_points(frames, times, sizes, camera,
                complete_search=True, multi_baseline_seeds=enhanced,
                return_diagnostics=True)
            if not diag['scan_completed']:
                row['status'] = 'INCOMPLETE_SEARCH'
            annotated = add_review_evidence(found, frames, times)
            # No retained historical reviews are mixed into this isolated replay.
            shortlist = ch.review_shortlist(annotated)
            ranked = sorted(annotated, key=ch.track_quality, reverse=True)
            row['runs'].append(dict(strategy='MULTI_BASELINE' if enhanced else 'ADJACENT_ONLY',
                search=diag, selected_tracks=len(found),
                review_eligible=sum(bool(r.get('precap_review_eligible')) for r in found),
                isolated_shortlist_ids=shortlist,
                ranked_tracks=[dict(r, blind_rank=i+1) for i, r in enumerate(ranked)]))
        if row['status'] != 'INCOMPLETE_SEARCH':
            row['status'] = 'BLIND_REPLAY_COMPLETE'
    output.mkdir(parents=True, exist_ok=True)
    path = output / (name + '.json')
    path.write_text(json.dumps(row, indent=2) + '\n')
    print(name, row['status'], 'frames', len(frames),
        'eligible/shortlist', [(r['review_eligible'], len(r['isolated_shortlist_ids']))
                              for r in row['runs']], flush=True)
    return row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--case', choices=tuple(WINDOWS), action='append')
    p.add_argument('--no-download', action='store_true')
    args = p.parse_args()
    rows = [replay(n, args.output_dir, args.no_download)
            for n in dict.fromkeys(args.case or WINDOWS)]
    if any(r['status'] != 'BLIND_REPLAY_COMPLETE' for r in rows):
        raise SystemExit('At least one case is unassessed; see saved source/search errors.')


if __name__ == '__main__':
    main()
