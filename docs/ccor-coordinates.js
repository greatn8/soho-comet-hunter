/* Source JPEG pixel positions. No JPEG-to-FITS transformation. */
(function(root){
  'use strict';
  function contentRect(rect,width,height){
    if(![rect.left,rect.top,rect.width,rect.height,width,height].every(Number.isFinite)||rect.width<=0||rect.height<=0||width<=0||height<=0)return null;
    const scale=Math.min(rect.width/width,rect.height/height),w=width*scale,h=height*scale;
    return {left:rect.left+(rect.width-w)/2,top:rect.top+(rect.height-h)/2,width:w,height:h};
  }
  function pixelAt(cx,cy,rect,width,height){
    const box=contentRect(rect,width,height);
    if(!box||!Number.isFinite(cx)||!Number.isFinite(cy))return null;
    const u=(cx-box.left)/box.width,v=(cy-box.top)/box.height;
    if(u<0||u>=1||v<0||v>=1)return null;
    return {x:Math.floor(u*width),y:Math.floor(v*height)};
  }
  function sourceMetadata(camera,time,url,width,height){
    if(!['ccor1','ccor2'].includes(camera)||!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1)throw Error('Invalid source dimensions or camera');
    const source=new URL(url),m=source.pathname.match(/^\/products\/(ccor[12])\/jpegs\/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})_(ccor[12])_[A-Za-z0-9_-]+\.jpg$/i);
    if(source.protocol!=='https:'||source.hostname!=='services.swpc.noaa.gov'||source.port||source.username||source.password||!m||m[1]!==camera||m[7]!==camera)throw Error('Source URL does not match this NOAA camera');
    const iso=m[2]+'-'+m[3]+'-'+m[4]+'T'+m[5]+':'+m[6]+':00Z';
    if(!Number.isFinite(Date.parse(time))||Date.parse(time)!==Date.parse(iso)||new Date(iso).toISOString().slice(0,16)!==iso.slice(0,16))throw Error('Frame time does not match the source filename');
    return {camera,time_utc:iso,time_precision:'minute',timestamp_source:'NOAA JPEG filename (not native FITS DATE-OBS)',source_url:source.href,source_file:source.pathname.split('/').pop(),image_width:width,image_height:height};
  }
  function measurement(source,x,y){
    const checked=sourceMetadata(source.camera,source.time_utc,source.source_url,source.image_width,source.image_height);
    if(!Number.isInteger(x)||!Number.isInteger(y)||x<0||y<0||x>=checked.image_width||y>=checked.image_height)throw Error('Position outside source image');
    return {...checked,x,y,coordinate_origin:'UPPER_LEFT_ZERO_BASED',x_direction:'RIGHT',y_direction:'DOWN',measurement:'MANUAL_SOURCE_IMAGE_CLICK'};
  }
  function report(candidate,rows){
    if(!candidate||!Array.isArray(rows)||rows.length<3)throw Error('Mark the same object in at least three distinct frames first');
    const observations=rows.map(r=>measurement(r,r.x,r.y)).sort((a,b)=>a.time_utc.localeCompare(b.time_utc));
    if(new Set(observations.map(r=>r.camera)).size!==1||new Set(observations.map(r=>r.time_utc)).size!==rows.length)throw Error('Use distinct frames from one camera');
    if(new Set(observations.map(r=>r.image_width+'x'+r.image_height)).size!==1)throw Error('Source sizes differ; use a consistent sequence');
    return {candidate_id:candidate,camera:observations[0].camera.toUpperCase(),coordinate_system:'SOURCE_JPEG_IMAGE_PIXELS',coordinate_origin:'UPPER_LEFT_ZERO_BASED',image_size:[observations[0].image_width,observations[0].image_height],native_fits_conversion_applied:false,confirmed_comet:false,observations};
  }
  function reportText(result){
    const lines=['Camera: '+result.camera,'Image size: '+result.image_size.join(' x ')+' pixels — original NOAA JPEG','Origin: Upper Left (0,0); X increases right; Y increases down.','Times: UTC from JPEG filenames, precision one minute.','Coordinates apply to the source JPEGs below, NOT native FITS images.','Object identity unverified. Candidate: '+result.candidate_id,'Use the actual image size in the form. If unavailable, include this size and source URLs in Note to Admin; do not label these as 2048 x 1920 FITS coordinates.',''];
    let date='';
    for(const r of result.observations){
      if(r.time_utc.slice(0,10)!==date){date=r.time_utc.slice(0,10);lines.push('Date (UTC): '+date,'HHMM X Y');}
      lines.push(r.time_utc.slice(11,16).replace(':','')+' '+r.x+' '+r.y);
    }
    lines.push('','Source images:');
    for(const r of result.observations)lines.push(r.time_utc+' '+r.source_url);
    return lines.join('\n');
  }
  const api={contentRect,pixelAt,sourceMetadata,measurement,report,reportText};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.CCORCoordinates=api;
})(typeof globalThis!=='undefined'?globalThis:this);
