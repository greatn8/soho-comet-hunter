const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const html=process.env.CCOR_NATIVE_HTML;
if(!html)throw Error('CCOR_NATIVE_HTML must name the generated reviewer');
(async()=>{
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
try{
 const page=await browser.newPage({viewport:{width:1300,height:950},acceptDownloads:true});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(pathToFileURL(html).href);
 await page.waitForFunction(()=>document.getElementById('image').naturalWidth===2048);
 // Frame zero has a source at column 1200, array row 300 -> upper-left Y 1619.
 const expected=[];
 for(let i=0;i<3;i++){
  const x=1200+i*3,y=1619-i;
  const zoom=[25,75,125][i];
  await page.locator('#zoom').fill(String(zoom));
  await page.locator('#zoom').dispatchEvent('input');
  await page.evaluate(({x,y})=>{
   const v=document.getElementById('viewport'),im=document.getElementById('image');
   const s=im.getBoundingClientRect().width/2048;v.scrollLeft=Math.max(0,x*s-v.clientWidth/2);v.scrollTop=Math.max(0,y*s-v.clientHeight/2);
  },{x,y});
  const pixel=await page.locator('#image').evaluate((im,{x,y})=>{const r=im.getBoundingClientRect();return {x:r.left+(x+.5)*r.width/2048,y:r.top+(y+.5)*r.height/1920}},{x,y});
  await page.mouse.click(pixel.x,pixel.y);
  expected.push({x,y});
  if(i<2){await page.locator('#next').click();await page.waitForFunction(()=>document.getElementById('image').complete&&document.getElementById('image').naturalWidth===2048);}
 }
 assert.equal(await page.locator('#count').innerText(),'3 frames marked');
 assert.equal(await page.locator('#download').isDisabled(),false);
 const text=await page.locator('#report').inputValue();assert.match(text,/2048x1920/);assert.match(text,/Upper Left/);assert.match(text,/25.736000Z/);
 for(const p of expected)assert.ok(text.includes(' '+p.x+' '+p.y));
 // Inspect the actual downloaded JSON, rather than just text displayed by the page.
 const event=page.waitForEvent('download');await page.locator('#json').click();const download=await event;
 const filename=path.join(os.tmpdir(),'ccor-native-browser-'+process.pid+'.json');await download.saveAs(filename);
 const result=JSON.parse(fs.readFileSync(filename));fs.unlinkSync(filename);
 assert.equal(result.jpeg_conversion_applied,false);
 for(let i=0;i<3;i++)assert.deepEqual(result.observations[i].fits_pixel_1based,[expected[i].x+1,1920-expected[i].y]);
 await page.locator('#view').selectOption('temporal');
 await page.waitForFunction(()=>document.getElementById('image').complete);
 assert.equal(await page.locator('#count').innerText(),'3 frames marked');
 await page.locator('#samples button').first().click();assert.equal(await page.locator('#download').isDisabled(),true);
 assert.deepEqual(errors,[]);
 if(process.env.CCOR_NATIVE_SCREENSHOT)await page.screenshot({path:process.env.CCOR_NATIVE_SCREENSHOT,fullPage:false});
 const guide=path.resolve(__dirname,'../docs/ccor-native-reporting.html');
 await page.goto(pathToFileURL(guide).href+'?candidate=CCOR1-native-test');
 const command=await page.locator('#command').inputValue();
 assert.match(command,/--candidate "CCOR1-native-test"/);
 const syntax=require('node:child_process').spawnSync('bash',['-n'],{input:command,encoding:'utf8'});
 assert.equal(syntax.status,0,syntax.stderr);
 await page.locator('#candidate').fill('CCOR1-bad; touch /tmp/bad');
 assert.equal(await page.locator('#copy').isDisabled(),true);
 console.log('PASS: native source pixel → clicks at three zoom levels → FITS indices and source UTC export → contrast toggle and removal');
}finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
