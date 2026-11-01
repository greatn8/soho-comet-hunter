#!/usr/bin/env python3
"""Build a bounded historical CCOR visual control from original NOAA FITS.

The live detector consumes decorated JPEGs. This native FITS rendering is a
separate input trial, not a claim of production recovery. No target positions
are supplied to rendering or tracking. Never writes live candidate state.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, io, json, re
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.request import urlopen, Request
from xml.etree import ElementTree as ET
import numpy as np
from PIL import Image

ARCHIVE='https://archive.data.noaa.gov/satellite-spaceweather/'
CONFIRMATION='https://sungrazer.nrl.navy.mil/confs-Feb2025-CCOR1'
CASE={
 'id':'CCOR-0001','camera':'ccor1','reference_status':'CONFIRMED_BY_SUNGRAZER',
 'confirmation_url':CONFIRMATION,
 'official_video':'https://sungrazer.nrl.navy.mil/sites/sungrazer/files/images/ccor001_comet.mp4',
 'from_utc':'2025-02-11T07:00:00Z','through_utc':'2025-02-11T11:01:00Z',
 'description':'Robert Pickard\u2019s first CCOR comet. The official animation marks the comet; the native sequence below has no assumed location.',
}
NAME=re.compile(r'CCOR1_1B_(20250211T\d{6})_V\d+_NC\.fits$')

def stamp(s): return dt.datetime.fromisoformat(s.replace('Z','+00:00'))

def fetch(url,cap):
 with urlopen(Request(url,headers={'User-Agent':'CCOR-historical-visual-control/1.0'}),timeout=45) as r:
  data=r.read(cap+1)
 if len(data)>cap:raise ValueError('Response exceeds bounded size')
 return data

def discover():
 prefix='SWFO/GOES-19/CCOR-1/ccor1-l1b/2025/02/11/'
 data=fetch(ARCHIVE+'?'+urlencode({'list-type':'2','prefix':prefix,'max-keys':200}),200000)
 root=ET.fromstring(data);ns={'s':'http://s3.amazonaws.com/doc/2006-03-01/'}
 if root.findtext('s:IsTruncated',namespaces=ns)=='true':raise ValueError('Truncated archive listing')
 selected=[]
 for item in root.findall('s:Contents',ns):
  key=item.findtext('s:Key',namespaces=ns);m=NAME.fullmatch(Path(key).name)
  if not key.startswith(prefix) or not m:continue
  when=dt.datetime.strptime(m[1],'%Y%m%dT%H%M%S').replace(tzinfo=dt.timezone.utc)
  if stamp(CASE['from_utc'])<=when<=stamp(CASE['through_utc']):
   selected.append({'name':Path(key).name,'url':ARCHIVE+quote(key,safe='/'),'bytes':int(item.findtext('s:Size',namespaces=ns))})
 if not 6<=len(selected)<=20:raise ValueError('Expected six to twenty original frames')
 return sorted(selected,key=lambda x:x['name'])

def load(path,source_url):
 from astropy.io import fits
 raw=path.read_bytes();m=NAME.fullmatch(path.name)
 if not m or not 2880<=len(raw)<=24000000:raise ValueError('Invalid science FITS')
 with fits.open(io.BytesIO(raw),memmap=False) as hs:
  h=next(h for h in hs if h.data is not None and h.data.ndim==2)
  if h.data.shape!=(1920,2048) or h.header.get('INSTRUME')!='CCOR1':raise ValueError('Unexpected native geometry or instrument')
  when=stamp(str(h.header['DATE-OBS'])+'Z')
  named=dt.datetime.strptime(m[1],'%Y%m%dT%H%M%S').replace(tzinfo=dt.timezone.utc)
  if abs((when-named).total_seconds())>2:raise ValueError('Observation timestamp disagreement')
  for part in hs:
   if 'CHECKSUM' in part.header and part.verify_checksum()!=1:raise ValueError('Checksum failure')
   if 'DATASUM' in part.header and part.verify_datasum()!=1:raise ValueError('Data checksum failure')
  data=np.asarray(h.data,dtype=np.float32).copy()
 return {'name':path.name,'time_utc':when.isoformat().replace('+00:00','Z'),
         'source_url':source_url,'source_sha256':hashlib.sha256(raw).hexdigest(),'data':data}

def render(arrays):
 """Sequence-wide stretches, no frame-wise normalization or spatial warping."""
 a=np.asarray(arrays,dtype=np.float32)
 if a.ndim!=3 or len(a)<6:raise ValueError('Need a temporal sequence')
 finite=np.isfinite(a); positive=a[finite & (a>0)]
 if positive.size<1000:raise ValueError('Insufficient science pixels')
 scale=float(np.median(positive));v=np.log1p(np.maximum(a,0)/scale)
 lo,hi=np.percentile(v[np.isfinite(v)],[1,99.5])
 if not hi>lo:raise ValueError('No original contrast range')
 original=np.uint8(np.nan_to_num(np.clip((v-lo)/(hi-lo),0,1))*255)
 # One fixed background for the entire sequence. Slow sources can be attenuated.
 with np.errstate(invalid='ignore'):
  baseline=np.nanmedian(a,axis=0);residual=a-baseline
 sample=np.abs(residual[np.isfinite(residual)&(residual!=0)])
 if not sample.size:raise ValueError('No temporal contrast')
 sigma=float(np.percentile(sample,70))
 if not sigma>0:raise ValueError('No temporal scale')
 temporal=np.uint8(np.nan_to_num(np.clip(128+28*np.arcsinh(residual/sigma),0,255)))
 temporal[~finite]=0
 return original,temporal,{'original_log_scale':scale,'original_low':float(lo),'original_high':float(hi),'temporal_scale':sigma,'background':'sequence_pixel_median','stretch':'128 + 28 * asinh(residual / temporal_scale)','same_stretch_all_frames':True}

def build(sources,output):
 output.mkdir(parents=True,exist_ok=True)
 sources=sorted(sources,key=lambda r:r['time_utc'])
 if len({r['time_utc'] for r in sources})!=len(sources):raise ValueError('Duplicate observation time')
 if not 6<=len(sources)<=20 or any(r['data'].shape!=(1920,2048) for r in sources):raise ValueError('Expected six to twenty native 2048 by 1920 arrays')
 original,temporal,settings=render([r['data'] for r in sources])
 frames=[]
 for i,r in enumerate(sources):
  row={k:v for k,v in r.items() if k!='data'}
  for label,series in [('original',original),('temporal',temporal)]:
   name=f'CCOR-0001-{i:02d}-{label}.png'
   # Native row flip only; 2x spatial reduction is explicitly recorded.
   img=Image.fromarray(np.flipud(series[i])).resize((1024,960),Image.Resampling.BOX)
   temporary=output/(name+'.part')
   img.save(temporary,format='PNG',optimize=True)
   with Image.open(temporary) as check:check.verify()
   temporary.replace(output/name);row[label]=name
  frames.append(row)
 result={**CASE,'mode':'HISTORICAL_NATIVE_FITS_VISUAL_CONTROL','frames':frames,
  'frame_count':len(frames),'elapsed_hours':(stamp(frames[-1]['time_utc'])-stamp(frames[0]['time_utc'])).total_seconds()/3600,
  'native_shape':[2048,1920],'preview_shape':[1024,960],
  'orientation':'FITS_AXIS2_UP_DISPLAY_FLIPUD_ONLY','rendering':settings,
  'live_detector_recovery':None,'recovery_status':'NOT_SCORED_NATIVE_FITS_IS_NOT_LIVE_JPEG_INPUT',
  'target_location_status':'UNREGISTERED_OFFICIAL_DEMONSTRATION_TO_NATIVE_ARRAY',
  'limitations':['The historical identity is confirmed by Sungrazer; no detector track has yet been independently associated with it.',
   'Native FITS previews have a different orientation and processing from decorated operational JPEGs.',
   'Temporal subtraction can attenuate slow objects and exaggerate noise, stars and changing coronal structures. Always check the original view.',
   'Playback is uniform per observation, not real time. Use the UTC timestamp and elapsed hours.'],
  'production_modified':False}
 (output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
 return result

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fits-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--download',action='store_true');a=p.parse_args()
 a.fits_dir.mkdir(parents=True,exist_ok=True);indexed=discover() if a.download else []
 urls={r['name']:r['url'] for r in indexed}
 for r in indexed:
  f=a.fits_dir/r['name']
  if not f.exists():f.write_bytes(fetch(r['url'],24000000))
  if f.stat().st_size!=r['bytes']:raise ValueError('Archive file size mismatch')
 sources=[]
 for f in sorted(a.fits_dir.glob('CCOR1_1B_20250211T*.fits')):
  m=NAME.fullmatch(f.name)
  if not m:continue
  when=dt.datetime.strptime(m[1],'%Y%m%dT%H%M%S').replace(tzinfo=dt.timezone.utc)
  if stamp(CASE['from_utc'])<=when<=stamp(CASE['through_utc']):
   url=urls.get(f.name,ARCHIVE+'SWFO/GOES-19/CCOR-1/ccor1-l1b/2025/02/11/'+f.name)
   sources.append(load(f,url))
 if not 6<=len(sources)<=20:raise ValueError('Expected six to twenty source frames')
 result=build(sources,a.output_dir)
 print(json.dumps({k:result[k] for k in ['id','frame_count','elapsed_hours','recovery_status']},indent=2))
if __name__=='__main__':main()
