/* Manual dashboard measurements on the loaded source image's intrinsic grid. */
(function(){
  'use strict';
  const C=window.CCORCoordinates, $=id=>document.getElementById(id);
  let current=null, candidate=null, enabled=false, zoom=null;
  const records=new Map();
  const storageKey='ccor-source-image-measurements-v1';
  try{
    const saved=JSON.parse(localStorage.getItem(storageKey)||'[]');
    if(Array.isArray(saved))for(const group of saved.slice(-100)){
      if(!group||typeof group.id!=='string'||!Array.isArray(group.rows))continue;
      const rows=new Map();
      for(const row of group.rows.slice(0,100))try{const r=C.measurement(row,row.x,row.y);rows.set(r.time_utc,r);}catch(e){}
      records.set(group.id,rows);
    }
  }catch(e){}
  function rows(){return [...(records.get(candidate)||new Map()).values()].sort((a,b)=>a.time_utc.localeCompare(b.time_utc));}
  function persist(){
    try{localStorage.setItem(storageKey,JSON.stringify([...records].slice(-100).map(([id,map])=>({id,rows:[...map.values()]}))));}
    catch(e){$('ccorReportStatus').textContent='Position recorded for this session; browser storage is unavailable.';}
  }
  function alignMarker(){
    const svg=$('ccorReportOverlay');svg.replaceChildren();
    if(!current)return;
    const image=$('ccorReviewImage'), box=C.contentRect(image.getBoundingClientRect(),current.image_width,current.image_height),stage=image.parentElement.getBoundingClientRect();
    if(!box)return;
    Object.assign(svg.style,{left:(box.left-stage.left)+'px',top:(box.top-stage.top)+'px',width:box.width+'px',height:box.height+'px',right:'auto',bottom:'auto'});
    svg.setAttribute('viewBox','0 0 '+current.image_width+' '+current.image_height);
    const r=records.get(candidate)?.get(current.time_utc);
    if(!r||r.source_url!==current.source_url)return;
    const circle=document.createElementNS('http://www.w3.org/2000/svg','circle');
    for(const [k,v]of Object.entries({cx:r.x+.5,cy:r.y+.5,r:8,fill:'none',stroke:'#ff82f3','stroke-width':2}))circle.setAttribute(k,v);
    svg.appendChild(circle);
  }
  function render(){
    const marked=rows(), table=$('ccorReportPositions');table.replaceChildren();
    for(const r of marked){
      const li=document.createElement('li'), remove=document.createElement('button');
      li.append(document.createTextNode(r.time_utc+' · X '+r.x+' · Y '+r.y+' '));
      remove.type='button';remove.textContent='Remove';remove.onclick=()=>{records.get(candidate)?.delete(r.time_utc);persist();render();};li.appendChild(remove);table.appendChild(li);
    }
    $('ccorReportCount').textContent=marked.length+' frame'+(marked.length===1?'':'s')+' marked';
    let result=null;
    try{result=C.report(candidate,marked);$('ccorReportText').value=C.reportText(result);}
    catch(e){$('ccorReportText').value=marked.length?e.message:'Click the same object in at least three frames to prepare coordinates.';}
    for(const id of ['ccorReportCopy','ccorReportDownload','ccorReportJSON'])$(id).disabled=!result;
    alignMarker();return result;
  }
  function mark(point){
    if(!enabled||!current||!point)return;
    document.dispatchEvent(new Event('ccor-report-measure'));
    const r=C.measurement(current,point.x,point.y);
    if(!records.has(candidate))records.set(candidate,new Map());
    records.get(candidate).set(r.time_utc,r);render();
    $('ccorReportStatus').textContent='Recorded '+r.time_utc+': X '+r.x+', Y '+r.y+'. Move to the next frame and mark the same object.';persist();
  }
  function fromImage(event){
    if(!current)return null;
    return C.pixelAt(event.clientX,event.clientY,$('ccorReviewImage').getBoundingClientRect(),current.image_width,current.image_height);
  }
  function cursor(point){
    if(current&&point)$('ccorReportCursor').textContent='Source image: X '+point.x+' · Y '+point.y+' · '+current.image_width+' × '+current.image_height+' · Upper Left (0,0)';
  }
  function pending(track){
    candidate=track?.id||null;current=null;zoom=null;
    $('ccorReportCursor').textContent='Loading source image — measurement unavailable until loaded.';render();
  }
  function ready(track,entry,image){
    if(candidate!==track.id)return;
    try{
      current=C.sourceMetadata(track.camera,entry.time,entry.image_url,image.naturalWidth,image.naturalHeight);
      $('ccorReportCursor').textContent=current.image_width+' × '+current.image_height+' source pixels · Upper Left (0,0). Move over the image to read coordinates.';
    }catch(e){current=null;$('ccorReportCursor').textContent='Measurement unavailable: '+e.message;}
    render();
  }
  function setZoom(canvas,sourceRect){
    zoom={canvas,...sourceRect};
    function point(e){
      if(!current||!zoom||zoom.canvas!==canvas)return null;
      const r=canvas.getBoundingClientRect(),style=getComputedStyle(canvas);
      const left=parseFloat(style.borderLeftWidth)||0,right=parseFloat(style.borderRightWidth)||0,top=parseFloat(style.borderTopWidth)||0,bottom=parseFloat(style.borderBottomWidth)||0;
      const u=(e.clientX-r.left-left)/(r.width-left-right),v=(e.clientY-r.top-top)/(r.height-top-bottom);
      if(u<0||u>=1||v<0||v>=1)return null;
      const x=Math.floor(sourceRect.x+u*sourceRect.width),y=Math.floor(sourceRect.y+v*sourceRect.height);
      if(x<0||y<0||x>=current.image_width||y>=current.image_height)return null;
      return {x,y};
    }
    canvas.addEventListener('mousemove',e=>cursor(point(e)));
    canvas.addEventListener('click',e=>mark(point(e)));
  }
  function download(json){
    const r=render();if(!r)return;
    const blob=new Blob([json?JSON.stringify(r,null,2):C.reportText(r)],{type:json?'application/json':'text/plain'}),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download=String(candidate).replace(/[^A-Za-z0-9_-]/g,'_')+'-source-coordinates.'+(json?'json':'txt');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  $('ccorReportMark').onclick=()=>{
    enabled=!enabled;$('ccorReportMark').textContent='Mark positions: '+(enabled?'ON':'off');$('ccorReportMark').setAttribute('aria-pressed',String(enabled));
    $('ccorReviewImage').classList.toggle('ccor-measure-mode',enabled);
    if(enabled){document.dispatchEvent(new Event('ccor-report-measure'));$('ccorReportStatus').textContent='Click the object in the full image or detection zoom, then advance a frame.';}
  };
  $('ccorReviewImage').addEventListener('mousemove',e=>cursor(fromImage(e)));
  $('ccorReviewImage').addEventListener('click',e=>mark(fromImage(e)));
  $('ccorReportCopy').onclick=async()=>{
    const result=render();if(!result)return;
    try{await navigator.clipboard.writeText(C.reportText(result));$('ccorReportStatus').textContent='Report coordinates copied.';}
    catch(e){$('ccorReportText').focus();$('ccorReportText').select();$('ccorReportStatus').textContent='Press Ctrl+C to copy the selected report.';}
  };
  $('ccorReportDownload').onclick=()=>download(false);$('ccorReportJSON').onclick=()=>download(true);
  window.addEventListener('resize',alignMarker);
  window.CCORReportUI={pending,ready,setZoom,unavailable:()=>{current=null;zoom=null;$('ccorReportCursor').textContent='Source image unavailable — cannot record a position on this frame.';render();}};
})();
