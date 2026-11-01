#!/usr/bin/env python3
"""Score fixed position-blind replays; refuse recovery claims without registration.

Calibration is per case, established using non-comet image landmarks with a
separate validation set. No transform is fitted from report coordinates.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import numpy as np
from ccor_evaluate_blind_jpeg import fit_affine_from_landmarks


def score(replay, reference, calibration=None):
    result = dict(status='UNREGISTERED', reported_object_recovered=None,
                  reference_label=reference['reference_label'], runs=[])
    if replay.get('status') != 'BLIND_REPLAY_COMPLETE':
        result['status'] = 'REPLAY_INCOMPLETE'
        return result
    if not calibration:
        return result
    if (calibration.get('independent_of_report_positions') is not True
            or not calibration.get('provenance')):
        raise ValueError('Independent image registration provenance is required')
    transform, validation = fit_affine_from_landmarks(calibration['landmarks'])
    if not np.isfinite(transform).all() or abs(np.linalg.det(transform[:2])) < 1e-8:
        raise ValueError('Invalid or degenerate affine transform')
    result['registration_validation'] = validation
    if validation['validation_rms_pixels'] > 3 or validation['validation_worst_pixels'] > 6:
        result['status'] = 'REGISTRATION_FAILED'
        return result
    report_points = {p[0]: np.array([p[1], p[2], 1.0]) @ transform
                     for p in reference['points']}
    covered = set(replay['processed_times']) & report_points.keys()
    result['report_times_covered'] = len(covered)
    if len(covered) < max(3, math.ceil(.6 * len(report_points))):
        result['status'] = 'INSUFFICIENT_REPORT_TIME_COVERAGE'
        return result
    result['status'] = 'REGISTERED_COMPARISON'
    for run in replay['runs']:
        candidates = []
        for row in run['ranked_tracks']:
            scale = np.asarray(row['source_jpeg_shape']) / np.asarray(row['processing_shape'])
            if not np.isfinite(scale).all() or (scale <= 0).any():
                raise ValueError('Invalid image dimensions')
            errors = [float(np.linalg.norm(
                report_points[p['time']]/scale - np.array([p['x'], p['y']])))
                for p in row['points'] if p['time'] in report_points]
            if len(errors) < 3:
                continue
            med, worst = float(np.median(errors)), max(errors)
            candidates.append(dict(id=row['id'], blind_rank=row['blind_rank'],
                observed_report_times=len(errors), median_error_px=round(med, 3),
                worst_error_px=round(worst, 3), position_match=med <= 3 and worst <= 6,
                review_eligible=bool(row.get('precap_review_eligible')),
                isolated_shortlist=row['id'] in run['isolated_shortlist_ids']))
        candidates.sort(key=lambda r: r['median_error_px'])
        matches = [r for r in candidates if r['position_match']]
        result['runs'].append(dict(strategy=run['strategy'],
            detected_report_path=bool(matches),
            eligible_report_path=any(r['review_eligible'] for r in matches),
            shortlisted_report_path=any(r['isolated_shortlist'] for r in matches),
            best_candidates=candidates[:5], matches=matches))
    # Results concern positional recovery of a reported path, not comet identity.
    result['reported_object_recovered'] = any(r['detected_report_path'] for r in result['runs'])
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--replay-dir', required=True, type=Path)
    p.add_argument('--calibration', type=Path, help='Independent registration by case name')
    a = p.parse_args()
    references = json.loads(Path(__file__).with_name('ccor_reference_reports.json').read_text())
    calibrations = json.loads(a.calibration.read_text()) if a.calibration else {}
    rows = []
    for name, reference in references.items():
        path = a.replay_dir / (name + '.json')
        if not path.exists():
            row = dict(case=name, status='NO_REPLAY', reported_object_recovered=None)
        else:
            replay = json.loads(path.read_text())
            try:
                row = dict(case=name, **score(replay, reference, calibrations.get(name)))
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                row = dict(case=name, status='INVALID_CALIBRATION_OR_INPUT',
                           error=str(exc), reported_object_recovered=None)
        row['report_url'] = reference['report_url']
        rows.append(row)
        print(name, row['status'], 'recovered:', row['reported_object_recovered'])
    a.replay_dir.mkdir(parents=True, exist_ok=True)
    (a.replay_dir / 'recovery_scores.json').write_text(json.dumps(dict(
        mode='POSTHOC_REPORTED_PATH_RECOVERY', confirmed_identity_claimed=False,
        shortlist_note='Isolated replay shortlist, without prior retained reviews.',
        cases=rows), indent=2)+'\n')


if __name__ == '__main__':
    main()
