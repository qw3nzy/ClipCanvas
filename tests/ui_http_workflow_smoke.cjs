'use strict';
const {chromium} = require(process.env.CLIPCANVAS_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {createHash} = require('node:crypto');
(async () => {
  const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const browser = await chromium.launch({headless:true, executablePath:process.env.CLIPCANVAS_CHROMIUM, args:['--no-proxy-server']});
  const page = await browser.newPage({viewport:{width:1536,height:1050}}), errors = [], passed = [];
  page.on('pageerror', error => errors.push(error.message));
  const automaticRequests=[];page.on('request',request=>{if(request.url().endsWith('/api/ai/automatic')&&request.method()==='POST')automaticRequests.push(request);});
  const route = name => page.locator(`[data-route="${name}"]`).click();
  const uuid = value => assert.match(value, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  const openParents = selector => page.locator(selector).evaluate(node => { for(let p=node.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true; });
  async function waitJob(id) {
    const deadline = Date.now()+120000;
    while(Date.now()<deadline) {
      const result = await page.request.get(manifest.base+'/api/jobs/'+id), job = await result.json();
      if(job.status==='done')return job;
      if(['error','cancelled'].includes(job.status))throw Error(job.error||job.status);
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    throw Error('Job timeout: '+id);
  }
  try {
    await page.goto(manifest.base);
    assert.deepEqual(await page.evaluate(()=>({secure:isSecureContext,uuid:typeof crypto.randomUUID,subtle:typeof crypto.subtle})),{secure:false,uuid:'undefined',subtle:'undefined'});
    await page.locator('#engineToken').fill(process.env.CLIPCANVAS_QA_TOKEN);
    await page.locator('#connectButton').click();await page.waitForFunction(()=>connected&&window.ClipBrowser);
    await page.locator('#assetSelect').selectOption(manifest.source_id);await page.locator('#openAsset').click();
    await page.waitForFunction(id=>state.sourceId===id&&video.readyState>=2&&!busy(),manifest.source_id);
    await page.evaluate(()=>{setCanvas(640,360);state.background.enabled=false;Object.assign(state.geometry.video,{x:0,y:0,w:640,h:360,fit:'stretch'});state.ranges=[{start:0,end:8}];state.splitMode='ranges';state.banners=[];state.subtitles.enabled=false;state.advertising.enabled=false;syncControls();renderParts();changed();});
    await route('design');
    await page.locator('.banner-library>summary').click();
    const add = page.locator('.banner-template-card').filter({hasText:'HTTP QA banner'}).getByRole('button',{name:'Добавить',exact:true});
    await add.click();
    await page.waitForFunction(()=>state.banners.length===1);uuid(await page.evaluate(()=>state.banners[0].id));
    await page.locator('#addBannerFile').setInputFiles(manifest.banner_file);await page.waitForFunction(()=>state.banners.length===2);
    uuid(await page.evaluate(()=>state.banners[1].id));
    passed.push('HTTP template insertion and file upload create UUIDs');

    await route('design');await openParents('#proText');await page.locator('#proText').fill('HTTP CTA');
    await page.locator('#proTextEnd').fill('8');await page.locator('#proSaveText').click();
    await page.waitForFunction(()=>state.pro.text_layers.length===1);uuid(await page.evaluate(()=>state.pro.text_layers[0].id));
    passed.push('Independent text layer creates an HTTP UUID');

    await page.evaluate(()=>{state.banners=[];changed();});await route('edit');
    await page.locator('#autoBannerTemplate').selectOption(manifest.template_id);
    for(const id of ['autoBanners','autoIntro','autoOutro','autoSubtitles'])await page.locator('#'+id).uncheck();
    await page.locator('#autoBackground').check();
    const snapshot = await page.evaluate(()=>ClipWorkflow.automaticSnapshot());
    await page.locator('#runAutomatic').click();await page.locator('#briefDialog').waitFor({state:'visible'});
    assert.equal(await page.locator('#briefInstructions-0').inputValue(),'Сохранить размер, не перекрывать субтитры.');
    assert.equal(automaticRequests.length,0,'Preset cannot start without explicit confirmation');
    await page.locator('#briefPosition-0').selectOption('center');
    await page.locator('#briefCancel').click();await page.waitForFunction(()=>!$('briefDialog').open&&!$('runAutomatic').disabled);
    assert.equal(await page.evaluate(()=>ClipWorkflow.automaticSnapshot()),snapshot);assert.equal(automaticRequests.length,0);
    passed.push('Every automatic run asks for preset instructions; cancel preserves project and starts no job');
    const response = page.waitForResponse(r=>r.url().endsWith('/api/ai/automatic')&&r.request().method()==='POST');
    await page.locator('#runAutomatic').click();await page.locator('#briefDialog').waitFor({state:'visible'});
    await page.locator('#briefInstructions-0').fill('Сохранить размер. Баннер по центру холста.');
    await page.locator('#briefPosition-0').selectOption('center');await page.locator('#briefSpeed-0').selectOption('2');
    await page.locator('#briefSound-0').check();await page.locator('#briefVolume-0').evaluate(node=>{node.value='.6';node.dispatchEvent(new Event('input'));});
    await page.waitForFunction(()=>{const p=document.querySelector('.brief-preview');return p.readyState>=1&&p.playbackRate===2&&!p.muted&&p.volume===.6;});
    for(const width of [1536,736,320]){await page.setViewportSize({width,height:1050});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);await page.screenshot({path:path.join(manifest.directory,'brief-'+width+'.png'),fullPage:true});}
    await page.setViewportSize({width:1536,height:1050});await page.locator('#briefConfirm').click();const reply=await response;
    assert.equal(reply.status(),200,await reply.text());
    const payload=reply.request().postDataJSON();uuid(payload.project.banners[0].id);
    assert.equal(payload.project.banners[0].geometry.y,140);assert.equal(payload.project.banners[0].speed,2);assert.equal(payload.project.banners[0].volume,.6);
    assert.equal(payload.client_snapshot,createHash('sha256').update(snapshot).digest('hex'));
    const created=await reply.json(), done=await waitJob(created.id);
    await page.waitForFunction(id=>state.editor.automaticJobId===id,created.id);
    assert.equal(await page.evaluate(()=>state.banners[0].chromakey.enabled),true);
    assert.equal(await page.evaluate(()=>state.banners[0].geometry.y),140);assert.ok(done.automatic_report.some(row=>row.fixed_position));
    passed.push('Collect automatically submits, processes and applies a real template with an HTTP fingerprint');
    await route('export');await page.locator('#sampleMode').selectOption('range');
    await page.locator('#sampleStart').fill('0');await page.locator('#sampleEnd').fill('2');
    const sampleResponse=page.waitForResponse(r=>r.url().endsWith('/api/sample')&&r.request().method()==='POST');
    await page.locator('#renderOne').click();const sampleReply=await sampleResponse;assert.equal(sampleReply.status(),200,await sampleReply.text());
    const sample=await waitJob((await sampleReply.json()).id), mp4=sample.files.find(f=>f.name.endsWith('.mp4'));
    assert.ok(mp4&&Math.abs(mp4.duration-2)<.1);const download=await page.request.get(manifest.base+mp4.url);assert.equal(download.status(),200);fs.writeFileSync(path.join(manifest.directory,'confirmed-sample.mp4'),await download.body());
    await page.waitForFunction(()=>!busy()&&!jobStarting);
    passed.push('A real two-second MP4 exports and downloads with the automatic video template on HTTP');
    await page.evaluate(async job=>{state.banners[0].name='Manual edit after job';changed();await ClipWorkflow.applyAutomatic({...job,id:'stale-http-result'});},done);
    assert.equal(await page.evaluate(()=>state.banners[0].name),'Manual edit after job');
    assert.match(await page.locator('#automaticStatus').textContent(),/Проект изменился/);
    passed.push('An outdated automatic result preserves subsequent manual edits on HTTP');

    await page.locator('#workMode').selectOption('automatic');
    for(const id of ['batchBanner','batchInputDirectory','batchReviewFirst','batchRemoveBackground','batchCheck','batchZip'])await openParents('#'+id);
    await page.locator('#batchBanner').selectOption(manifest.template_id);
    await page.locator('#batchInputDirectory').fill(manifest.episodes);
    await page.locator('#batchReviewFirst').check();
    for(const id of ['batchRemoveBackground','batchCheck','batchZip'])await page.locator('#'+id).uncheck();
    const batchResponse=page.waitForResponse(r=>r.url().endsWith('/api/batch')&&r.request().method()==='POST');
    await page.locator('#batchStart').click();await page.locator('#briefDialog').waitFor({state:'visible'});
    assert.match(await page.locator('#briefSummary').textContent(),/После подтверждения весь пакет/);
    await page.locator('#briefPosition-0').selectOption('center');await page.locator('#briefSpeed-0').selectOption('1.5');await page.locator('#briefSound-0').check();
    await page.locator('#briefConfirm').click();const batchReply=await batchResponse;
    assert.equal(batchReply.status(),200,await batchReply.text());uuid(batchReply.request().postDataJSON().config.banners[0].id);
    const batch=await waitJob((await batchReply.json()).id);assert.equal(batch.batch_items[0].status,'prepared');
    assert.equal(batchReply.request().postDataJSON().config.banners[0].speed,1.5);
    passed.push('Automatic folder mode creates a template UUID and prepares a real project on HTTP');
    await page.waitForFunction(()=>!busy()&&!jobStarting);await page.locator('#workMode').selectOption('manual');await route('design');
    await page.evaluate(()=>{const b=state.banners[0];b.geometry.y=20;b.instructions='Баннер по центру холста.';b.layout_rules.zone='any';ClipEditor.selectBanner(b.id);changed();});
    await openParents('#adaptBanner');const manualResponse=page.waitForResponse(r=>r.url().endsWith('/api/ai/banner-layout')&&r.request().method()==='POST');
    await page.locator('#adaptBanner').click();await page.locator('#briefDialog').waitFor({state:'visible'});
    assert.equal(await page.locator('#briefPosition-0').inputValue(),'center');await page.locator('#briefConfirm').click();
    const manualReply=await manualResponse;assert.equal(manualReply.status(),200,await manualReply.text());
    const manual=await waitJob((await manualReply.json()).id);assert.equal(manual.geometry.y,140);assert.equal(manual.fixed_position,true);
    await page.locator('#applyBannerLayout').waitFor({state:'visible'});await page.locator('#applyBannerLayout').click();
    assert.equal(await page.evaluate(()=>state.banners[0].geometry.y),140);
    passed.push('Manual banner placement also confirms instructions and applies exact centre without a model');
    assert.deepEqual(errors,[]);await page.screenshot({path:path.join(manifest.directory,'http-workflow.png'),fullPage:true});
    fs.writeFileSync(path.join(manifest.directory,'result.json'),JSON.stringify({passed,errors,automatic_job:created.id,batch_id:batch.id},null,2));
    console.log(JSON.stringify({passed,errors},null,2));
  } catch(error) {await page.screenshot({path:path.join(manifest.directory,'failure.png'),fullPage:true}).catch(()=>{});console.error('UI state',await page.evaluate(()=>({toast:$('toast').textContent,status:$('automaticStatus').textContent})).catch(()=>null));throw error;}
  finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
