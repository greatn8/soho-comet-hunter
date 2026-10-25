const test=require('node:test'),assert=require('node:assert/strict');
const c=require('../docs/ccor-coordinates.js');
const url=t=>'https://services.swpc.noaa.gov/products/ccor1/jpegs/20261025_'+t+'_ccor1_1024by960.jpg';
const source=(t='1315')=>c.sourceMetadata('ccor1','2026-10-25T'+t.slice(0,2)+':'+t.slice(2)+':00Z',url(t),1204,1205);
test('actual dimensions override filename; CSS scaling preserves pixels',()=>{
 assert.deepEqual(c.pixelAt(310.25,370.25,{left:10,top:20,width:602,height:602.5},1204,1205),{x:600,y:700});
 assert.deepEqual(c.pixelAt(610.5,720.5,{left:10,top:20,width:1204,height:1205},1204,1205),{x:600,y:700});
 assert.equal(source().image_width,1204);
});
test('contain margins and outside edges cannot be recorded',()=>{
 const r={left:0,top:0,width:1000,height:500};
 assert.equal(c.pixelAt(100,250,r,1000,1000),null);
 assert.deepEqual(c.pixelAt(250,0,r,1000,1000),{x:0,y:0});
 assert.deepEqual(c.pixelAt(749.9,499.9,r,1000,1000),{x:999,y:999});
 assert.equal(c.pixelAt(750,500,r,1000,1000),null);
});
test('camera and source timestamp must agree; no unloaded image',()=>{
 assert.throws(()=>c.sourceMetadata('ccor2','2026-10-25T13:15:00Z',url('1315'),1204,1205));
 assert.throws(()=>c.sourceMetadata('ccor1','2026-10-25T13:30:00Z',url('1315'),1204,1205));
 assert.throws(()=>c.sourceMetadata('ccor1','2026-10-25T13:15:00Z',url('1315'),0,0));
});
test('manual report includes origin and sources, never native estimates',()=>{
 const rows=['1315','1330','1345'].map((t,i)=>c.measurement(source(t),600-i*5,700+i));
 const r=c.report('CCOR1-test',rows),text=c.reportText(r);
 assert.deepEqual(r.image_size,[1204,1205]);assert.equal(r.native_fits_conversion_applied,false);
 assert.match(text,/1315 600 700/);assert.match(text,/Upper Left/);assert.match(text,/20261025_1315/);
 assert.throws(()=>c.report('test',[rows[0],rows[0],rows[1]]));assert.throws(()=>c.report('test',rows.slice(0,2)));
 assert.throws(()=>c.measurement(source(),1204,700));assert.throws(()=>c.measurement(source(),1.5,700));
});
test('dates are retained across midnight',()=>{
 const rows=[['20261025','2345'],['20261026','0000'],['20261026','0015']].map(([date,t])=>{
 const u='https://services.swpc.noaa.gov/products/ccor1/jpegs/'+date+'_'+t+'_ccor1_1024by960.jpg';
 const iso=date.slice(0,4)+'-'+date.slice(4,6)+'-'+date.slice(6)+'T'+t.slice(0,2)+':'+t.slice(2)+':00Z';
 return c.measurement(c.sourceMetadata('ccor1',iso,u,1204,1205),500,700)});
 const text=c.reportText(c.report('midnight',rows));assert.match(text,/2026-10-25/);assert.match(text,/2026-10-26/);
});
