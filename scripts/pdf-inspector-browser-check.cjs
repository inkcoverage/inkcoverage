/* Real local PDF workflow plus isolated stale/failure/keyboard/layout checks. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8002';
const output = path.resolve('tmp/inspect-pdf');
fs.mkdirSync(output, {recursive:true});
const sample = path.join(output, 'estimator-inspector-sample.pdf');
const waitFrames = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  try {
    let savedPacket, savedLayerView;
    for (const mobile of [false, true]) {
      const context = await browser.newContext({viewport:{width:mobile?390:1440, height:mobile?844:900}, hasTouch:mobile});
      const page = await context.newPage(), errors = [], calls = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('response', async response => {
        if (response.url().endsWith('/api/inspect') && response.ok()) {
          const data = await response.json(); if (data.page === 1) savedPacket = data;
        }
        if (response.url().endsWith('/api/inspect/layers') && response.ok()) {
          const data = await response.json(); if (data.page === 1 && !data.hiddenLayers.length) savedLayerView = data;
        }
      });
      page.on('request', request => { if (request.url().endsWith('/api/inspect')) calls.push(request.postDataJSON()); });
      await page.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
      await page.goto(base);
      await page.locator('#fileInput').setInputFiles(sample);
      await page.waitForFunction(() => state.previewReady && !state.isLoading);
      // A genuine pointer crop and a completed average result must survive inspection.
      const crop = await page.locator('#cropOverlay').boundingBox();
      await page.mouse.move(crop.x+crop.width*.08, crop.y+crop.height*.1);
      await page.mouse.down(); await page.mouse.move(crop.x+crop.width*.65, crop.y+crop.height*.65, {steps:5}); await page.mouse.up();
      await page.locator('#btnAnalyze').click();
      await page.locator('#btnStartBatch').click();
      await page.waitForFunction(() => !state.isAnalyzing && !!validResult(1));
      const before = await page.evaluate(() => JSON.stringify({crop:state.crop, selection:state.selectionVersion, results:state.pageResults, scopes:state.resultScopes, zoom:document.querySelector('[data-zoom="1"]').getAttribute('aria-pressed')}));
      await page.locator('#btnInspectPdf').click();
      await page.waitForFunction(() => !document.getElementById('inspectPage').hidden && !state.isAnalyzing);
      const dialog = page.locator('#pdfInspectorDialog');
      assert(await dialog.isVisible());
      const at = async (x,y,click=false) => {
        const box = await page.locator('#inspectCanvas').boundingBox();
        await page.mouse.move(box.x+x/500*box.width, box.y+(330-y)/330*box.height);
        if (click) {
          if (mobile) await page.touchscreen.tap(box.x+x/500*box.width, box.y+(330-y)/330*box.height);
          else await page.mouse.click(box.x+x/500*box.width, box.y+(330-y)/330*box.height);
        }
        await waitFrames(page);
      };
      const layers = page.locator('#inspectLayerList');
      const foilEye = layers.getByRole('switch', {name:'Logo / foil layer visibility'});
      const cutEye = layers.getByRole('switch', {name:'Dieline layer visibility'});
      const layerReady = () => page.waitForFunction(() => !state.isAnalyzing && document.getElementById('inspectViewport').getAttribute('aria-busy') === 'false');
      if (mobile) await page.locator('#inspectToggleLayers').click();
      await waitFrames(page);
      if (!mobile) {
        const layout = await page.evaluate(() => {
          const box = s => document.querySelector(s).getBoundingClientRect().toJSON();
          return {header:box('.inspect-header'), title:box('#inspectTitle'), tools:box('.inspect-toolbar'), hint:box('.inspect-guidance'), left:box('#inspectLayers'), preview:box('#inspectViewport'), right:box('#inspectProperties')};
        });
        assert(Math.abs(layout.title.y-layout.tools.y)<12, 'Zoom tools share the title row');
        assert(layout.header.height<65 && layout.hint.y>=layout.header.bottom-1);
        assert(layout.left.right<=layout.preview.left+1 && layout.preview.right<=layout.right.left+1);
        assert.equal(await page.locator('.inspect-footer').count(),0);
      }
      await at(200,110,true);
      assert.match(await page.locator('#inspectProperties').innerText(), /100\.00 × 60\.00 mm/);
      assert.match(await page.locator('#inspectProperties').innerText(), /CutContour · 100%/);
      assert.match(await page.locator('#inspectProperties').innerText(), /Fill\s+None/);
      await page.screenshot({path:path.join(output,mobile?'mobile-dieline.png':'desktop-dieline.png')});
      // Clicking another shape does not displace a pinned selection until clicked.
      await at(400,160); assert.match(await page.locator('#inspectProperties').innerText(), /CutContour/);
      await page.locator('#inspectClear').click();
      await at(157,200,true);
      assert.match(await page.locator('#inspectProperties').innerText(), /Gold Foil · 100%/);
      await page.screenshot({path:path.join(output,mobile?'mobile-foil.png':'desktop-foil.png')});
      const beforeHide = await page.locator('#inspectImage').getAttribute('src');
      await foilEye.click(); await layerReady();
      assert.equal(await foilEye.getAttribute('aria-checked'),'false');
      assert.notEqual(await page.locator('#inspectImage').getAttribute('src'),beforeHide,'Layer artwork actually changes');
      assert.match(await page.locator('#inspectProperties').innerText(),/Find a size or ink/,'Hiding a selected layer clears selection');
      await at(157,200,true);
      assert(!(await page.locator('#inspectProperties').innerText()).includes('Gold Foil'),'Hidden object cannot be selected');
      const underlying = await page.locator('#inspectImage').evaluate(img => {
        const c=document.createElement('canvas');c.width=img.naturalWidth;c.height=img.naturalHeight;
        const ctx=c.getContext('2d');ctx.drawImage(img,0,0);return [...ctx.getImageData(Math.floor(img.naturalWidth*157/500),Math.floor(img.naturalHeight*130/330),1,1).data];
      });
      assert(underlying[2]>200,'Hiding foil reveals the original pale background');
      await foilEye.click(); await layerReady(); await at(157,200,true);
      assert.match(await page.locator('#inspectProperties').innerText(),/Gold Foil/);
      await cutEye.click(); await layerReady();
      assert.match(await page.locator('#inspectProperties').innerText(),/Gold Foil/,'Visible selection is retained');
      await at(200,110,true);assert(!(await page.locator('#inspectProperties').innerText()).includes('CutContour'));
      await cutEye.click();await layerReady();
      await at(80,130,true);
      assert.match(await page.locator('#inspectProperties').innerText(), /Text box/);
      assert.match(await page.locator('#inspectProperties').innerText(), /C 0% · M 0% · Y 0% · K 100%/);
      // The live label above the panel is now selectable. Sample the panel
      // away from that text box to verify its separate native ink recipe.
      await at(80,145,true);
      assert.match(await page.locator('#inspectProperties').innerText(), /C 85% · M 25% · Y 0% · K 5%/);
      await at(295,60,true);
      assert.match(await page.locator('#inspectProperties').innerText(), /Gray · 75% black/);
      for (const [x,y,type] of [[400,240,'Vector shape'],[400,160,'Image'],[400,90,'Gradient']]) {
        await at(x,y,true);
        const content = await page.locator('#inspectProperties').innerText();
        assert(content.includes(type), content); assert.match(content, /colour cannot be read reliably/);
        assert(!content.includes('C 100%'), content);
      }
      await at(215,70,true);
      assert(await page.locator('#inspectNextObject').isVisible());
      const top = await page.locator('#inspectProperties').innerText();
      await page.locator('#inspectNextObject').click();
      assert.notEqual(await page.locator('#inspectProperties').innerText(), top);
      await page.locator('[data-inspect-zoom="5"]').click();
      const scrolled = await page.locator('#inspectViewport').evaluate(v => ({w:v.scrollWidth,c:v.clientWidth}));
      assert(scrolled.w > scrolled.c);
      await page.locator('#inspectHand').click();
      const view = await page.locator('#inspectViewport').boundingBox();
      const panBefore = await page.locator('#inspectViewport').evaluate(v => v.scrollLeft);
      await page.mouse.move(view.x+view.width*.6,view.y+view.height*.5); await page.mouse.down();
      await page.mouse.move(view.x+view.width*.3,view.y+view.height*.5,{steps:6}); await page.mouse.up();
      assert.notEqual(await page.locator('#inspectViewport').evaluate(v => v.scrollLeft), panBefore);
      await page.locator('#inspectClose').click();
      assert.equal(await page.evaluate(() => JSON.stringify({crop:state.crop, selection:state.selectionVersion, results:state.pageResults, scopes:state.resultScopes, zoom:document.querySelector('[data-zoom="1"]').getAttribute('aria-pressed')})), before);
      await page.locator('#btnInspectPdf').click(); await layerReady(); await waitFrames(page);
      assert.equal(calls.length, 1, 'Reopen should reuse this page’s inspection');
      await page.locator('#inspectViewport').focus();
      await page.keyboard.press('ArrowRight'); assert.match(await page.locator('#inspectProperties').innerText(), /Vector shape/);
      await page.keyboard.press('Enter'); assert.match(await page.locator('#inspectProperties').innerText(), /Selected object/i);
      await page.keyboard.press('Escape'); assert(!(await dialog.isVisible()));
      await page.locator('.units-btn[data-units="in"]').click();
      await page.locator('#btnInspectPdf').click(); await layerReady(); await waitFrames(page);
      await at(200,110,true); assert.match(await page.locator('#inspectProperties').innerText(), /3\.937 × 2\.362 in/);
      await page.locator('#inspectClose').click();
      await page.locator('#btnNextPage').click(); await page.waitForFunction(() => state.currentPage === 2 && state.previewReady && !state.isLoading);
      await page.locator('#btnInspectPdf').click(); await page.waitForFunction(() => !document.getElementById('inspectPage').hidden && !state.isAnalyzing);
      assert.equal(calls.at(-1).page, 2);
      assert.equal(await page.locator('#inspectLayerList [role="switch"]').count(),2);
      await page.locator('#inspectViewport').focus(); await page.keyboard.press('ArrowRight'); await page.keyboard.press('Enter');
      assert.match(await page.locator('#inspectProperties').innerText(), /0\.556 × 1\.111 in/);
      await page.screenshot({path:path.join(output,mobile?'mobile-rotated.png':'desktop-rotated.png')});
      const geometry = await page.evaluate(() => {
        const d=document.getElementById('pdfInspectorDialog').getBoundingClientRect();
        return {left:d.left,right:d.right,top:d.top,bottom:d.bottom,w:innerWidth,h:innerHeight,body:document.documentElement.scrollWidth};
      });
      assert(geometry.left >= 0 && geometry.top >= 0 && geometry.right <= geometry.w && geometry.bottom <= geometry.h);
      assert(geometry.body <= geometry.w, 'No page overflow');
      assert.deepEqual(errors, []);
      await context.close();
      console.log(`PASS real ${mobile?'mobile':'desktop'}: measured dieline/foil/CMYK, warnings, overlaps, zoom/pan, keyboard, cache, units, rotated page and unchanged crop/results.`);
    }
    // Ownership/failure and translated toolbar layout use controlled responses.
    for (const lang of ['en','es','pt','zh','de','fr','uk']) {
      const context = await browser.newContext({viewport:{width:390,height:844}});
      const page = await context.newPage();
      await page.route('**/*', r => new URL(r.request().url()).origin === base ? r.continue() : r.abort());
      await page.route('**/api/inspect', r => r.fulfill({json:savedPacket}));
      await page.route('**/api/inspect/layers',r=>r.fulfill({json:savedLayerView}));
      await page.goto(base+(lang==='en'?'':'/'+lang)); await page.locator('#fileInput').setInputFiles(sample);
      await page.waitForFunction(() => state.previewReady && !state.isLoading);
      assert(await page.locator('#btnInspectPdf').isVisible());
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.locator('#btnInspectPdf').click(); await page.waitForFunction(() => !document.getElementById('inspectPage').hidden);
      if (lang==='en') for(const width of [320,768,1024,1440]) {
        await page.setViewportSize({width,height:900}); await waitFrames(page);
        assert(await page.locator('#inspectClose').evaluate(node=>{
          const r=node.getBoundingClientRect(),d=document.getElementById('pdfInspectorDialog').getBoundingClientRect();
          return r.right<=d.right&&r.left>=d.left;
        }),`Inspector header fits ${width}px`);
      }
      await page.locator('#inspectClose').click();
      await context.close();
    }
    console.log('PASS seven-language mobile toolbar and English prototype dialog.');
    const context = await browser.newContext({viewport:{width:1366,height:768}});
    const page = await context.newPage(), errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
    let held, release;
    await page.route('**/api/inspect', async r => { await new Promise(resolve=>{release=resolve;held=true;}); await r.fulfill({json:savedPacket}); });
    await page.goto(base);await page.locator('#fileInput').setInputFiles(sample);await page.waitForFunction(()=>state.previewReady&&!state.isLoading);
    await page.locator('#btnInspectPdf').click();while(!held)await page.waitForTimeout(20);
    await page.locator('#inspectClose').click();release();await page.waitForFunction(()=>!state.isAnalyzing);
    assert.equal(await page.locator('#pdfInspectorDialog').isVisible(),false);
    await page.unroute('**/api/inspect');
    await page.route('**/api/inspect', r=>r.fulfill({status:422,json:{detail:'Unsupported test page'}}));
    await page.locator('#btnInspectPdf').click();await page.waitForFunction(()=>!state.isAnalyzing);
    assert.equal(await page.locator('#inspectStatus').innerText(),await page.evaluate(()=>t('inspect_read_failed')));
    assert.equal(await page.locator('#inspectPage').isVisible(),false);
    await page.locator('#inspectClose').click();
    await page.unroute('**/api/inspect');
    await page.route('**/api/inspect',r=>r.fulfill({json:savedPacket}));
    await page.route('**/api/inspect/layers',r=>{
      const hidden=r.request().postDataJSON().hiddenLayers;
      return hidden.length ? r.fulfill({status:422,json:{detail:'Layer render test failure'}}) : r.fulfill({json:savedLayerView});
    });
    await page.locator('#btnInspectPdf').click();await page.waitForFunction(()=>!state.isAnalyzing&&!document.getElementById('inspectPage').hidden);
    const foilEye=page.locator('#inspectLayerList').getByRole('switch',{name:'Logo / foil layer visibility'});
    const unchangedImage=await page.locator('#inspectImage').getAttribute('src');
    await foilEye.click();await page.waitForFunction(()=>!state.isAnalyzing);
    assert.equal(await foilEye.getAttribute('aria-checked'),'true','Failed layer render retains previous visibility');
    assert.equal(await page.locator('#inspectImage').getAttribute('src'),unchangedImage);
    assert.equal(await page.locator('#inspectStatus').innerText(),await page.evaluate(()=>t('inspect_layer_failed')));
    await page.unroute('**/api/inspect/layers');
    held=false;release=null;
    await page.route('**/api/inspect/layers',async r=>{
      const hidden=r.request().postDataJSON().hiddenLayers;
      await new Promise(resolve=>{release=resolve;held=true;});
      await r.fulfill({json:{...savedLayerView,hiddenLayers:hidden}});
    });
    await foilEye.click();while(!held)await page.waitForTimeout(20);
    await page.locator('#inspectClose').click();release();await page.waitForFunction(()=>!state.isAnalyzing);
    assert.equal(await page.locator('#pdfInspectorDialog').isVisible(),false);
    await page.locator('#btnInspectPdf').click();await page.waitForFunction(()=>!state.isAnalyzing);
    assert.equal(await foilEye.getAttribute('aria-checked'),'true','Late response cannot hide a layer after closing');
    await page.locator('#inspectClose').click();
    assert.deepEqual(errors,[]);await context.close();
    console.log('PASS metadata/layer delayed-close ownership and layer failure rollback.');
    // Decimal MediaBoxes round differently in Ghostscript's metadata output.
    // Also keep the original preview selectable when its initial layer request
    // fails, while rejecting an actually different page size.
    for (const mode of ['rounded', 'failed', 'wrong-size']) {
      const c=await browser.newContext({viewport:{width:1366,height:768}}),p=await c.newPage();
      await p.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
      await p.route('**/api/inspect',r=>r.fulfill({json:{...savedPacket,widthPt:savedPacket.widthPt+.00049}}));
      await p.route('**/api/inspect/layers',r=>mode==='failed'
        ?r.fulfill({status:422,json:{detail:'Initial layer preview test failure'}})
        :r.fulfill({json:{...savedLayerView,widthPt:savedLayerView.widthPt+(mode==='wrong-size'?10:0)}}));
      await p.goto(base);await p.locator('#fileInput').setInputFiles(sample);
      await p.waitForFunction(()=>state.previewReady&&!state.isLoading);
      await p.locator('#btnInspectPdf').click();
      await p.waitForFunction(()=>!state.isAnalyzing&&!document.getElementById('inspectPage').hidden
        &&document.getElementById('inspectViewport').getAttribute('aria-busy')==='false');
      assert.equal(await p.locator('#inspectStatus').getAttribute('data-error'),String(mode!=='rounded'));
      const b=await p.locator('#inspectCanvas').boundingBox();
      await p.mouse.click(b.x+200/500*b.width,b.y+220/330*b.height);
      assert.match(await p.locator('#inspectProperties').innerText(),/Selected object/i);
      await p.locator('#inspectClear').click();await p.locator('#inspectViewport').focus();
      await p.keyboard.press('ArrowRight');await p.keyboard.press('Enter');
      assert.match(await p.locator('#inspectProperties').innerText(),/Selected object/i);
      await c.close();
    }
    console.log('PASS rounded dimensions, initial layer failure selection and wrong-size rejection.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
