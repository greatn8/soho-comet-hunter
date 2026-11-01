const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../docs');
let server,browser;
(async()=>{
 server=http.createServer((req,res)=>{
  const u=new URL(req.url,'http://localhost'),file=path.join(root,u.pathname==='/'?'index.html':u.pathname);
  if(!file.startsWith(root+path.sep)){res.writeHead(403).end();return;}
  fs.readFile(file,(e,b)=>{if(e){res.writeHead(404).end();return;}
   res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.json')?'application/json':'text/html');res.end(b);});
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1300,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const times=['0415','0430','0445','0500','0515','0530','0545','0600'];
 const entries=times.map(t=>({time:'2026-11-01T'+t.slice(0,2)+':'+t.slice(2)+':00Z',image_url:'https://services.swpc.noaa.gov/products/ccor2/jpegs/20261101_'+t+'_ccor2_1024by960.jpg'}));
 const contextual=[{time:'2026-11-01T03:00:00Z',image_url:'https://services.swpc.noaa.gov/products/ccor2/jpegs/20261101_0300_ccor2_1024by960.jpg'},...entries,{time:'2026-11-01T07:15:00Z',image_url:'https://services.swpc.noaa.gov/products/ccor2/jpegs/20261101_0715_ccor2_1024by960.jpg'}];
 const observed=[1,3,4,5,6].map((i,j)=>({...entries[i],x:180+j,y:73}));
 const track={id:'CCOR2-fixed-test',camera:'ccor2',first:observed[0].time,last:observed.at(-1).time,
  frames:5,points:observed,processing_shape:[512,512],source_jpeg_shape:[1204,1205],
  x_first:180,y_first:73,x_last:184,y_last:73,vx_px_h:1.34,vy_px_h:0,
  review_validation:{status:'PASS'},review_stage:'EARLY'};
 const peers=[[50,200],[310,200],[400,300]].map(([x,y],i)=>({...track,id:'CCOR2-peer-'+i,x_first:x,x_last:x+4,y_first:y,y_last:y}));
 const tracks=[track,...peers];
 await page.route('https://services.swpc.noaa.gov/**',route=>{
  if(route.request().url().includes('_0600_'))return route.abort();
  const i=times.findIndex(t=>route.request().url().includes('_'+t+'_'));
  // No CORS headers: the display must work without reading remote canvas pixels.
  return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1204" height="1205"><rect width="1204" height="1205" fill="#010307"/><circle cx="'+((180+i*.6)*1204/512)+'" cy="172" r="2" fill="#263c53"/><circle cx="390" cy="190" r="2" fill="#1c2d40"/></svg>'});
 });
 await page.route('**/data/*.json?*',route=>{
  const name=new URL(route.request().url()).pathname.split('/').pop();
  const data=name==='ccor.json'?{tracks,review_shortlist_ids:tracks.map(r=>r.id),frame_manifests:{ccor2:contextual},generated_at:'2026-11-01T06:00:00Z'}:{candidates:[],available:false};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://127.0.0.1:'+server.address().port+'/');
 await page.waitForSelector('[data-ccor-id]');
 assert.equal(await page.locator('[data-ccor-id]').count(),4);
 await page.locator('[data-ccor-id="'+track.id+'"]').click();
 const ready=()=>page.waitForFunction(()=>document.getElementById('ccorReportCursor').textContent.includes('1204')&&document.querySelector('#ccorReviewZoom canvas')&&document.querySelector('#ccorReferenceZoom canvas'));
 await ready();
 assert.equal(await page.locator('#ccorContextWindow').inputValue(),'90');
 assert.match(await page.locator('#ccorFramePosition').innerText(),/1 \/ 10/);
 assert.match(await page.locator('#ccorReviewFrameEvidence').innerText(),/NO MEASURED/);
 assert.equal(await page.locator('#ccorReviewOverlay circle[stroke="#ffcc62"]').count(),0);
 await page.locator('#ccorContextWindow').selectOption('15');await ready();
 assert.match(await page.locator('#ccorStatus').innerText(),/1 Nov 2026, 5:00 pm AEDT/);
 assert.match(await page.locator('#ccorReviewCaption').innerText(),/1 Nov 2026, 3:15 pm AEDT/);
 assert.doesNotMatch(await page.locator('#ccorReviewCaption').innerText(),/T04:15:00Z/);
 assert.equal(await page.locator('#ccorZoomMode').inputValue(),'fixed');
 assert.match(await page.locator('#ccorFramePosition').innerText(),/1 \/ 8/);
 assert.match(await page.locator('#ccorReviewFrameEvidence').innerText(),/NO MEASURED/);
 assert.equal(await page.locator('#ccorReviewOverlay circle[stroke="#ffcc62"]').count(),0);
 const fixed=await page.locator('#ccorReviewZoom canvas').getAttribute('data-source-rect');
 assert.equal(await page.locator('#ccorReferenceZoom canvas').getAttribute('data-source-rect'),fixed);
 assert.match(await page.locator('#ccorMotionContext').innerText(),/3 other displayed tracks/);
 assert.match(await page.locator('#ccorMotionContext').innerText(),/not been independently measured/);
 await page.locator('#ccorBrightness').fill('3');await page.locator('#ccorBrightness').dispatchEvent('input');
 await page.locator('#ccorContrast').fill('1.5');await page.locator('#ccorContrast').dispatchEvent('input');
 await ready();assert.match(await page.locator('#ccorReviewImage').getAttribute('style'),/brightness\(3\)/);
 assert.equal(await page.locator('#ccorReviewZoom canvas').getAttribute('data-source-rect'),fixed);
 await page.locator('#ccorReviewNext').click();await ready();
 assert.match(await page.locator('#ccorReviewFrameEvidence').innerText(),/^MEASURED PEAK/);
 assert.equal(await page.locator('#ccorReviewZoom canvas').getAttribute('data-source-rect'),fixed);
 await page.locator('#ccorShowMarkers').uncheck();
 assert.equal(await page.locator('#ccorReviewOverlay').evaluate(e=>e.style.visibility),'hidden');
 await page.locator('#ccorReportMark').click();
 const reference=page.locator('#ccorReferenceZoom canvas');await reference.scrollIntoViewIfNeeded();await reference.click({position:{x:100,y:100}});
 assert.equal(await page.locator('#ccorReportCount').innerText(),'0 frames marked');
 const current=page.locator('#ccorReviewZoom canvas');await current.scrollIntoViewIfNeeded();await current.click({position:{x:144,y:144}});
 assert.equal(await page.locator('#ccorReportCount').innerText(),'1 frame marked');
 const saved=JSON.parse(await page.evaluate(()=>localStorage.getItem('ccor-source-image-measurements-v1')))[0].rows[0];
 assert.equal(saved.time_utc,observed[0].time);
 assert.match(await page.locator('#ccorReportPositions').innerText(),/1 Nov 2026, 3:30 pm AEDT/);
 await page.locator('#ccorZoomMode').selectOption('follow');await ready();
 assert.notEqual(await current.getAttribute('data-source-rect'),fixed);
 await page.locator('#ccorZoomMode').selectOption('fixed');await ready();
 assert.equal(await current.getAttribute('data-source-rect'),fixed);
 await page.locator('#ccorReviewNext').click();await ready();
 assert.match(await page.locator('#ccorReviewFrameEvidence').innerText(),/NO MEASURED/);
 assert.equal(await page.locator('#ccorReviewOverlay circle[stroke="#ffcc62"]').count(),0);
 assert.equal(await current.getAttribute('data-source-rect'),fixed);
 await page.locator('#ccorFrameMode').click();await ready();
 assert.match(await page.locator('#ccorFramePosition').innerText(),/1 \/ 5/);
 await page.locator('#ccorFrameMode').click();await ready();
 assert.match(await page.locator('#ccorFramePosition').innerText(),/1 \/ 8/);
 if(process.env.CCOR_REVIEW_SCREENSHOT){await page.locator('#ccorReviewHeading').scrollIntoViewIfNeeded();await page.screenshot({path:process.env.CCOR_REVIEW_SCREENSHOT});}
 // Unavailable current frame clears the previous image's usable measurement state.
 await page.locator('#ccorReviewLast').click();
 await page.waitForFunction(()=>document.getElementById('ccorReportCursor').textContent.includes('unavailable'));
 assert.equal(await page.locator('#ccorReviewZoom canvas').count(),0);
 await page.locator('#ccorReviewFirst').click();await ready();
 assert.match(await page.locator('#ccorFramePosition').innerText(),/1 \/ 8/);
 assert.deepEqual(errors,[]);
 console.log('PASS: fixed/reference crops, all frames, missing peaks, contrast, shared-drift context, marker toggle and source-coordinate identity');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();if(server)server.close();});
