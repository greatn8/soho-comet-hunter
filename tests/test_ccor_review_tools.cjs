const test=require('node:test'),assert=require('node:assert/strict');
const t=require('../docs/ccor-review-tools.js');
function track(id='target',x=180,y=73){return {id,camera:'ccor2',first:'2026-11-01T04:30:00Z',last:'2026-11-01T08:15:00Z',frames:15,
 x_first:x,y_first:y,x_last:x+5,y_last:y,vx_px_h:1.34,vy_px_h:0,processing_shape:[512,512],source_jpeg_shape:[1204,1205],
 points:[{x,y},{x:x+5,y}]};}
test('fixed field is stable through the sequence while follow mode recenters',()=>{
 const r=track(),a=t.viewport(r,'fixed',r.points[0]),b=t.viewport(r,'fixed',r.points[1]);
 assert.deepEqual(a,b);assert.equal(a.width,112);
 assert.notDeepEqual(t.viewport(r,'follow',r.points[0]),t.viewport(r,'follow',r.points[1]));
 for(const p of r.points)assert.ok(p.x>=a.x&&p.x<a.x+a.width&&p.y>=a.y&&p.y<a.y+a.height);
});
test('fixed rectangle stays in bounds and preserves a wide track on non-square frames',()=>{
 const r=track();r.processing_shape=[512,480];r.points=[{x:1,y:1},{x:510,y:478}];
 const v=t.viewport(r);assert.deepEqual(v,{x:0,y:0,width:512,height:480});
 assert.equal(t.viewport({...r,points:[]}),null);
});
test('shared fitted drift is context only and never identifies a star or removes a track',()=>{
 const r=track(),peers=[track('a',50,200),track('b',310,200),track('c',400,300)];
 const data=JSON.parse(JSON.stringify([r,...peers]));
 const c=t.context(r,data);assert.equal(c.peer_count,3);assert.equal(c.identity,'UNIDENTIFIED');
 assert.equal(c.independent_star_comparison,'UNMEASURED');assert.match(c.peer_note,/not a star identification/);
 assert.deepEqual(data,[r,...peers]);
});
test('camera changes, non-overlap, duplicate locations and unlike vectors cannot inflate drift context',()=>{
 const r=track(),a=track('a',50,200),duplicate=track('duplicate',51,201);
 const otherCamera={...track('camera',310,200),camera:'ccor1'};
 const old={...track('old',400,300),last:'2026-10-31T20:00:00Z'};
 const fast={...track('fast',400,300),vx_px_h:10};
 assert.equal(t.sharedDrift(r,[r,a,duplicate,otherCamera,old,fast]).length,1);
 assert.equal(t.context(r,[r]).independent_star_comparison,'UNMEASURED');
 assert.match(t.context(r,[r]).peer_note,/does not establish independent motion/);
});
test('movement is expressed in source JPEG pixels without inferring FITS coordinates',()=>{
 const c=t.context(track(),[]);assert.ok(Math.abs(c.source_displacement_px-11.7578125)<1e-8);
 assert.equal(c.hours,3.75);assert.equal(c.native_coordinates,undefined);
});
test('display filters are bounded intensity-only adjustments',()=>{
 assert.equal(t.displayFilter(2,1.5),'contrast(1.5) brightness(2)');
 assert.equal(t.displayFilter(100,100),'contrast(3) brightness(8)');
 assert.equal(t.displayFilter(undefined,NaN),'contrast(1) brightness(1)');
});
