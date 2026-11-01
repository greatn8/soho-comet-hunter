/* Display geometry and track context. No star classification or detection veto. */
(function(root){
  'use strict';
  const finite=v=>v!==null&&v!==undefined&&Number.isFinite(Number(v));
  function viewport(track,mode='fixed',point=null){
    const [w,h]=(track.processing_shape||[512,512]).map(Number);
    if(!(w>0&&h>0))return null;
    const points=(track.points||[]).filter(p=>finite(p.x)&&finite(p.y));
    if(!points.length)return null;
    const xs=points.map(p=>+p.x),ys=points.map(p=>+p.y);
    let crop,cx,cy;
    if(mode==='follow'&&point&&finite(point.x)&&finite(point.y)){
      crop=Math.min(72,w,h);cx=+point.x;cy=+point.y;
    }else{
      const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
      crop=Math.max(112,x1-x0+48,y1-y0+48);
      cx=(x0+x1)/2;cy=(y0+y1)/2;
    }
    const width=Math.min(crop,w),height=Math.min(crop,h);
    return {x:Math.max(0,Math.min(w-width,cx-width/2)),
            y:Math.max(0,Math.min(h-height,cy-height/2)),width,height};
  }
  function displayFilter(brightness,contrast){
    const b=finite(brightness)?Math.max(.5,Math.min(8,Number(brightness))):1;
    const c=finite(contrast)?Math.max(.5,Math.min(3,Number(contrast))):1;
    return 'contrast('+c+') brightness('+b+')';
  }
  function sharedDrift(track,tracks){
    // This is a comparison of fitted tracks, NOT independently measured stars.
    // Require time overlap, spatial separation and the same processing grid.
    if(![track.vx_px_h,track.vy_px_h,track.x_first,track.y_first].every(finite))return [];
    const start=Date.parse(track.first),end=Date.parse(track.last);
    if(!Number.isFinite(start)||!Number.isFinite(end))return [];
    const peers=[];
    for(const r of tracks||[]){
      if(r.id===track.id||r.camera!==track.camera||Number(r.frames)<5)continue;
      if(![r.vx_px_h,r.vy_px_h,r.x_first,r.y_first].every(finite))continue;
      if(JSON.stringify(r.processing_shape||[512,512])!==JSON.stringify(track.processing_shape||[512,512]))continue;
      const overlap=Math.min(end,Date.parse(r.last))-Math.max(start,Date.parse(r.first));
      if(!(overlap>=3600000))continue;
      if(Math.hypot(+r.x_first- +track.x_first,+r.y_first- +track.y_first)<24)continue;
      if(Math.hypot(+r.vx_px_h- +track.vx_px_h,+r.vy_px_h- +track.vy_px_h)>.5)continue;
      if(peers.some(p=>Math.hypot(+p.x_first- +r.x_first,+p.y_first- +r.y_first)<24))continue;
      peers.push(r);
    }
    return peers;
  }
  function context(track,tracks){
    const peers=sharedDrift(track,tracks);
    const summary={peer_ids:peers.map(r=>r.id),peer_count:peers.length,
      independent_star_comparison:'UNMEASURED',identity:'UNIDENTIFIED',
      peer_note:peers.length>=3
        ? 'Similar fitted drift to '+peers.length+' other displayed tracks. This is a background-motion clue, not a star identification.'
        : 'No clear shared-drift group in the displayed tracks. This does not establish independent motion.',
      source_displacement_px:null,hours:null};
    const [w,h]=track.processing_shape||[],[sw,sh]=track.source_jpeg_shape||[];
    if([w,h,sw,sh,track.x_first,track.y_first,track.x_last,track.y_last].every(finite)&&w>0&&h>0&&sw>0&&sh>0){
      summary.source_displacement_px=Math.hypot((track.x_last-track.x_first)*sw/w,(track.y_last-track.y_first)*sh/h);
    }
    const hours=(Date.parse(track.last)-Date.parse(track.first))/3600000;
    if(Number.isFinite(hours)&&hours>=0)summary.hours=hours;
    return summary;
  }
  const api={viewport,displayFilter,sharedDrift,context};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.CCORReviewTools=api;
})(typeof globalThis!=='undefined'?globalThis:this);
