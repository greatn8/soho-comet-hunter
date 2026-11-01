/* Presentation only. Source timestamps, matching, sorting and exports stay UTC. */
(function(root){
 'use strict';
 const zone='Australia/Sydney';
 const formatter=new Intl.DateTimeFormat('en-AU',{timeZone:zone,year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',hour12:true,timeZoneName:'short'});
 function format(value){
  if(value===null||value===undefined||value==='')return '—';
  const date=new Date(value);if(!Number.isFinite(date.getTime()))return String(value);
  return formatter.format(date).replace(/[\u00a0\u202f]/g,' ');
 }
 function frame(value){
  const m=String(value||'').match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(?:_c3_(?:512|1024)\.jpg)?$/i);
  if(!m)return '—';
  const iso=`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:00Z`,date=new Date(iso);
  if(!Number.isFinite(date.getTime())||date.toISOString().slice(0,16)!==iso.slice(0,16))return '—';
  return format(iso);
 }
 function utc(value){
  if(value===null||value===undefined||value==='')return '—';
  const date=new Date(value);return Number.isFinite(date.getTime())?date.toISOString():String(value);
 }
 const api={format,frame,utc,timeZone:zone};root.SydneyTime=api;
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
