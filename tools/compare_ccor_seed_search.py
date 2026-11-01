#!/usr/bin/env python3
"""Compare CCOR seed strategies on identical cached images, without live writes.

No image downloads, reports, alerts, production state updates or file removal.
More hypotheses or eligible tracks are NOT evidence of comet recovery.
"""
import argparse
import json
from pathlib import Path
import time
import ccor_hunter as ch


def compare(camera, cache):
    files=sorted(cache.glob(f'*_{camera}_*by*.jpg'))
    if not files:
        return {'camera':camera,'status':'NO_CACHED_IMAGES'}
    newest=max(ch.stamp(p.name) for p in files)
    frames,times,sizes=ch.load_frames(cache,camera,newest)
    if len(frames)<6:
        return {'camera':camera,'status':'INSUFFICIENT_IMAGES','frames':len(frames)}
    runs=[]
    rows=[]
    for enabled in (False,True):
        start=time.monotonic()
        tracks,info=ch.track_points(frames,times,sizes,camera,complete_search=True,
                                   multi_baseline_seeds=enabled,return_diagnostics=True)
        runs.append({'strategy':'MULTI_BASELINE' if enabled else 'ADJACENT_ONLY',
                     'seconds':round(time.monotonic()-start,2),
                     'search':info,
                     'eligible_selected_tracks':sum(bool(t.get('precap_review_eligible')) for t in tracks),
                     'selected_tracks':len(tracks)})
        rows.append(tracks)
    complete=all(run['search']['scan_completed'] for run in runs)
    old,new=rows
    losses=[r for r in old if r.get('precap_review_eligible') and not any(
        n.get('precap_review_eligible') and ch.match_track(r,n,None,None) for n in new)]
    additions=[r for r in new if r.get('precap_review_eligible') and not any(
        o.get('precap_review_eligible') and ch.match_track(r,o,None,None) for o in old)]
    fields=('id','first','last','frames','vx_px_h','vy_px_h','rms_px','precap_review_eligible')
    return {'camera':camera,'status':'COMPLETE_COMPARISON' if complete else 'INCOMPLETE_SEARCH_BUDGET',
            'frames':len(frames),'same_in_memory_frames':True,
            'first':ch.iso(times[0]),'last':ch.iso(times[-1]),
            'source_gaps_over_30_minutes':sum((b-a).total_seconds()>1800 for a,b in zip(times,times[1:])),
            'runs':runs,
            'additional_eligible_tracks':[{k:r.get(k) for k in fields} for r in additions] if complete else None,
            'old_eligible_tracks_not_retained':[{k:r.get(k) for k in fields} for r in losses] if complete else None,
            'duplicate_selected_ids':[len(rows)-len({r['id'] for r in rows}) for rows in (old,new)],
            'reported_comet_recovery':None,
            'warning':'Synthetic recovery tests pass, but actual report recovery needs calibrated coordinates and source identification. Counts are not comet discoveries.'}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project',type=Path,required=True)
    ap.add_argument('--summary',action='store_true',help='Print counts without per-track lists')
    args=ap.parse_args()
    project=args.project.expanduser().resolve()
    report={'mode':'READ_ONLY_AB_COMPARISON',
            'cameras':[compare(camera,project/'cache/ccor'/camera) for camera in ch.CAMERAS]}
    if args.summary:
        for camera in report['cameras']:
            for field in ('additional_eligible_tracks','old_eligible_tracks_not_retained'):
                if isinstance(camera.get(field),list):
                    camera[field+'_count']=len(camera[field])
                    camera.pop(field)
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
