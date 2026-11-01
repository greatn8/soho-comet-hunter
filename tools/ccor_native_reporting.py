#!/usr/bin/env python3
"""Make a portable, offline CCOR native-FITS coordinate reviewer.

Downloads at most six original science frames for measured candidate times,
into temporary storage, or reads --fits-dir. No live caches or workers touched.
Only intensity changes are applied. The display flips array rows once so FITS
axis 2 increases upwards. A click at upper-left (x,y) therefore maps EXACTLY
to FITS 1-based (x+1, height-y), independent of spacecraft roll or JPEG layout.
"""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
import numpy as np
from PIL import Image

NAME = re.compile(r'CCOR([12])_(1A|1B)_(20\d{6}T\d{6})_[A-Za-z0-9_-]+\.fits(?:\.gz)?$', re.I)
MAX_FRAMES = 6
MAX_BYTES = 24_000_000
SHAPE = (1920, 2048)


def timestamp(value):
    t = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('UTC offset required')
    return t.astimezone(dt.timezone.utc)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def native_pixel(x, y, shape=SHAPE):
    """Upper-left displayed pixel -> original array row/column + FITS pixel."""
    height, width = shape
    if type(x) is not int or type(y) is not int or not (0 <= x < width and 0 <= y < height):
        raise ValueError('Pixel outside native image')
    return height-1-y, x, [x+1, height-y]


def load_source(path, camera=None, source_url=None):
    from astropy.io import fits
    match = NAME.fullmatch(path.name)
    if not match or not 2880 <= path.stat().st_size <= MAX_BYTES:
        raise ValueError('Invalid or oversized native science FITS: '+path.name)
    found_camera = 'ccor'+match[1]
    if camera is not None and camera != found_camera:
        raise ValueError('FITS camera does not match candidate')
    raw = path.read_bytes()
    with fits.open(io.BytesIO(raw), memmap=False) as hdus:
        science = next((h for h in hdus if h.data is not None and h.data.ndim == 2), None)
        if science is None or science.data.shape != SHAPE:
            raise ValueError('Expected 2048 x 1920 science image')
        # Accessing the entire compressed array catches truncated FITS heaps.
        data = np.array(science.data, dtype=np.float32, copy=True)
        header = science.header
        instrument = str(header.get('INSTRUME', '')).upper().replace('-', '')
        if instrument in ('CCOR1', 'CCOR2') and instrument.lower() != found_camera:
            raise ValueError('FITS instrument and filename camera disagree')
        when = header.get('DATE-OBS', hdus[0].header.get('DATE-OBS'))
        if not when:
            raise ValueError('FITS DATE-OBS is required')
        when = timestamp(str(when)+'Z' if not re.search(r'(Z|[+-]\d\d:\d\d)$', str(when)) else when)
        named_time = dt.datetime.strptime(match[3], '%Y%m%dT%H%M%S').replace(tzinfo=dt.timezone.utc)
        if abs((when-named_time).total_seconds()) > 2:
            raise ValueError('FITS header time and filename disagree')
        for h in hdus:
            if 'CHECKSUM' in h.header and h.verify_checksum() != 1:
                raise ValueError('FITS checksum failed')
            if 'DATASUM' in h.header and h.verify_datasum() != 1:
                raise ValueError('FITS data checksum failed')
    return dict(camera=found_camera, source_file=path.name, source_sha256=digest(raw),
        source_url=source_url, time_utc=when.isoformat().replace('+00:00', 'Z'), data=data)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.hrefs.extend(v for k, v in attrs if k == 'href' and v)


def fetch(url, cap):
    with urlopen(Request(url, headers={'User-Agent':'CCOR-native-report-review/1.0'}), timeout=45) as response:
        value = response.read(cap+1)
    if len(value) > cap:
        raise ValueError('NOAA response exceeds size limit')
    return value


def select_sources(camera, times, index):
    base = 'https://services.swpc.noaa.gov/products/'+camera+'/fits/'
    parser = Links()
    parser.feed(index.decode('utf-8', 'replace'))
    available = []
    for href in parser.hrefs:
        url = urljoin(base, href)
        parsed = urlsplit(url)
        name = parsed.path.rsplit('/', 1)[-1]
        m = NAME.fullmatch(name)
        if (not m or 'ccor'+m[1] != camera or parsed.scheme != 'https'
                or parsed.netloc != 'services.swpc.noaa.gov'
                or parsed.path.rsplit('/', 1)[0]+'/' != urlsplit(base).path
                or parsed.query or parsed.fragment):
            continue
        when = dt.datetime.strptime(m[3], '%Y%m%dT%H%M%S').replace(tzinfo=dt.timezone.utc)
        available.append((when, m[2].upper(), name, url))
    selected = {}
    for when in times:
        choices = sorted(available, key=lambda r: (abs((r[0]-when).total_seconds()), r[1] != '1B', r[2]))
        if not choices or abs((choices[0][0]-when).total_seconds()) > 90:
            raise ValueError('No original FITS within 90 seconds of '+when.isoformat())
        best = choices[0]
        selected[best[0]] = best
    if not 3 <= len(selected) <= MAX_FRAMES:
        raise ValueError('Require three to six distinct original observations')
    return list(selected.values())


def candidate_times(dashboard, candidate):
    tracks = [r for r in dashboard.get('tracks', []) if r.get('id') == candidate]
    if len(tracks) != 1 or tracks[0].get('camera') not in ('ccor1', 'ccor2'):
        raise ValueError('Candidate is missing or ambiguous in CCOR dashboard')
    row = tracks[0]
    times = sorted({timestamp(p['time']) for p in row.get('points', [])})
    if len(times) < 3:
        raise ValueError('Candidate needs at least three measured observation times')
    indices = np.linspace(0, len(times)-1, min(MAX_FRAMES, len(times))).round().astype(int)
    return row['camera'], [times[i] for i in indices]


def png(data):
    # FITS axis 2 runs upwards; CSS/image y runs downwards. Exactly one row flip.
    image = Image.fromarray(np.flipud(data).copy())
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode('ascii')


def prepare(sources, output, candidate='Native CCOR review'):
    if output.exists():
        raise FileExistsError('Use a new output HTML file')
    if not 3 <= len(sources) <= MAX_FRAMES or len({r['camera'] for r in sources}) != 1:
        raise ValueError('Require three to six frames from one camera')
    sources = sorted(sources, key=lambda r: timestamp(r['time_utc']))
    if len({r['time_utc'] for r in sources}) != len(sources):
        raise ValueError('Repeated observation time')
    array = np.stack([r['data'] for r in sources])
    if array.shape[1:] != SHAPE:
        raise ValueError('Source dimensions changed')
    valid = array[np.isfinite(array) & (array > 0)]
    if len(valid) < 10000:
        raise ValueError('Insufficient valid science pixels')
    scale = float(np.median(valid))
    logged = np.log1p(np.maximum(array, 0)/scale)
    low, high = np.nanpercentile(logged, [1, 99.8])
    if not high > low:
        raise ValueError('Sequence has no usable image contrast')
    original = np.rint(np.nan_to_num(np.clip((logged-low)/(high-low), 0, 1))*255).astype(np.uint8)
    # Temporal display aid: intensity-only, same source pixels and orientation.
    masked = ~np.isfinite(array).any(axis=0)
    array[:, masked] = 0
    residual = array-np.nanmedian(array, axis=0)
    noise = np.nanmedian(abs(residual), axis=0)*1.4826
    positive = noise[np.isfinite(noise) & (noise > 0)]
    floor = float(np.percentile(positive, 30)) if positive.size else scale
    temporal = np.rint(np.nan_to_num(np.clip(128+16*residual/np.maximum(noise, floor), 0, 255))).astype(np.uint8)
    frames = []
    for i, r in enumerate(sources):
        frames.append({k:v for k,v in r.items() if k != 'data'} | dict(
            original=png(original[i]), temporal=png(temporal[i])))
    config = dict(candidate=candidate, camera=sources[0]['camera'], width=2048, height=1920,
                  geometry='FITS_AXIS2_UP_DISPLAY_FLIPUD_ONLY', frames=frames)
    payload = json.dumps(config, separators=(',', ':')).replace('<', '\\u003c')
    page = HTML.replace('__CONFIG__', payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding='utf-8')
    print('Created', output, '|', len(frames), 'original FITS frames | 2048 x 1920')
    return config


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', help='CCOR candidate ID; selects measured times only')
    p.add_argument('--dashboard', type=Path, default=Path('docs/data/ccor.json'))
    p.add_argument('--fits-dir', type=Path, help='Read local native science FITS instead of downloading')
    p.add_argument('--output', type=Path, required=True, help='New portable HTML file')
    a = p.parse_args()
    if a.fits_dir:
        paths = sorted(a.fits_dir.glob('*.fits'))+sorted(a.fits_dir.glob('*.fits.gz'))
        if not 3 <= len(paths) <= MAX_FRAMES:
            p.error('Local review requires three to six original science FITS files')
        sources = [load_source(path) for path in paths]
    else:
        if not a.candidate:
            p.error('Supply --candidate or --fits-dir')
        camera, times = candidate_times(json.loads(a.dashboard.read_text()), a.candidate)
        base = 'https://services.swpc.noaa.gov/products/'+camera+'/fits/'
        chosen = select_sources(camera, times, fetch(base, 2_000_000))
        sources = []
        with tempfile.TemporaryDirectory(prefix='ccor-native-report-') as directory:
            for _, _, name, url in chosen:
                path = Path(directory)/name
                path.write_bytes(fetch(url, MAX_BYTES))
                sources.append(load_source(path, camera, url))
    prepare(sources, a.output.expanduser().resolve(), a.candidate or 'Native CCOR review')


HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CCOR native coordinate review</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0c1422;color:#e5edf7;font:15px system-ui,sans-serif}main{max-width:1400px;margin:auto;padding:20px}h1{font-size:24px;margin:0 0 8px}p{line-height:1.5;color:#b7c9df}button,select{font:inherit;background:#20334e;color:white;border:1px solid #557399;border-radius:6px;padding:9px;cursor:pointer}button:disabled{opacity:.4;cursor:default}label{display:inline-flex;gap:8px;align-items:center}.controls{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:12px 0}.badge{background:#173f43;color:#b1f7d4;padding:6px 10px;border-radius:6px}#viewport{overflow:auto;height:65vh;border:1px solid #557399;background:#02060c}#stage{position:relative;line-height:0;width:max-content}#image{display:block;max-width:none;image-rendering:pixelated;cursor:crosshair}#marks{position:absolute;inset:0;pointer-events:none;width:100%;height:100%}#position,#frameInfo{font-variant-numeric:tabular-nums;overflow-wrap:anywhere}textarea{width:100%;background:#111e31;color:#e8f2ff;padding:12px;border:1px solid #557399;border-radius:6px;font:13px monospace}#samples{padding-left:22px}#samples li{margin:6px 0}a{color:#9bcaff}.small{font-size:13px}.panels{display:grid;grid-template-columns:1fr 1fr;gap:24px}@media(max-width:850px){.panels{grid-template-columns:1fr}}
</style>
<main>
<h1>CCOR · native coordinate review</h1>
<p id="heading"></p>
<div class="controls"><span class="badge">2048 × 1920 · original FITS grid</span><span>Report origin: Upper Left (0,0)</span></div>
<p>Click the same visible object in at least three frames. Pink marks are your measurements. The preview keeps every native pixel; zoom changes only its screen size.</p>
<div class="controls"><button id="previous">← Frame</button><button id="play">Play</button><button id="next">Frame →</button><strong id="frameNumber"></strong><label>View <select id="view"><option value="original">Original · intensity stretch</option><option value="temporal">Temporal contrast</option></select></label><label>Zoom <input id="zoom" type="range" min="25" max="200" value="50"><span id="zoomValue">50%</span></label></div>
<p id="frameInfo" class="small"></p><p id="position">Move over the image to read native coordinates.</p>
<div id="viewport"><div id="stage"><img id="image" alt="Original CCOR FITS pixels"><svg id="marks" viewBox="0 0 2048 1920"></svg></div></div>
<p class="small">FITS axis 2 points upward. The preview displays row 0 at the bottom. Upper-left (X,Y) maps to FITS pixel (X+1,1920−Y). Both coordinate conventions and original observation times are included in the export. Temporal contrast can suppress slow sources; check the original view too.</p>
<div class="panels"><section><h2>Measured positions</h2><strong id="count">0 frames marked</strong><ol id="samples"></ol><p id="status" role="status">Object identity remains for you to assess.</p></section><section><h2>Sungrazer report</h2><div class="controls"><button id="copy" disabled>Copy report</button><button id="download" disabled>Download report</button><button id="json" disabled>Download measurements</button><a href="https://sungrazer.nrl.navy.mil/report" target="_blank" rel="noopener">Open report form ↗</a></div><textarea id="report" rows="14" readonly aria-label="Report coordinates"></textarea></section></div>
</main>
<script id="config" type="application/json">__CONFIG__</script>
<script>
(function(root){
'use strict';
function nativePixel(x,y,width=2048,height=1920){if(!Number.isInteger(x)||!Number.isInteger(y)||x<0||y<0||x>=width||y>=height)throw Error('Outside native image');return {x,y,fits_pixel_1based:[x+1,height-y],array_column:x,array_row:height-1-y};}
function pixelAt(cx,cy,r,width=2048,height=1920){if(![cx,cy,r.left,r.top,r.width,r.height].every(Number.isFinite)||r.width<=0||r.height<=0)return null;const x=Math.floor((cx-r.left)*width/r.width),y=Math.floor((cy-r.top)*height/r.height);try{return nativePixel(x,y,width,height)}catch(e){return null}}
function makeReport(config,observations){if(observations.length<3)throw Error('Mark the same object in at least three frames.');const rows=observations.map(r=>{const f=config.frames.find(f=>f.source_file===r.source_file);if(!f||r.source_sha256!==f.source_sha256)throw Error('Unknown science frame');return {...nativePixel(r.x,r.y),source_file:f.source_file,source_sha256:f.source_sha256,source_url:f.source_url,time_utc:f.time_utc};}).sort((a,b)=>a.time_utc.localeCompare(b.time_utc));if(new Set(rows.map(r=>r.time_utc)).size!==rows.length)throw Error('Use distinct source frames');return {camera:config.camera.toUpperCase(),candidate_id:config.candidate,image_size:[2048,1920],coordinate_origin:'UPPER_LEFT_ZERO_BASED',geometry:config.geometry,jpeg_conversion_applied:false,object_identity_confirmed:false,observations:rows};}
function reportText(r){const lines=['Camera: '+r.camera,'Image size: 2048x1920 (native FITS)','Your (0,0) position: Upper Left','X increases right; Y increases down. Zero-based pixel positions.','Object/candidate: '+r.candidate_id,''];let date='';for(const p of r.observations){if(p.time_utc.slice(0,10)!==date){date=p.time_utc.slice(0,10);lines.push('Image date (UTC): '+date,'HHMM X Y');}lines.push(p.time_utc.slice(11,16).replace(':','')+' '+p.x+' '+p.y);}lines.push('','Note to Admin / source provenance:','Positions measured on the original 2048x1920 FITS grid. FITS axis 2 is displayed upward; upper-left Y = 1920 - FITS one-based Y.','Full source UTC times, filenames, and FITS one-based pixels:');for(const p of r.observations){lines.push(p.time_utc+' | '+p.source_file+' | FITS pixel '+p.fits_pixel_1based.join(' '),'SHA256 '+p.source_sha256);if(p.source_url)lines.push(p.source_url);}return lines.join('\n');}
root.CCORNative={nativePixel,pixelAt,makeReport,reportText};
if(typeof module!=='undefined'&&module.exports)module.exports=root.CCORNative;
if(typeof document==='undefined')return;
const config=JSON.parse(document.getElementById('config').textContent),$=id=>document.getElementById(id),img=$('image'),measurements=new Map();let index=0,timer=null,loaded=false;
$('heading').textContent=config.camera.toUpperCase()+' · '+config.candidate+' · '+config.frames.length+' original observations';
function stop(){if(timer)clearInterval(timer);timer=null;$('play').textContent='Play';}
function marked(){return [...measurements.values()];}
function result(){return makeReport(config,marked());}
function overlay(){const s=$('marks');s.replaceChildren();const p=measurements.get(config.frames[index].source_file);if(!p)return;const c=document.createElementNS('http://www.w3.org/2000/svg','circle');for(const [k,v]of Object.entries({cx:p.x+.5,cy:p.y+.5,r:9,fill:'none',stroke:'#ff88df','stroke-width':2}))c.setAttribute(k,v);s.append(c);}
function render(){const list=$('samples');list.replaceChildren();for(const p of marked().sort((a,b)=>a.time_utc.localeCompare(b.time_utc))){const li=document.createElement('li'),b=document.createElement('button');li.append(document.createTextNode(p.time_utc+' · X '+p.x+' · Y '+p.y+' '));b.textContent='Remove';b.onclick=()=>{measurements.delete(p.source_file);render()};li.append(b);list.append(li)}$('count').textContent=measurements.size+' frames marked';let r;try{r=result();$('report').value=reportText(r)}catch(e){$('report').value=e.message}for(const id of ['copy','download','json'])$(id).disabled=!r;overlay();}
function draw(){loaded=false;const f=config.frames[index];img.src=f[$('view').value];img.style.width=(2048*Number($('zoom').value)/100)+'px';$('zoomValue').textContent=$('zoom').value+'%';$('frameNumber').textContent=(index+1)+' / '+config.frames.length;$('frameInfo').textContent=f.time_utc+' · '+f.source_file;overlay();}
img.onload=()=>{loaded=img.naturalWidth===2048&&img.naturalHeight===1920;if(!loaded)$('status').textContent='Unexpected image dimensions; measurement disabled.'};
img.onerror=()=>{loaded=false;$('status').textContent='Frame could not be loaded; measurement disabled.'};
function step(n){index=(index+n+config.frames.length)%config.frames.length;draw()}
$('previous').onclick=()=>{stop();step(-1)};$('next').onclick=()=>{stop();step(1)};$('play').onclick=()=>{if(timer)stop();else{timer=setInterval(()=>step(1),700);$('play').textContent='Pause'}};
$('view').onchange=draw;$('zoom').oninput=()=>{img.style.width=(2048*Number($('zoom').value)/100)+'px';$('zoomValue').textContent=$('zoom').value+'%'};
img.onmousemove=e=>{if(!loaded)return;const p=pixelAt(e.clientX,e.clientY,img.getBoundingClientRect());if(p)$('position').textContent='Native X '+p.x+' · Y '+p.y+' | FITS one-based '+p.fits_pixel_1based.join(', ')};
img.onclick=e=>{if(!loaded)return;const p=pixelAt(e.clientX,e.clientY,img.getBoundingClientRect());if(!p)return;stop();const f=config.frames[index];measurements.set(f.source_file,{...p,source_file:f.source_file,source_sha256:f.source_sha256,time_utc:f.time_utc});render();$('status').textContent='Position recorded. Advance to the next frame and mark the same object.'};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText(reportText(result()));$('status').textContent='Report copied.'}catch(e){$('report').focus();$('report').select();$('status').textContent='Press Ctrl+C to copy the selected report.'}};
function download(json){const r=result(),url=URL.createObjectURL(new Blob([json?JSON.stringify(r,null,2):reportText(r)],{type:json?'application/json':'text/plain'})),a=document.createElement('a');a.href=url;a.download=String(config.candidate).replace(/[^A-Za-z0-9_-]/g,'_')+'-native-coordinates.'+(json?'json':'txt');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
$('download').onclick=()=>download(false);$('json').onclick=()=>download(true);
document.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName))return;if(e.key==='ArrowRight'){e.preventDefault();stop();step(1)}if(e.key==='ArrowLeft'){e.preventDefault();stop();step(-1)}});draw();render();
})(typeof globalThis!=='undefined'?globalThis:this);
</script></html>'''


if __name__ == '__main__':
    main()
