'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPCANVAS_PLAYWRIGHT || 'playwright');
(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const out=path.join(manifest.directory,'ui-banner-cleanup');fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1536,height:1150}}),errors=[],passed=[];
  page.on('pageerror',error=>errors.push(error.message));
  const idle=()=>page.waitForFunction(()=>!busy()&&!restoring&&!jobStarting,null,{timeout:120000});
  const select=async index=>{await page.locator('#bannerPart').selectOption(String(index));await page.waitForTimeout(500);};
  const confirm=async(id,accept=true)=>{await page.locator('#'+id).click();await page.locator('#settingsResetDialog').waitFor({state:'visible'});await page.locator(accept?'#settingsResetConfirm':'#settingsResetCancel').click();await page.locator('#settingsResetDialog').waitFor({state:'hidden'});await page.waitForTimeout(550);};
  async function waitJob(job){
    const deadline=Date.now()+120000;
    while(Date.now()<deadline){const result=await page.evaluate(id=>api('/api/jobs/'+id),job.id);if(result.status==='error')throw Error(result.error);if(result.status==='done')return result;await page.waitForTimeout(300);}
    throw Error('Export timed out');
  }
  async function pixels(file,times,y=25){
    const response=await page.request.get(manifest.base+file.url);assert.equal(response.status(),200);
    fs.writeFileSync(path.join(out,file.name),await response.body());
    return page.evaluate(async({url,times,y})=>{
      const player=document.createElement('video');player.muted=true;player.src=url;player.hidden=true;document.body.append(player);
      try{
        await new Promise((resolve,reject)=>{player.onloadeddata=resolve;player.onerror=()=>reject(Error('MP4 decode failed'));});
        const frame=document.createElement('canvas');frame.width=player.videoWidth;frame.height=player.videoHeight;
        const context=frame.getContext('2d'),result=[];
        for(const time of times){await new Promise(resolve=>{player.onseeked=resolve;player.currentTime=time;});context.drawImage(player,0,0);result.push([...context.getImageData(25,y,1,1).data].slice(0,3));}return result;
      }finally{player.removeAttribute('src');player.load();player.remove();}
    },{url:manifest.base+file.url,times,y});
  }
  const red=rgb=>rgb[0]>170&&rgb[2]<90,blue=rgb=>rgb[2]>150&&rgb[0]<90;
  try{
    await page.goto(manifest.base);await page.waitForFunction(()=>connected&&window.ClipUsability);
    await page.locator('#videoFile').setInputFiles(path.join(manifest.directory,'long-source.mp4'));await idle();
    await page.evaluate(()=>{setCanvas(240,300);state.splitMode='ranges';state.ranges=[{start:0,end:16},{start:16,end:24}];state.cuts=[];state.background.enabled=false;state.encoder='cpu';state.fps=24;state.subtitles.enabled=false;state.advertising.enabled=false;Object.assign(state.geometry.video,{x:0,y:0,w:240,h:300,fit:'stretch'});syncControls();renderParts();changed();});
    await page.locator('[data-route="design"]').click();await page.locator('#addBannerFile').setInputFiles(path.join(manifest.directory,'moving-banner.mp4'));await idle();
    const ids=await page.evaluate(()=>{const first=state.banners[0];first.name='Показ с 5 секунды';Object.assign(first,{start:0,end:2,timebase:'source',schedule:{mode:'interval',start:5,duration:2,interval:8,clips:[]}});Object.assign(first.geometry,{x:20,y:20,w:100,h:50,fit:'stretch'});const second=structuredClone(first);second.id=ClipBrowser.randomUUID();second.name='Старый баннер с 13 секунды';second.schedule={mode:'each',start:13,duration:2,clips:[]};second.geometry.y=90;state.banners.push(second);state.pro.clip_overrides={1:{banners:structuredClone(state.banners),video:structuredClone(state.geometry.video)},2:{banners:structuredClone(state.banners)}};ClipEditor.selectBanner(first.id);renderParts();changed();return [first.id,second.id];});
    await select(0);await page.locator('#clipBannerManager').scrollIntoViewIfNeeded();
    assert.match(await page.locator('#clipBannerCount').textContent(),/Показываются: 2/);
    const first=page.locator('#clipBannerList [data-banner-id="'+ids[0]+'"]'),second=page.locator('#clipBannerList [data-banner-id="'+ids[1]+'"]');
    await first.getByRole('button',{name:'Удалить показ 00:13.000',exact:true}).click();await second.getByRole('button',{name:'Удалить из клипа',exact:true}).click();await page.waitForTimeout(550);
    assert.deepEqual(await page.evaluate(()=>ClipPro.previewBanners().map(b=>[b.start,b.end])),[[5,7]]);
    await page.screenshot({path:path.join(out,'clip-banners-desktop.png')});
    passed.push('Отдельный повтор на 13-й секунде и старое размещение удаляются, показ 5–7 с остаётся');
    await page.evaluate(()=>saveProject(true));const project=await page.evaluate(()=>state.projectId),source=await page.evaluate(()=>state.sourceId);
    await page.evaluate(async id=>applyProject(await api('/api/projects/'+id)),project);await idle();await select(0);
    const full=await waitJob(await page.evaluate(()=>post('/api/render',renderConfig(false))));
    const files=full.files.filter(file=>file.name.endsWith('.mp4'));assert.equal(files.length,2);
    const firstPixels=await pixels(files[0],[5.5,13.5]);assert.ok(red(firstPixels[0])&&blue(firstPixels[1]),JSON.stringify(firstPixels));
    assert.ok((await pixels(files[0],[13.5],95)).every(blue));assert.ok((await pixels(files[1],[5.5])).every(red));
    passed.push('Сохранение/открытие и настоящий MP4 не возвращают удалённые показы; соседний клип сохраняет баннер');
    await confirm('clearClipBanners',false);assert.equal(await page.evaluate(()=>ClipPro.previewBanners().length),1);
    await confirm('clearClipBanners');assert.deepEqual(await page.evaluate(()=>ClipPro.previewBanners()),[]);
    await page.keyboard.press('Control+z');await page.waitForTimeout(550);assert.equal(await page.evaluate(()=>ClipPro.previewBanners().length),1);
    await confirm('clearClipBanners');await page.evaluate(()=>saveProject(true));
    await page.evaluate(async id=>applyProject(await api('/api/projects/'+id)),project);await idle();await select(0);assert.deepEqual(await page.evaluate(()=>ClipPro.previewBanners()),[]);
    await select(1);assert.equal(await page.evaluate(()=>ClipPro.previewBanners().length),1);await select(0);
    assert.equal(await page.locator('#bannerClipRepeat').isChecked(),false);
    await page.locator('#bannerClipStart').fill('00:05');await page.locator('#bannerClipEnd').fill('00:07');await page.locator('#bannerClipApply').click();await page.waitForTimeout(550);
    assert.deepEqual(await page.evaluate(()=>ClipPro.previewBanners().map(b=>[b.start,b.end])),[[5,7]]);
    await confirm('clearClipBanners');
    passed.push('Все баннеры удаляются только из выбранного клипа: подтверждение, отмена, Ctrl+Z и устойчивое отключение после открытия');
    await page.locator('#bannerSelect').selectOption(ids[1]);await page.locator('#removeBanner').click();await page.waitForTimeout(550);
    assert.equal(await page.evaluate(id=>state.banners.some(b=>b.id===id)||Object.values(state.pro.clip_overrides).some(o=>o.banners?.some(b=>b.id===id)||o.banner_schedules?.[id]),ids[1]),false);
    await confirm('clearProjectBanners',false);assert.equal(await page.evaluate(()=>state.banners.length),1);
    await confirm('clearProjectBanners');assert.equal(await page.evaluate(()=>state.banners.length),0);
    assert.ok(await page.evaluate(()=>Object.values(state.pro.clip_overrides).every(o=>!o.banners&&!o.banner_schedules&&o.banners_enabled===undefined&&!o.hidden_banner_ids)));
    assert.ok(await page.evaluate(()=>Boolean(state.pro.clip_overrides[1].video)));
    await page.keyboard.press('Control+z');await page.waitForTimeout(550);assert.equal(await page.evaluate(()=>state.banners.length),1);
    passed.push('Удаление из всего проекта очищает копии и расписания; очистка баннеров сохраняет другие настройки и отменяется');
    await page.evaluate(async id=>{await activateAdvert(await api('/api/assets/'+id),true);state.advertising.enabled=true;state.advertising.profile='custom';state.advertising.start=2;state.advertising.repeat=false;state.cuts=[{start:1,end:2}];state.subtitles.enabled=true;state.subtitles.cues=[{start:0,end:1,text:'Ручной текст',manual:true}];state.pro.audio.source_volume=.3;state.pro.delivery.max_duration=1;state.editor.bannerProposal={old:true};state.speechLanguage='en';state.speechModel='tiny';state.quality='high';syncControls();renderParts();changed();await saveProject(true);},manifest.advert_id);
    const before=await page.evaluate(()=>JSON.stringify(projectData().settings));
    await confirm('resetProjectSettings',false);assert.equal(await page.evaluate(()=>JSON.stringify(projectData().settings)),before);
    await page.locator('#resetProjectSettings').click();await page.locator('#settingsResetDialog').waitFor({state:'visible'});await page.screenshot({path:path.join(out,'reset-project-dialog.png')});await page.locator('#settingsResetConfirm').click();await page.locator('#settingsResetDialog').waitFor({state:'hidden'});await page.waitForTimeout(600);
    async function checkReset(){assert.deepEqual(await page.evaluate(()=>({projectId:state.projectId,sourceId:state.sourceId,banners:state.banners,cuts:state.cuts,ranges:state.ranges,subtitles:state.subtitles.cues,subtitlesEnabled:state.subtitles.enabled,advertId:state.advertId,advertising:state.advertising.enabled,overrides:state.pro.clip_overrides,audio:state.pro.audio.source_volume,speechLanguage:state.speechLanguage,speechModel:state.speechModel,quality:state.quality,canvas:[state.width,state.height]})),{projectId:project,sourceId:source,banners:[],cuts:[],ranges:[{start:0,end:24}],subtitles:[],subtitlesEnabled:false,advertId:null,advertising:false,overrides:{},audio:1,speechLanguage:'ru',speechModel:'base',quality:'standard',canvas:[1080,1920]});}
    await checkReset();await page.keyboard.press('Control+z');await page.waitForTimeout(600);
    assert.equal(await page.evaluate(()=>JSON.stringify(projectData().settings).replace(/"projectContext":"[^"]+"/,'')),before.replace(/"projectContext":"[^"]+"/,''));
    assert.ok(await page.evaluate(()=>advertAsset&&state.advertId===advertAsset.id));
    await page.keyboard.press('Control+Shift+z');await page.waitForTimeout(600);await checkReset();await page.evaluate(()=>saveProject(true));
    await page.reload();await page.waitForFunction(()=>connected&&!restoring&&state.sourceId);await page.waitForTimeout(600);await checkReset();
    passed.push('Полный сброс: подтверждение/отмена, исходник и ID сохраняются, удаляются все настройки; Ctrl+Z/повтор и обновление страницы работают');
    const clean=await waitJob(await page.evaluate(()=>{const config=renderConfig(true);config.parts=[{start:0,end:4}];config.canvas={width:240,height:300};config.video={x:0,y:0,w:240,h:300,fit:'stretch'};config.background.enabled=false;config.encoder='cpu';return post('/api/sample',config);}));
    assert.ok((await pixels(clean.files.find(file=>file.name.endsWith('.mp4')),[1.5,3.5])).every(blue));
    for(const width of [736,320]){await page.setViewportSize({width,height:1150});await page.locator('[data-route="design"]').click();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(out,'empty-banners-'+width+'.png'),fullPage:true});}
    assert.deepEqual(errors,[]);
    const report={passed,errors,project_id:project,full_job_id:full.id,clean_job_id:clean.id,first_clip_pixels:firstPixels};fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
