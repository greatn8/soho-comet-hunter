#!/usr/bin/env python3
"""Offline native-grid temporal review; no coordinate calibration is inferred."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image
from ccor_native_report_kit import validate_source, sha256_file, iso


def review(fits_dir, output_dir):
    paths=sorted(fits_dir.rglob('*.fits'))
    if not 3<=len(paths)<=24:
        raise ValueError('Supply 3..24 original FITS files for one camera')
    if output_dir.exists():
        raise FileExistsError('Use a new review output directory')
    records=[validate_source(p) for p in paths]
    if len({r['camera'] for r in records})!=1:
        raise ValueError('Review one camera at a time')
    records=sorted(zip(paths,records),key=lambda pr:pr[1]['timestamp'])
    a=np.stack([r['array'] for _,r in records])
    a=np.where(np.isfinite(a),a,np.nan)
    masked=np.all(np.isnan(a),axis=0)
    a[:,masked]=0
    residual=a-np.nanmedian(a,axis=0)
    # Local, per-pixel temporal scale: masks/large gradients cannot dominate it.
    scale=np.nanmedian(np.abs(residual),axis=0)*1.4826
    positive=scale[np.isfinite(scale)&(scale>0)]
    if not positive.size:
        raise ValueError('Sequence has no measurable temporal variation')
    floor=float(np.percentile(positive,25))
    normalized=residual/np.maximum(scale,floor)
    output_dir.mkdir(parents=True)
    sources=[]
    for i,(p,r) in enumerate(records):
        dest=output_dir/(p.name+'.temporal.png')
        pixels=np.uint8(np.rint(np.nan_to_num(np.clip(128+normalized[i]*16,0,255),nan=0)))
        Image.fromarray(pixels).save(dest)
        sources.append({'source_file':p.name,'source_sha256':sha256_file(p),
                        'timestamp_utc':iso(r['timestamp']), 'preview':dest.name,
                        'preview_sha256':sha256_file(dest)})
    result={'coordinate_origin':'TOP_LEFT_ZERO_BASED','native_dimensions':[2048,1920],
            'processing':'TEMPORAL_MEDIAN_SUBTRACTION_LOCAL_TEMPORAL_MAD_DISPLAY_ONLY',
            'warning':'Short sequence background subtraction can suppress slow sources and create residuals. Inspect original FITS too. No registration, rotation, crop or resampling.',
            'coordinate_origin_confirmed':False,'ready_to_submit':False,'sources':sources}
    (output_dir/'sequence_manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    page='<!doctype html><meta charset="utf-8"><title>Native CCOR sequence review</title>\n<style>body{background:#171717;color:#eee;font:16px sans-serif;margin:20px}button,input{margin:8px}#frame{display:block;max-width:none;image-rendering:pixelated}#viewport{overflow:auto;height:78vh;border:1px solid #777}#info{white-space:pre-wrap}</style>\n<h2>Native CCOR temporal review</h2><p>Display aid only. Background subtraction can suppress sources or create residuals. Coordinates: top-left (0,0), X right, Y down. Calibration and comet identity remain unverified.</p>\n<button id="prev">Previous</button><button id="next">Next</button><button id="play">Play / pause</button><label>Zoom <input id="zoom" type="range" min="25" max="200" value="50"></label><span id="label"></span><p id="info">Move over the image to read native pixel coordinates. Arrow keys change frames.</p><div id="viewport"><img id="frame"></div>\n<script>const sources=__SOURCES__;let index=0,timer=null;const img=document.getElementById(\'frame\');function draw(){img.src=sources[index].preview;img.style.width=(2048*Number(document.getElementById(\'zoom\').value)/100)+\'px\';document.getElementById(\'label\').textContent=(index+1)+\' / \'+sources.length+\' — \'+sources[index].timestamp_utc;}function step(n){index=(index+n+sources.length)%sources.length;draw()}document.getElementById(\'prev\').onclick=()=>step(-1);document.getElementById(\'next\').onclick=()=>step(1);document.getElementById(\'zoom\').oninput=draw;document.getElementById(\'play\').onclick=()=>{if(timer){clearInterval(timer);timer=null}else timer=setInterval(()=>step(1),600)};document.onkeydown=e=>{if(e.key===\'ArrowRight\')step(1);if(e.key===\'ArrowLeft\')step(-1)};img.onmousemove=e=>{const r=img.getBoundingClientRect(),x=Math.floor((e.clientX-r.left)*2048/r.width),y=Math.floor((e.clientY-r.top)*1920/r.height);if(x>=0&&x<2048&&y>=0&&y<1920)document.getElementById(\'info\').textContent=\'Native preview (top-left, zero-based): x=\'+x+\', y=\'+y+\' | FITS array indices (one-based): \'+(x+1)+\', \'+(y+1)+\'\\n\'+sources[index].source_file;};draw();</script>'
    (output_dir/'index.html').write_text(page.replace('__SOURCES__',json.dumps(sources)))
    print('Wrote',len(sources),'native-grid temporal previews to',output_dir)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fits-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args()
    review(args.fits_dir.expanduser().resolve(),args.output_dir.expanduser().resolve())
