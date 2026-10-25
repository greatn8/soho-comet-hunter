const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../docs');
let server;

(async()=>{
server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://localhost');
 const file=path.join(root,url.pathname==='/'?'index.html':url.pathname);
 if(!file.startsWith(root+path.sep)){res.writeHead(403).end();return;}
 fs.readFile(file,(err,data)=>{if(err){res.writeHead(404).end();return;}
 res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.json')?'application/json':'text/html');res.end(data);});
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const context=await browser.newContext({viewport:{width:1200,height:900}}),page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.route('https://services.swpc.noaa.gov/**',async route=>{
const svg='<svg xmlns="http://www.w3.org/2000/svg" width="1204" height="1205"><rect width="1204" height="1205" fill="#222"/><circle cx="600" cy="700" r="3" fill="white"/></svg>';
await route.fulfill({contentType:'image/svg+xml',body:svg});});
const points=['1315','1330','1345','1400'].map((t,i)=>({time:'2026-10-25T'+t.slice(0,2)+':'+t.slice(2)+':00Z',x:161-i*4,y:283+i,image_url:'https://services.swpc.noaa.gov/products/ccor1/jpegs/20261025_'+t+'_ccor1_1024by960.jpg'}));
const track={id:'CCOR1-coordinate-test',camera:'ccor1',first:points[0].time,last:points.at(-1).time,frames:4,points,processing_shape:[512,512],source_jpeg_shape:[1204,1205],x_first:161,x_last:149,y_first:283,y_last:286};
await page.route('**/data/ccor.json?*',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({tracks:[track],review_shortlist_ids:[track.id],frame_manifests:{ccor1:points},instruments:{},generated_at:'2026-10-25T14:00:00Z'})}));
await page.goto('http://127.0.0.1:'+server.address().port+'/');
await page.waitForSelector('[data-ccor-id]');await page.locator('[data-ccor-id]').first().click();
await page.waitForFunction(()=>document.getElementById('ccorReportCursor').textContent.includes('1204'));
await page.locator('#ccorReportMark').click();
for(let i=0;i<3;i++){
 const pos=await page.locator('#ccorReviewImage').evaluate(img=>{
 const r=img.getBoundingClientRect(),scale=Math.min(r.width/img.naturalWidth,r.height/img.naturalHeight);
 return {x:r.left+(r.width-img.naturalWidth*scale)/2+600.5*scale,y:r.top+(r.height-img.naturalHeight*scale)/2+700.5*scale};});
 await page.mouse.click(pos.x,pos.y);
 if(i<2){await page.locator('#ccorReviewNext').click();await page.waitForFunction(()=>document.getElementById('ccorReportCursor').textContent.includes('1204'));}
}
assert.equal(await page.locator('#ccorReportCount').innerText(),'3 frames marked');
let text=await page.locator('#ccorReportText').inputValue();assert.match(text,/1204 x 1205/);assert.match(text,/600 700/);
assert.equal(await page.locator('#ccorReportCopy').isDisabled(),false);
const saved=await page.evaluate(()=>localStorage.getItem('ccor-source-image-measurements-v1'));assert.ok(saved.includes('"x":600'));
await page.screenshot({path:process.env.CCOR_SCREENSHOT||'/tmp/ccor-report-ui.png',fullPage:false});
await page.reload();await page.waitForSelector('[data-ccor-id]');await page.locator('[data-ccor-id]').first().click();
assert.equal(await page.locator('#ccorReportCount').innerText(),'3 frames marked');
await page.locator('#ccorReportPositions button').first().click();assert.equal(await page.locator('#ccorReportCopy').isDisabled(),true);
console.log('PASS: browser click → three frames → exact source pixel export → restore → remove. Page errors:',JSON.stringify(errors));
await browser.close();server.close();if(errors.length)process.exitCode=1;
})().catch(e=>{console.error(e);if(server)server.close();process.exit(1)});
