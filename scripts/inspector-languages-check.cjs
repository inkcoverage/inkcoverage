const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8005';
const out=path.resolve(process.env.TEST_OUTPUT_DIR||'tmp/inspector-languages');fs.mkdirSync(out,{recursive:true});
const sample=path.resolve('tmp/inspect-pdf/estimator-inspector-sample.pdf');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
  await page.goto(base);await page.locator('#fileInput').setInputFiles(sample);
  await page.waitForFunction(()=>state.previewReady&&!state.isLoading);
  const doc=await page.evaluate(()=>state.pdfId);
  const value=(key,vars={})=>page.evaluate(({key,vars})=>t('inspect_'+key).replace(/\{(\w+)\}/g,(all,name)=>vars[name]??all),{key,vars});
  const ready=()=>page.waitForFunction(()=>!state.isAnalyzing&&!document.getElementById('inspectPage').hidden&&document.getElementById('inspectViewport').getAttribute('aria-busy')==='false');
  const at=async(x,y)=>{const b=await page.locator('#inspectCanvas').boundingBox();await page.mouse.click(b.x+x/500*b.width,b.y+(330-y)/330*b.height);};
  for(const lang of ['en','es','pt','zh','de','fr','uk']){
   await page.locator(`.lang-btn[data-lang="${lang}"]`).click();await page.waitForFunction(l=>currentLang===l,lang);
   assert.equal(await page.evaluate(()=>state.pdfId),doc);
   assert.equal(await page.locator('#btnInspectPdf span').innerText(),await value('title'));
   await page.locator('#btnInspectPdf').click();await ready();
   assert.equal(await page.locator('#inspectTitle').innerText(),await value('title'));
   assert.equal(await page.locator('#inspectHand').innerText(),await value('hand'));
   assert.equal(await page.locator('#inspectClose').getAttribute('aria-label'),await value('close'));
   await at(200,110);const properties=await page.locator('#inspectProperties').innerText();
   assert(properties.includes('100.00 × 60.00 mm'));assert(properties.includes('CutContour · 100%'));
   assert(properties.includes(await value('fill'))&&properties.includes(await value('none')));
   const foil=page.getByRole('switch',{name:await value('visibility',{name:'Logo / foil'})});
   await foil.click();await ready();assert.equal(await foil.getAttribute('aria-checked'),'false');
   await foil.click();await ready();await at(157,200);
   assert((await page.locator('#inspectProperties').innerText()).includes('Gold Foil · 100%'));
   await page.screenshot({path:path.join(out,`${lang}-desktop.png`)});
   await at(400,160);assert((await page.locator('#inspectProperties').innerText()).includes(await value('color_warning')));
   await page.setViewportSize({width:390,height:844});await page.waitForTimeout(100);
   const fitting=await page.locator('#inspectClose').evaluate(n=>{const b=n.getBoundingClientRect(),d=document.getElementById('pdfInspectorDialog').getBoundingClientRect();return b.right<=d.right&&b.left>=d.left;});
   assert(fitting,`${lang}: mobile close button fits`);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:path.join(out,`${lang}-mobile.png`)});
   await page.locator('#inspectClose').click();await page.setViewportSize({width:1440,height:900});
   await page.locator('#btnHelp').click();assert.equal(await page.locator('[data-i18n="inspect_help_body"]').innerText(),await value('help_body'));await page.locator('#btnCloseHelp').click();
   const support=await context.newPage();
   for(const name of ['about','faq','privacy']){
    await support.goto(base+(lang==='en'?'':'/'+lang)+'/'+name);
    assert(await support.locator(name==='about'?'#pdf-inspector':name==='privacy'?'.inspector-privacy':'.faq-item').count()>0);
    await support.setViewportSize({width:390,height:844});assert(await support.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   }
   await support.close();console.log(`PASS ${lang}: native inspector/properties/warnings/layers/Help, preserved source, support pages and mobile layout.`);
  }
  assert.deepEqual(errors,[]);await context.close();
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
