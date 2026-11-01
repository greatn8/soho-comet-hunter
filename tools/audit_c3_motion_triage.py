#!/usr/bin/env python3
"""Read-only comparison against saved reviews and unique historical objects.

Does not download images, launch CUDA, send alerts, write results, or delete files.
Prints JSON to stdout. Published records without motion evidence are UNASSESSED.
No automatic activation: this is NOT a validated differential-star classifier.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from c3_motion_triage import triage, protected, assessed


def rows_in(value):
    if isinstance(value, list):
        yield from (r for r in value if isinstance(r, dict))
    elif isinstance(value, dict):
        if any(k in value for k in ('candidate', 'review_id', 'event_id')):
            yield value
        for key in ('candidates', 'review_candidates', 'latest_candidates', 'pending'):
            child = value.get(key)
            if isinstance(child, dict):
                yield from rows_in(list(child.values()))
            elif isinstance(child, list):
                yield from rows_in(child)


def read_rows(paths):
    rows, errors = [], []
    for path in paths:
        try:
            rows.extend(rows_in(json.loads(path.read_text())))
        except (OSError, ValueError) as exc:
            errors.append({'path': str(path), 'error': str(exc)})
    return rows, errors


def identity(row):
    return row.get('review_id') or row.get('event_id') or row.get('id') or (
        str(row.get('candidate')), str(row.get('first')), str(row.get('last')))


def compare(history, current):
    unique = {}
    labels = defaultdict(set)
    # Prefer evidence-rich duplicate records, independent of file ordering.
    for row in history:
        key = identity(row)
        label = row.get('known_object') or row.get('report_id')
        if label:
            labels[key].add(str(label))
        rank = (assessed(row), bool(row.get('pixel_verification')), protected(row))
        if key not in unique or rank > unique[key][0]:
            unique[key] = (rank, row)
    known = defaultdict(list)
    for key, (_, row) in unique.items():
        # Identity labels from sparse archive records must survive enrichment
        # by a diagnostic-rich row that lacks the original report label.
        for label in sorted(labels[key]):
            known[label].append(row)
    controls = []
    for name, records in sorted(known.items()):
        measured = [r for r in records if assessed(r)]
        stages = Counter(triage(r)['stage'] for r in measured)
        controls.append({'object_or_report': name, 'unique_reviews': len(records),
                         'assessed_reviews': len(measured), 'stages': dict(stages),
                         'result': ('UNASSESSED' if not measured else
                                    'RETAINED' if stages['MOTION_SUPPORTED_REVIEW'] else 'WOULD_DOWNGRADE')})
    dedup = {identity(r): r for r in current}
    counts = Counter(triage(r)['stage'] for r in dedup.values())
    return {'mode': 'SHADOW_ONLY', 'activation_allowed': False,
            'limitation': 'Temporal controls are not proof of motion different from stars. Known-report labels are not independently confirmed identities.',
            'unique_historical_reviews': len(unique), 'historical_controls': controls,
            'current_unique_reviews': len(dedup), 'current_projection': dict(counts),
            'current_details': [{'candidate': r.get('candidate'), 'review_id': r.get('review_id'),
                                 'protected': protected(r), **triage(r)} for r in dedup.values()]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', type=Path, required=True)
    ap.add_argument('--dashboard-dir', type=Path, required=True)
    ap.add_argument('--summary', action='store_true', help='Omit individual current candidate rows')
    args = ap.parse_args()
    results = args.project.expanduser() / 'results/realtime'
    dashboard = args.dashboard_dir.expanduser()
    paths = sorted((results / 'review').glob('RT*/review.json'))
    paths += [p for p in (results/'latest_candidates.json', dashboard/'candidates.json',
                         dashboard/'realtime_archive.json', dashboard/'realtime.json',
                         dashboard/'realtime_strong_pending.json') if p.is_file()]
    history, errors = read_rows(paths)
    current_path = results/'latest_candidates.json'
    if not current_path.is_file():
        current_path = dashboard/'realtime.json'
    current, current_errors = read_rows([current_path])
    report = compare(history, current)
    report['files_read'] = len(paths)
    report['read_errors'] = errors + current_errors
    if args.summary:
        report.pop('current_details', None)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
