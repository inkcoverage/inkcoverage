/* Select live text boxes, including stroke-only text, without a font UI. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8005';
const out=path.resolve('tmp/inspect-text');fs.mkdirSync(out,{recursive:true});
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    for(const mobile of [false,true]){
      const context=await browser.newContext({viewport:{width:mobile?390:1440,height:mobile?844:1000},hasTouch:mobile});
      const p=await context.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
      await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
      await p.goto(base);await p.locator('#fileInput').setInputFiles(path.resolve('tmp/inspect-pdf/live-text-sample.pdf'));
      await p.waitForFunction(()=>state.previewReady&&!state.isLoading);
      const response=p.waitForResponse(r=>r.url().endsWith('/api/inspect'));
      await p.locator('#btnInspectPdf').click();const data=await(await response).json();
      await p.waitForFunction(()=>!state.isAnalyzing&&!document.getElementById('inspectPage').hidden
        &&document.getElementById('inspectViewport').getAttribute('aria-busy')==='false');
      assert.equal(data.limited,false);
      const texts=data.objects.filter(o=>o.type==='Text');assert.equal(texts.length,3,'Invisible text excluded');
      const at=async item=>{
        const b=await p.locator('#inspectCanvas').boundingBox(),box=item.bounds;
        const x=b.x+(box[0]+box[2])/2/data.widthPt*b.width,y=b.y+(box[1]+box[3])/2/data.heightPt*b.height;
        if(mobile)await p.touchscreen.tap(x,y);else await p.mouse.click(x,y);
      };
      const value=key=>p.evaluate(key=>t('inspect_'+key),key);
      for(const lang of ['en','es','pt','zh','de','fr','uk']){
        await p.locator('#inspectClose').click();
        await p.locator(`.lang-btn[data-lang="${lang}"]`).click();await p.locator('#btnInspectPdf').click();
        await p.waitForFunction(()=>!state.isAnalyzing&&document.getElementById('inspectViewport').getAttribute('aria-busy')==='false');
        for(const item of texts){
          await at(item);const content=await p.locator('#inspectProperties').innerText();
          assert(content.includes(await value('type_Text')),content);
          assert(content.includes(await value('text_bounds')),content);
          const b=item.bounds;assert(content.includes(`${((b[2]-b[0])*25.4/72).toFixed(2)} × ${((b[3]-b[1])*25.4/72).toFixed(2)} mm`));
          if(item.stroke.kind==='Spot'){
            assert(content.includes('Gold Foil · 85%'));
            const fields=await p.locator('#inspectProperties dd').allTextContents();assert.equal(fields[0],await value('none'));
          }
          if(item.fill.kind==='CMYK')assert(content.includes('C 0% · M 70% · Y 100% · K 0%'));
          assert(!/Helvetica|Font family|Font size/.test(content));
        }
      }
      await p.screenshot({path:path.join(out,mobile?'mobile.png':'desktop.png')});
      if(mobile)await p.locator('#inspectToggleLayers').click();
      const eye=p.locator('#inspectLayerList [role="switch"]');await eye.click();
      await p.waitForFunction(()=>!state.isAnalyzing&&document.getElementById('inspectViewport').getAttribute('aria-busy')==='false');
      assert.equal(await eye.getAttribute('aria-checked'),'false');
      assert.equal(await p.locator('#inspectClear').isDisabled(),true,'Hiding text clears selection');
      await eye.click();await p.waitForFunction(()=>!state.isAnalyzing);
      if(mobile)await p.locator('#inspectToggleLayers').click();
      await p.locator('#inspectViewport').focus();await p.keyboard.press('ArrowRight');await p.keyboard.press('ArrowRight');await p.keyboard.press('Enter');
      assert((await p.locator('#inspectProperties').innerText()).includes(await value('type_Text')));
      assert.deepEqual(errors,[]);await context.close();
      console.log(`PASS live text ${mobile?'touch':'desktop'}: seven languages, metric dimensions, fill/outline inks, rotation, keyboard and layers.`);
    }
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
