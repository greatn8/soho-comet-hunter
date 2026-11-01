/* Historical reference imagery is independent of live candidates and alerts. */
(async function(){
 'use strict';
 const $=id=>document.getElementById(id);let manifest,index=0,timer=null,loaded=false,initialFocus=true;
 const controls=['first','previous','play','next','last','reference','practice','mode','timeline','zoom','speed'];
 const root='./data/ccor-reference/';
 function stop(){clearInterval(timer);timer=null;$('play').textContent='Play';}
 function draw(){
  const frame=manifest.frames[index],elapsed=(Date.parse(frame.time_utc)-Date.parse(manifest.frames[0].time_utc))/3600000;
  loaded=false;$('status').textContent='Loading observation…';$('frame').style.visibility='hidden';
  $('frame').src=root+frame[$('mode').value];$('frame').style.width=(manifest.preview_shape[0]*Number($('zoom').value)/100)+'px';
  $('frameNumber').textContent=(index+1)+' / '+manifest.frames.length;
  $('frameInfo').textContent=window.SydneyTime.format(frame.time_utc)+' · '+elapsed.toFixed(2)+' hours after first observation';
  $('timeline').value=index;$('source').href=frame.source_url;$('source').target='_blank';$('source').rel='noopener';
  $('provenance').textContent=frame.name+' · original FITS grid 2048 × 1920';$('hash').textContent='SHA-256: '+frame.source_sha256;
 }
 function go(i){stop();index=Math.max(0,Math.min(manifest.frames.length-1,i));draw();}
 function focusPractice(){const z=Number($('zoom').value)/100,v=$('viewport');v.scrollLeft=Math.max(0,180*z-v.clientWidth/2);v.scrollTop=Math.max(0,550*z-v.clientHeight/2);}
 $('reference').onclick=()=>go(7);$('practice').onclick=()=>{$('zoom').value='200';$('frame').style.width='2048px';focusPractice();};
 $('frame').onload=()=>{loaded=$('frame').naturalWidth===manifest.preview_shape[0]&&$('frame').naturalHeight===manifest.preview_shape[1];$('frame').style.visibility=loaded?'visible':'hidden';$('status').textContent=loaded?'':'Unexpected image dimensions; this observation is unavailable.';if(loaded&&initialFocus){focusPractice();initialFocus=false;}};
 $('frame').onerror=()=>{loaded=false;$('frame').style.visibility='hidden';$('status').textContent='Observation unavailable. The previous frame is hidden.';};
 $('first').onclick=()=>go(0);$('last').onclick=()=>go(manifest.frames.length-1);$('previous').onclick=()=>go(index-1);$('next').onclick=()=>go(index+1);
 $('timeline').oninput=()=>go(Number($('timeline').value));$('mode').onchange=()=>{stop();draw();};
 $('zoom').onchange=()=>{$('frame').style.width=(manifest.preview_shape[0]*Number($('zoom').value)/100)+'px';};
 $('play').onclick=()=>{if(timer){stop();return;}timer=setInterval(()=>{index=(index+1)%manifest.frames.length;draw();},Number($('speed').value));$('play').textContent='Pause';};
 $('speed').onchange=stop;document.addEventListener('keydown',e=>{if(['INPUT','SELECT','BUTTON'].includes(e.target.tagName)||!manifest)return;if(e.key==='ArrowRight'){e.preventDefault();go(index+1);}if(e.key==='ArrowLeft'){e.preventDefault();go(index-1);}});
 try{
  const response=await fetch(root+'manifest.json',{cache:'no-store'});if(!response.ok)throw Error('Reference manifest unavailable');manifest=await response.json();
  if(manifest.id!=='CCOR-0001'||manifest.frame_count!==manifest.frames.length||manifest.frames.length<6||manifest.production_modified!==false||manifest.live_detector_recovery!==null)throw Error('Invalid historical reference');
  if(manifest.frames.some(f=>!/^CCOR-0001-\d{2}-original\.png$/.test(f.original)||!/^CCOR-0001-\d{2}-temporal\.png$/.test(f.temporal)||!f.source_url.startsWith('https://archive.data.noaa.gov/satellite-spaceweather/')))throw Error('Invalid source file');
  $('summary').textContent=manifest.frames.length+' original observations · '+manifest.elapsed_hours.toFixed(2)+' hours · '+window.SydneyTime.format(manifest.frames[0].time_utc)+' · confirmed by Sungrazer';
  index=Math.min(7,manifest.frames.length-1);$('reference').textContent='Example time · '+window.SydneyTime.format(manifest.frames[index].time_utc);$('timeline').max=manifest.frames.length-1;controls.forEach(id=>$(id).disabled=false);draw();
 }catch(e){stop();controls.forEach(id=>$(id).disabled=true);$('summary').textContent='Reference sequence unavailable.';$('error').textContent=e.message;}
})();
