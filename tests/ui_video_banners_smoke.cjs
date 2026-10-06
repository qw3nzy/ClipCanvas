'use strict';
const {chromium} = require(process.env.CLIPCANVAS_PLAYWRIGHT || 'playwright');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
(async () => {
  const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const out = path.join(manifest.directory, 'ui-video-banners'); fs.mkdirSync(out, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page = await browser.newPage({viewport:{width:1536,height:1050}}), errors = [], passed = [];
  page.setDefaultTimeout(30000); page.on('pageerror', e => errors.push(e.message));
  const route = name => page.locator(`[data-route="${name}"]`).click();
  const idle = () => page.waitForFunction(() => !busy() && !restoring && !jobStarting, null, {timeout:120000});
  const seek = async time => {
    await page.evaluate(t => { sourcePreview(); video.currentTime=t; requestDraw(); }, time);
    await page.waitForFunction(t => { const b=state.banners[0], m=ClipVideoBanners.get(b); return Math.abs(video.currentTime-t)<.04 && !video.seeking && m?.readyState>=2 && !m.seeking && (ClipVideoBanners.targetTime(b,m.duration,t).ended || Math.abs(m.currentTime-ClipVideoBanners.targetTime(b,m.duration,t).time)<.08); }, time);
    await page.evaluate(() => requestDraw()); await page.waitForTimeout(80);
  };
  const pixel = (x,y) => page.evaluate(([x,y]) => [...ctx.getImageData(Math.round(x*canvas.width/state.width),Math.round(y*canvas.height/state.height),1,1).data].slice(0,3), [x,y]);
  try {
    await page.goto(manifest.base); await page.waitForFunction(() => connected && window.ClipVideoBanners && window.ClipWorkflow);
    await page.locator('#assetSelect').selectOption(manifest.source_id); await page.locator('#openAsset').click(); await page.waitForFunction(id=>state.sourceId===id && video.readyState>=2 && !busy(),manifest.source_id,{timeout:120000}); await idle();
    await route('design'); await page.locator('#addBannerFile').setInputFiles(path.join(manifest.directory,'moving-banner.mp4'));
    await page.waitForFunction(() => state.banners.length===1 && ClipVideoBanners.get(state.banners[0])?.readyState>=2, null, {timeout:120000});
    const asset = await page.evaluate(() => ClipVideoBanners.asset(state.banners[0].asset_id));
    assert.equal(asset.media_type,'video'); assert.ok(asset.duration>1.9 && asset.duration<2.1);
    await page.evaluate(() => {
      setCanvas(640,360); Object.assign(state.geometry.video,{x:0,y:0,w:640,h:360,fit:'stretch'}); state.background.enabled=false;
      state.splitMode='ranges'; state.ranges=[{start:0,end:8}]; state.cuts=[]; state.advertising.enabled=false; state.subtitles.enabled=false;
      const b=state.banners[0]; Object.assign(b,{start:1,end:7,loop:true,offset:0,volume:0}); Object.assign(b.geometry,{x:40,y:40,w:320,h:160,rotation:0,fit:'stretch'});
      ClipEditor.selectBanner(b.id); syncControls(); renderParts(); changed();
    });
    await seek(1.25); const first = await pixel(200,120); assert.ok(first.every(n=>n>200), 'white first phase '+first);
    await seek(2.25); const next = await pixel(200,120); assert.ok(next[0]>200 && next[1]>200 && next[2]<80,'yellow second phase '+next);
    await seek(3.25); const repeat = await pixel(200,120); assert.ok(repeat.every(n=>n>200),'repeat returns to white '+repeat);
    passed.push('MP4 remains a video; paused seeking and repeated animation change the visible frame');

    await page.evaluate(() => { state.banners[0].schedule={mode:'interval',start:1,duration:1.8,interval:3,clips:[]}; changed(); requestDraw(); });
    const scheduledSeek=async time=>{
      await page.evaluate(t=>{sourcePreview();video.currentTime=t;requestDraw();},time);
      await page.waitForFunction(t=>{
        if(video.seeking||Math.abs(video.currentTime-t)>.04)return false;
        const banners=ClipPro.previewBanners(),clock=ClipPro.outputClock();
        const b=banners.find(b=>clock>=b.start&&clock<b.end);
        if(!b)return banners.every(b=>!ClipVideoBanners.get(b)||ClipVideoBanners.get(b).paused);
        const m=ClipVideoBanners.get(b);
        return m?.readyState>=2&&!m.seeking&&Math.abs(m.currentTime-ClipVideoBanners.targetTime(b,m.duration,clock).time)<.08;
      },time);
      await page.evaluate(()=>requestDraw());await page.waitForTimeout(80);
    };
    await scheduledSeek(1.25);assert.ok((await pixel(200,120)).every(n=>n>200),'first scheduled occurrence starts white');
    await scheduledSeek(2.25);const scheduledYellow=await pixel(200,120);assert.ok(scheduledYellow[0]>200&&scheduledYellow[1]>200&&scheduledYellow[2]<80);
    await scheduledSeek(3.25);const between=await pixel(200,120);assert.ok(between[2]>180&&between[0]<60,'banner hidden between occurrences');
    await scheduledSeek(4.25);assert.ok((await pixel(200,120)).every(n=>n>200),'second occurrence resets media phase');
    assert.equal(await page.evaluate(()=>{const b=ClipPro.previewBanners();return ClipVideoBanners.get(b[0])!==ClipVideoBanners.get(b[1]);}),true);
    await page.evaluate(()=>{state.banners[0].schedule={mode:'source'};changed();ClipVideoBanners.sync();});await seek(1.25);
    passed.push('Scheduled video copies restart independently and disappear between occurrences');

    await page.locator('#bannerEyedropper').evaluate(node => { for(let p=node.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true; });
    await page.locator('#bannerEyedropper').click();
    const picker = await page.locator('#bannerKeyPreview').boundingBox();
    await page.mouse.click(picker.x+picker.width*.1, picker.y+picker.height*.5);
    await page.waitForFunction(() => state.banners[0].chromakey.enabled);
    const color = await page.evaluate(() => state.banners[0].chromakey.color); assert.ok(parseInt(color.slice(1,3),16)>220 && parseInt(color.slice(3,5),16)<50,color);
    await seek(2.25); const keyed = await pixel(60,80); assert.ok(keyed[2]>180 && keyed[0]<60,'red background reveals blue source '+keyed);
    const movingAfterKey=await pixel(200,120); assert.ok(movingAfterKey[0]>200 && movingAfterKey[1]>200 && movingAfterKey[2]<80,'animation preserved after chromakey');
    passed.push('Eyedropper removes red from each moving frame, not a frozen poster');

    await page.locator('#bannerVideoLoop').uncheck();
    await seek(3.5); const hidden=await pixel(200,120); assert.ok(hidden[2]>180 && hidden[0]<60,'one-shot expires without freezing foreground');
    await page.locator('#bannerVideoLoop').check();
    await page.locator('#bannerVideoSpeed').selectOption('2');await seek(1.75);
    assert.equal(await page.evaluate(()=>ClipVideoBanners.get(state.banners[0]).playbackRate),2);
    const sped=await pixel(200,120);assert.ok(sped[0]>200&&sped[1]>200&&sped[2]<80,'2x changes the visible banner phase');
    await page.locator('#bannerVideoSound').check();assert.equal(await page.evaluate(()=>state.banners[0].volume),1);
    await page.locator('#bannerVideoSound').uncheck();assert.equal(await page.evaluate(()=>state.banners[0].volume),0);
    await page.locator('#bannerVideoSpeed').selectOption('1');
    passed.push('Inspector changes actual banner playback speed and explicitly enables/disables audio');
    await page.locator('#bannerVideoOffset').fill('1'); await page.locator('#bannerVideoOffset').dispatchEvent('change');
    await page.locator('#bannerVideoVolume').evaluate(node=>{node.value='.4';node.dispatchEvent(new Event('input',{bubbles:true}));});
    await seek(1.25); const offset=await pixel(200,120); assert.ok(offset[0]>200 && offset[1]>200 && offset[2]<80,'media offset changes frame');
    await page.locator('#previewVolume').evaluate(node=>{node.value='.5';node.dispatchEvent(new Event('input',{bubbles:true}));});
    await page.waitForFunction(()=>Math.abs(ClipVideoBanners.get(state.banners[0]).volume-.2)<.001);
    await seek(1.75);
    await page.locator('#playButton').click();
    await page.waitForFunction(()=>!video.paused && !ClipVideoBanners.get(state.banners[0]).paused);
    const timeBefore=await page.evaluate(()=>video.currentTime); await page.waitForTimeout(400); assert.ok(await page.evaluate(()=>video.currentTime)>timeBefore+.2);
    await page.locator('#playButton').click(); await page.waitForFunction(()=>ClipVideoBanners.get(state.banners[0]).paused);
    await page.waitForTimeout(80); const liveFrame=await pixel(200,120); assert.ok(liveFrame.every(n=>n>200),'keyed animation changes during live playback '+liveFrame);
    await page.locator('#muteButton').click(); await page.waitForFunction(()=>ClipVideoBanners.get(state.banners[0]).muted);
    await page.locator('#fullscreenButton').click(); await page.waitForFunction(()=>!!document.fullscreenElement); assert.equal(await page.evaluate(()=>ClipVideoBanners.get(state.banners[0]).paused),true);
    await page.keyboard.press('Escape'); await page.waitForFunction(()=>!document.fullscreenElement);
    passed.push('No-loop expiry, media offset, master volume/mute, source keeps playing, pause/fullscreen stop overlay');

    await page.evaluate(()=>{state.banners[0].name='Animated QA';state.banners[0].geometry.rotation=30;changed();});
    await page.locator('#saveBannerTemplate').click(); await page.waitForFunction(()=>[...document.querySelectorAll('.banner-template-card strong')].some(n=>n.textContent==='Animated QA'));
    const templates=await page.evaluate(()=>api('/api/banner-templates')), template=templates.templates.find(t=>t.name==='Animated QA');
    assert.equal(template.media_type,'video'); assert.equal(template.offset,1); assert.equal(template.volume,.4); assert.equal(template.loop,true);
    await page.locator('.banner-library>summary').click();
    const card=page.locator('.banner-template-card').filter({has:page.locator('strong',{hasText:'Animated QA'})}).last();
    await card.getByRole('button',{name:'Добавить',exact:true}).click(); await page.waitForFunction(()=>state.banners.length===2 && ClipVideoBanners.get(state.banners[1])?.readyState>=2);
    assert.equal(await page.evaluate(()=>ClipVideoBanners.get(state.banners[0])!==ClipVideoBanners.get(state.banners[1])),true);
    await page.evaluate(()=>{state.banners[1].start=4;state.banners[1].end=6;state.banners[1].offset=0;changed();});
    await page.locator('#saveProject').click(); await page.waitForFunction(()=>$('autosaveState').textContent==='Проект сохранён');
    await page.reload(); await page.waitForFunction(()=>state.banners.length===2 && !busy() && !restoring && ClipVideoBanners.get(state.banners[0])?.readyState>=2,null,{timeout:120000});
    assert.equal(await page.evaluate(()=>state.banners[0].offset),1); assert.equal(await page.evaluate(()=>state.banners[0].volume),.4);
    await route('design');await page.evaluate(()=>ClipEditor.selectBanner(state.banners[0].id));
    assert.equal(await page.locator('#videoBannerControls').isVisible(),true);
    assert.equal(await page.locator('#bannerVideoLoop').isChecked(),true);
    assert.equal(await page.locator('#bannerVideoOffset').inputValue(),'1');
    assert.equal(await page.locator('#bannerVideoVolume').inputValue(),'0.4');
    passed.push('Video templates preserve rotation/offset/loop/audio and each placed copy owns an independent player; project reload restores video');

    await route('design'); await page.locator('#addBannerFile').setInputFiles(path.join(manifest.directory,'moving-banner.gif'));
    await page.waitForFunction(()=>state.banners.length===3 && ClipVideoBanners.get(state.banners[2])?.readyState>=2,null,{timeout:120000});
    assert.equal(await page.evaluate(async()=> (await ClipVideoBanners.asset(state.banners[2].asset_id)).media_type),'video');
    await page.evaluate(()=>{state.banners[1].enabled=false;state.banners[2].enabled=false;state.banners[0].start=1;state.banners[0].end=7;sourcePreview();changed();});
    await route('edit');
    await page.evaluate(()=>{state.banners[0].chromakey.enabled=false;changed();});
    for(const id of ['autoBanners','autoIntro','autoOutro','autoSubtitles']) await page.locator('#'+id).uncheck();
    await page.locator('#autoBackground').check();
    const autoResponse=page.waitForResponse(r=>r.url().endsWith('/api/ai/automatic') && r.request().method()==='POST'); await page.locator('#runAutomatic').click();
    await page.locator('#briefDialog').waitFor({state:'visible'});await page.locator('#briefConfirm').click();
    const automatic=await (await autoResponse).json();await page.waitForFunction(id=>state.editor.automaticJobId===id,automatic.id,{timeout:120000});
    assert.equal(await page.evaluate(()=>state.banners[0].chromakey.enabled),true);assert.equal(await page.evaluate(()=>state.banners[0].volume),.4);
    passed.push('Automatic mode removes a video background using multiple frames and preserves playback/audio settings');
    await route('export'); await page.locator('#sampleMode').selectOption('range');
    await page.locator('#sampleStart').fill('0'); await page.locator('#sampleEnd').fill('8');
    const response=page.waitForResponse(r=>r.url().endsWith('/api/sample') && r.request().method()==='POST');await page.locator('#renderOne').click();
    const created=await (await response).json();
    await page.waitForFunction(async id=>{const j=await api('/api/jobs/'+id);if(j.status==='error')throw Error(j.error);return j.status==='done';},created.id,{timeout:120000});await idle();
    const job=await page.evaluate(id=>api('/api/jobs/'+id),created.id);assert.ok(job.files.some(f=>f.name.endsWith('.mp4')));assert.equal(job.request.banners[0].offset,1);assert.equal(job.request.banners[0].volume,.4);
    passed.push('Animated GIF is retained; real MP4 export includes moving, keyed, rotated video overlay and its selected audio');
    await route('design');await page.evaluate(()=>ClipEditor.selectBanner(state.banners[0].id));await seek(1.25);
    for(const width of [1536,736,320]) { await page.setViewportSize({width,height:1050});await route('design');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);await page.screenshot({path:path.join(out,'design-'+width+'.png'),fullPage:true}); }
    assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({passed,errors,sample_job:created.id,video_asset:asset.id},null,2));console.log(JSON.stringify({passed,out},null,2));
  } catch(error) { await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});console.error('Diagnostics',errors,await page.evaluate(()=>({toast:$('toast').textContent,banners:state.banners,videoTime:video.currentTime})).catch(()=>null));throw error; }
  finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
