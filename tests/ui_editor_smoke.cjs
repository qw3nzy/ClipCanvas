// Extended editor acceptance against an isolated QA server. Boundary proposals
// below are deterministic UI fixtures; real model quality is measured separately.
const {chromium} = require(process.env.CLIPCANVAS_PLAYWRIGHT || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const out=path.join(manifest.directory,'ui-editor');fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1536,height:1050}}),errors=[],passed=[];
  page.on('pageerror',e=>errors.push(e.message));
  const route=key=>page.locator(`[data-route="${key}"]`).click();
  const set=async(id,value)=>{await page.locator('#'+id).evaluate(e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;});await page.locator('#'+id).fill(String(value));await page.locator('#'+id).dispatchEvent('change');};
  const slider=async(id,value)=>page.locator('#'+id).evaluate((e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));},String(value));
  const idle=()=>page.waitForFunction(()=>!busy()&&!state.activeJob,null,{timeout:180000});
  async function job(id,endpoint){await page.locator('#'+id).evaluate(e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;});const response=page.waitForResponse(r=>r.url().endsWith(endpoint)&&r.request().method()==='POST');await page.locator('#'+id).click();const result=await response;assert.equal(result.status(),200,await result.text());const created=await result.json();await page.waitForFunction(async id=>{const j=await(await fetch('/api/jobs/'+id)).json();if(j.status==='error')throw Error(j.error);return j.status==='done';},created.id,{timeout:180000});await idle();return created;}
  try{
    await page.goto(manifest.base);await page.waitForFunction(()=>window.ClipEditor&&connected);
    await route('projects');await page.locator('#assetSelect').selectOption(manifest.source_id);await page.locator('#openAsset').click();await idle();
    await route('edit');
    await page.evaluate(()=>ClipEditor.applyJobResult({status:'done',transcript_partial:true,
      request:{source_id:state.sourceId},boundaries:[],cues:[{start:0,end:1,text:'Только край'}]}));
    assert.equal(await page.evaluate(()=>state.subtitles.cues.length),0,'Partial edge transcript must not replace full captions');
    await job('extractSubtitles','/api/subtitles/extract');
    assert.equal(await page.evaluate(()=>state.subtitles.cues.length),2);
    await page.evaluate(()=>ClipEditor.applyJobResult({status:'done',request:{source_id:state.sourceId},boundaries:[
      {kind:'intro',start:0,end:1,reason:'Контрольное вступление',confidence:.92},
      {kind:'outro',start:7,end:8,reason:'Контрольное завершение',confidence:.9}]}));
    await page.locator('.boundary-card').first().getByRole('button',{name:'Удалить участок',exact:true}).click();
    await page.locator('.boundary-card').nth(1).getByRole('button',{name:'Удалить участок',exact:true}).click();
    assert.equal(await page.evaluate(()=>state.cuts.length),2);
    await page.locator('#undoEdit').click();assert.equal(await page.evaluate(()=>state.cuts.length),1);
    await page.locator('#redoEdit').click();assert.equal(await page.evaluate(()=>state.cuts.length),2);
    passed.push('Intro/outro proposal fixture: accept, source preservation, undo and redo');
    await route('design');await page.locator('[data-preset="portrait"]').click();
    assert.deepEqual(await page.evaluate(()=>[state.width,state.height]),[1080,1350]);
    await set('canvasWidth',240);await set('canvasHeight',300);await page.locator('#applyCanvas').click();
    await slider('backgroundBlur',12);await page.locator('#showGuides').check();
    await route('subtitles');await page.locator('#subtitlesEnabled').check();await set('subtitleSize',20);await set('captionSpacing',1.4);await set('captionWidth',80);
    await page.locator('#subtitleList .subtitle-row').first().getByRole('button',{name:'Оформление',exact:true}).click();
    await set('captionX',45);await set('captionY',78);await page.locator('#captionItalic').check();
    assert.equal(await page.evaluate(()=>state.subtitles.cues[0].appearance.x),45);
    assert.equal(await page.evaluate(()=>state.subtitles.cues[1].appearance?.x??null),null);
    const selection=await page.locator('#captionSelection').boundingBox();assert.ok(selection);
    await page.mouse.move(selection.x+selection.width/2,selection.y+selection.height/2);await page.mouse.down();await page.mouse.move(selection.x+selection.width/2+15,selection.y+selection.height/2-10,{steps:4});await page.mouse.up();
    assert.ok(await page.evaluate(()=>state.subtitles.cues[0].appearance.x>45));
    const rows=()=>page.locator('#subtitleList .subtitle-row');
    await rows().first().getByRole('button',{name:'Разделить',exact:true}).click();assert.equal(await page.evaluate(()=>state.subtitles.cues.length),3);
    await rows().first().getByRole('button',{name:'Объединить ↓',exact:true}).click();assert.equal(await page.evaluate(()=>state.subtitles.cues.length),2);
    passed.push('4:5 and custom canvas, blur, per-cue coordinates/style, drag, split and merge');
    await route('design');await page.locator('#addBannerFile').setInputFiles(path.join(manifest.directory,'composition','key-alpha.png'));
    await page.waitForFunction(()=>state.banners.length===1);await set('multiBannerStart',1);await set('multiBannerEnd',4);
    await page.locator('#multiBannerControls details summary').filter({hasText:'Удаление однотонного фона'}).click();await page.locator('#bannerKeyEnabled').check();await slider('bannerTolerance',.1);await slider('bannerEdge',.02);
    await page.waitForFunction(()=>state.banners[0].chromakey.enabled);
    assert.equal(await page.evaluate(()=>state.banners[0].start),2);
    await page.locator('#addBannerFile').setInputFiles(path.join(manifest.directory,'composition','key-alpha.png'));
    await page.waitForFunction(()=>state.banners.length===2);await set('multiBannerStart',4);await set('multiBannerEnd',6);
    await page.locator('#editBannerPosition').click();await set('layerX',130);await set('layerY',20);await set('layerW',80);await set('layerH',40);
    assert.equal(await page.locator('.banner-track-bar').count(),2);
    passed.push('Multiple banners with final-time intervals, independent geometry and color removal');
    await page.locator('#themeSelect').selectOption('dark');await page.screenshot({path:path.join(out,'design-dark.png'),fullPage:true});
    await page.locator('#fullscreenButton').click();await page.waitForFunction(()=>!!document.fullscreenElement);
    await slider('previewVolume',.35);assert.equal(await page.evaluate(()=>video.volume),.35);
    await page.locator('#playButton').click();await page.waitForFunction(()=>!selectedMedia().paused);
    await page.locator('#playButton').click();await page.keyboard.press('Escape');
    await page.waitForFunction(()=>!document.fullscreenElement);passed.push('Fullscreen composition retains playback, volume and Escape exit');
    for(const theme of ['dark','light','system']){await page.locator('#themeSelect').selectOption(theme);for(const key of ['projects','edit','design','subtitles','export','settings']){await route(key);assert.equal(await page.locator('#editorPane'+key).isVisible(),true);}}
    await route('settings');await page.waitForFunction(()=>document.getElementById('modelName').value);
    const originalModel=await page.locator('#modelName').inputValue();await set('modelName','clipcanvas-qa-missing');
    await page.locator('#saveModel').click();await page.waitForFunction(()=>document.getElementById('modelStatus').textContent.includes('clipcanvas-qa-missing'));
    const modelResponse=page.waitForResponse(response=>response.url().endsWith('/api/ai/test')&&response.request().method()==='POST');
    await page.locator('#testModel').click();const modelTest=await (await modelResponse).json();
    assert.ok(!modelTest.available&&!modelTest.ok&&modelTest.error,'Missing or unreachable model must report a usable error');
    await page.waitForFunction(error=>document.getElementById('modelStatus').textContent.includes(error),modelTest.error);
    assert.equal(await page.evaluate(()=>JSON.stringify(projectData()).includes('clipcanvas-qa-missing')),false);
    await set('modelName',originalModel);await page.locator('#saveModel').click();
    await page.waitForFunction(model=>document.getElementById('modelStatus').textContent.includes(model),originalModel);
    passed.push('Model configuration persists on server, missing/unreachable model has readable error and never enters project');
    await page.locator('#themeSelect').selectOption('dark');await route('export');await page.locator('#sampleMode').selectOption('range');await set('sampleStart',0);await set('sampleEnd',8);
    const sample=await job('renderOne','/api/sample');await page.waitForFunction(()=>!document.getElementById('sampleResult').hidden);
    const full=await job('exportButton','/api/render');
    const resolved=await page.evaluate(async()=>post('/api/composition/resolve',renderConfig(false)));
    assert.equal(resolved.parts[0].duration,6);assert.ok(resolved.parts[0].banners.length>=2);
    const saved=await page.evaluate(()=>JSON.stringify(projectData()));assert.ok(!saved.includes('api_key'));
    await page.locator('#saveProject').click();await page.waitForFunction(()=>document.getElementById('autosaveState').textContent==='Проект сохранён');
    await page.screenshot({path:path.join(out,'export-dark.png'),fullPage:true});
    await page.reload();await page.waitForFunction(()=>window.ClipEditor&&state.banners.length===2&&!busy());
    assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');assert.equal(await page.evaluate(()=>state.cuts.length),2);assert.equal(await page.evaluate(()=>state.background.blur),12);
    for(const width of [736,320]){await page.setViewportSize({width,height:1000});for(const key of ['projects','edit','design','subtitles','export','settings']){await route(key);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'Overflow: '+key+' '+width);}await page.screenshot({path:path.join(out,'mobile-'+width+'.png'),fullPage:true});}
    assert.deepEqual(errors,[]);passed.push('All six routes and three themes, project restore, 736/320px, no JavaScript errors');
    passed.push('Actual sample and full MP4 through the UI with current composition');
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({passed,sample_job:sample.id,full_job:full.id},null,2));console.log(JSON.stringify({passed,directory:out},null,2));
  }catch(error){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});console.error('UI errors',errors,'toast',await page.locator('#toast').textContent().catch(()=>''));throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
