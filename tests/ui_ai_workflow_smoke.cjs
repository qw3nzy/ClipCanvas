// UI fixtures exercise reversible application; model inference is tested separately.
const {chromium}=require(process.env.CLIPCANVAS_PLAYWRIGHT||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const folder=path.join(manifest.directory,'ui-ai');fs.mkdirSync(folder,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1536,height:1000}}),errors=[],passed=[];
  page.on('pageerror',error=>errors.push(error.message));
  const route=key=>page.locator('[data-route="'+key+'"]').click();
  const set=async(id,value)=>{await page.locator('#'+id).evaluate(e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;});await page.locator('#'+id).fill(String(value));await page.locator('#'+id).dispatchEvent('change');};
  const idle=()=>page.waitForFunction(()=>!busy()&&!state.activeJob,null,{timeout:120000});
  try{
    await page.goto(manifest.base);await page.waitForFunction(()=>window.ClipEditor&&connected);
    await route('settings');await page.waitForFunction(()=>document.querySelector('#modelChoice option[value="qwen3.5:9b"]'));
    const original='qwen3.5:9b';
    await page.locator('#modelChoice').selectOption('qwen3.5:4b');await page.locator('#speechDevice').selectOption('cpu');await page.locator('#saveModel').click();
    await page.waitForFunction(()=>document.getElementById('modelStatus').textContent.includes('qwen3.5:4b'));
    const stored=await page.evaluate(()=>api('/api/ai/config'));assert.equal(stored.model,'qwen3.5:4b');assert.equal(stored.speech_device,'cpu');
    await page.waitForFunction(()=>document.getElementById('aiStatus').textContent.includes('qwen3.5:4b'));
    await page.locator('#modelChoice').selectOption(original);await page.locator('#speechDevice').selectOption('auto');await page.locator('#saveModel').click();
    await page.waitForFunction(()=>document.getElementById('modelStatus').textContent.includes('qwen3.5:9b'));
    assert.match(await page.locator('#speechHardware').textContent(),/CUDA доступна/);
    assert.equal(await page.evaluate(()=>JSON.stringify(projectData()).includes('speech_device')),false);
    await page.screenshot({path:path.join(folder,'models-and-cuda.png'),fullPage:true});
    passed.push('Installed 4B/9B picker, persistent speech device, real CUDA probe, model status updates');
    await route('projects');await page.locator('#assetSelect').selectOption(manifest.source_id);await page.locator('#openAsset').click();
    await page.waitForFunction(id=>state.sourceId===id&&!busy()&&!restoring&&video.readyState>=1,manifest.source_id);await idle();
    await route('edit');
    await page.evaluate(()=>ClipEditor.applyJobResult({id:'auto-fixture',status:'done',request:{source_id:state.sourceId,auto_remove:true},boundaries:[
      {kind:'intro',start:0,end:1,confidence:.95,reason:'Приветствие'},
      {kind:'outro',start:7,end:8,confidence:.8,reason:'Неуверенное завершение'}]}));
    assert.deepEqual(await page.evaluate(()=>state.cuts),[{start:0,end:1}]);
    await page.evaluate(()=>ClipEditor.applyJobResult({id:'auto-fixture',status:'done',request:{source_id:state.sourceId,auto_remove:true},boundaries:[]}));
    assert.equal(await page.evaluate(()=>state.cuts.length),1);
    await page.locator('#undoEdit').click();assert.equal(await page.evaluate(()=>state.cuts.length),0);
    await page.locator('#redoEdit').click();assert.equal(await page.evaluate(()=>state.cuts.length),1);
    await page.locator('#undoEdit').click();
    passed.push('Automatic cut confidence threshold, duplicate-result protection, one-step undo and redo');
    await route('design');await set('canvasWidth',600);await set('canvasHeight',600);await page.locator('#applyCanvas').click();
    await page.locator('#addBannerFile').setInputFiles(path.join(manifest.directory,'composition','key-alpha.png'));
    await page.waitForFunction(()=>state.banners.length===1&&!busy());
    await set('bannerInstructions','Баннер сверху. Размер и время показа сохранять.');
    await page.locator('#bannerZone').selectOption('top');await page.locator('#bannerZone').dispatchEvent('change');
    const before=await page.evaluate(()=>structuredClone(state.banners[0]));
    await page.evaluate(()=>{
      const b=state.banners[0];
      ClipEditor.applyJobResult({type:'banner-layout',status:'done',source_id:state.sourceId,banner_id:b.id,asset_id:b.asset_id,
        request:{original:structuredClone(b.geometry),instructions:b.instructions,rules:structuredClone(b.layout_rules)},
        geometry:{...b.geometry,x:0,y:20},requirements:{reason:'Сверху, размер сохранён'},message:'Размещение подобрано.'},'banner-layout');
    });
    await page.locator('#applyBannerLayout').click();
    const after=await page.evaluate(()=>structuredClone(state.banners[0]));
    assert.equal(after.geometry.y,20);assert.equal(after.geometry.w,before.geometry.w);assert.equal(after.start,before.start);assert.equal(after.end,before.end);assert.equal(after.asset_id,before.asset_id);assert.equal(after.instructions,before.instructions);
    await page.keyboard.press('Control+z');assert.deepEqual(await page.evaluate(()=>state.banners[0].geometry),before.geometry);
    await page.screenshot({path:path.join(folder,'banner-client-rules.png'),fullPage:true});
    await page.locator('#saveProject').click();await page.waitForFunction(()=>document.getElementById('autosaveState').textContent==='Проект сохранён');
    await page.reload();await page.waitForFunction(()=>window.ClipEditor&&state.banners.length===1&&!busy()&&!restoring);
    assert.equal(await page.evaluate(()=>state.banners[0].instructions),'Баннер сверху. Размер и время показа сохранять.');
    assert.equal(await page.evaluate(()=>state.banners[0].layout_rules.zone),'top');
    passed.push('Banner client instructions/rules persist, placement preserves asset and timing, undo restores geometry');
    await page.setViewportSize({width:320,height:1000});for(const key of ['settings','edit','design']){await route(key);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);}
    assert.deepEqual(errors,[]);passed.push('No JavaScript errors or 320px overflow');
    fs.writeFileSync(path.join(folder,'result.json'),JSON.stringify({passed,errors},null,2));console.log(JSON.stringify({passed,folder},null,2));
  }catch(error){console.error('UI diagnostics',await page.evaluate(()=>({source:state.sourceId,cuts:state.cuts,restoring,importBusy:state.importBusy,toast:document.getElementById('toast').textContent,bannerStatus:document.getElementById('bannerAIStatus').textContent,proposal:state.editor.bannerProposal,banner:state.banners[0]})));await page.screenshot({path:path.join(folder,'failure.png'),fullPage:true}).catch(()=>{});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
