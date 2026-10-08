'use strict';
// A legacy source-anchored banner, two different clips, and real MP4 decoding.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPCANVAS_PLAYWRIGHT || 'playwright');
(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const out=path.join(manifest.directory,'ui-banner-time');fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1536,height:1050}}),errors=[],passed=[];
  page.on('pageerror',error=>errors.push(error.message));
  const idle=()=>page.waitForFunction(()=>!busy()&&!restoring&&!jobStarting,null,{timeout:120000});
  const route=key=>page.locator('[data-route="'+key+'"]').click();
  const select=async index=>{await page.locator('#bannerPart').selectOption(String(index));await page.waitForTimeout(500);};
  const apply=async(start,end)=>{await page.locator('#bannerClipStart').fill(start);await page.locator('#bannerClipEnd').fill(end);await page.locator('#bannerClipApply').click();await page.waitForTimeout(500);};
  const isRed=rgb=>rgb[0]>170&&rgb[1]<90&&rgb[2]<90;
  const isBlue=rgb=>rgb[2]>150&&rgb[0]<90;
  async function waitJob(job){
    const deadline=Date.now()+120000;
    while(Date.now()<deadline){
      const result=await page.evaluate(id=>api('/api/jobs/'+id),job.id);
      if(result.status==='error')throw Error(result.error);
      if(result.status==='done'){assert.ok(result.files.some(file=>file.name.endsWith('.mp4')),JSON.stringify(result));return result;}
      await page.waitForTimeout(250);
    }
    throw Error('Export timed out: '+job.id);
  }
  async function pixels(file,times){
    const response=await page.request.get(manifest.base+file.url);assert.equal(response.status(),200);
    fs.writeFileSync(path.join(out,file.name),await response.body());
    return page.evaluate(async({url,times})=>{
      const player=document.createElement('video');player.muted=true;player.preload='auto';player.src=url;document.body.append(player);player.hidden=true;
      try{
        await new Promise((resolve,reject)=>{player.onloadeddata=resolve;player.onerror=()=>reject(Error('MP4 decode failed'));});
        const frame=document.createElement('canvas');frame.width=player.videoWidth;frame.height=player.videoHeight;
        const context=frame.getContext('2d'),result=[];
        for(const time of times){await new Promise(resolve=>{player.onseeked=resolve;player.currentTime=time;});context.drawImage(player,0,0);result.push([...context.getImageData(25,25,1,1).data].slice(0,3));}
        return result;
      }finally{player.removeAttribute('src');player.load();player.remove();}
    },{url:manifest.base+file.url,times});
  }
  try{
    await page.goto(manifest.base);await page.waitForFunction(()=>connected&&window.ClipUsability);
    await page.evaluate(async id=>{await activateSource(await api('/api/assets/'+id));setCanvas(240,300);state.splitMode='ranges';state.ranges=[{start:0,end:3},{start:3,end:8}];state.cuts=[{start:4,end:4.5}];state.encoder='cpu';state.fps=24;state.background.enabled=false;state.subtitles.enabled=false;state.advertising.enabled=false;state.pro.delivery={};Object.assign(state.geometry.video,{x:0,y:0,w:240,h:300,fit:'stretch'});syncControls();renderParts();changed();},manifest.source_id);
    await route('design');await page.locator('#addBannerFile').setInputFiles(path.join(manifest.directory,'moving-banner.mp4'));await idle();
    await page.evaluate(()=>{const b=state.banners[0];Object.assign(b,{timebase:'source',start:0,end:1,schedule:{mode:'source'},volume:0});Object.assign(b.geometry,{x:20,y:20,w:100,h:50,fit:'stretch'});ClipEditor.selectBanner(b.id);changed();});
    await select(1);await page.locator('#bannerFlowTiming').click();
    assert.equal(await page.locator('#bannerClipStart').inputValue(),'');assert.equal(await page.locator('#bannerClipEnd').inputValue(),'');
    assert.match(await page.locator('#bannerDuration').textContent(),/В этом клипе показа нет/);
    assert.match(await page.locator('#bannerClipContext').textContent(),/Клип 2 · исходник 00:03\.000–00:08\.000.*04\.500/);
    await page.screenshot({path:path.join(out,'no-show-desktop.png')});
    passed.push('Старая привязка вне выбранного клипа: пустые поля и объяснение вместо ложного диапазона 0–0');
    await apply('00:00.500','00:01.500');
    assert.deepEqual(await page.evaluate(()=>[state.banners[0].start,state.banners[0].end,state.banners[0].schedule.mode]),[0,1,'source']);
    assert.deepEqual(await page.evaluate(()=>ClipPro.previewBanners().map(b=>[b.start,b.end,b.timebase])),[[.5,1.5,'output']]);
    await select(0);assert.equal(await page.locator('#bannerClipStart').inputValue(),'00:00.000');assert.equal(await page.locator('#bannerClipEnd').inputValue(),'00:01.000');
    assert.equal(await page.evaluate(()=>Boolean(state.pro.clip_overrides[1]?.banner_schedules)),false);
    await select(1);assert.equal(await page.locator('#bannerClipStart').inputValue(),'00:00.500');
    passed.push('Время применяется только к выбранному отрезку; другой клип и исходная привязка сохранены');
    await page.locator('#bannerClipRepeat').check();await page.locator('#bannerClipPause').fill('00:00.800');await page.locator('#bannerClipApply').click();await page.waitForTimeout(500);
    const rounded=await page.evaluate(()=>ClipPro.previewBanners().map(b=>[b.start,b.end].map(n=>Number(n.toFixed(3)))));
    assert.deepEqual(rounded,[[.5,1.5],[2.3,3.3],[4.1,4.5]]);
    assert.match(await page.locator('#bannerClipDraft').textContent(),/Следующий: 00:02\.300–00:03\.300/);
    const before=await page.evaluate(()=>JSON.stringify(state.pro.clip_overrides));
    await apply('00:00.500','00:01.800');await page.keyboard.press('Control+z');await page.waitForTimeout(600);
    assert.equal(await page.evaluate(()=>JSON.stringify(state.pro.clip_overrides)),before);
    await page.evaluate(()=>saveProject(true));const saved=await page.evaluate(()=>state.projectId);
    await page.evaluate(async id=>applyProject(await api('/api/projects/'+id)),saved);await idle();await select(1);
    assert.equal(await page.evaluate(()=>JSON.stringify(state.pro.clip_overrides)),before);
    await page.locator('#bannerFlowTiming').click();await page.screenshot({path:path.join(out,'time-range-desktop.png')});
    passed.push('Повторы: первый диапазон, явная пауза, следующий показ; Ctrl+Z и сохранение/открытие сохраняют время');
    const sample=await waitJob(await page.evaluate(()=>{$('sampleClip').value='1';$('sampleMode').value='clip';return post('/api/sample',renderConfig(true));}));
    const samplePixels=await pixels(sample.files.find(file=>file.name.endsWith('.mp4')),[.2,.8,1.9,2.6,3.7,4.3]);
    assert.ok([isBlue,isRed,isBlue,isRed,isBlue,isRed].every((test,index)=>test(samplePixels[index])),JSON.stringify(samplePixels));
    const full=await waitJob(await page.evaluate(()=>post('/api/render',renderConfig(false))));
    const files=full.files.filter(file=>file.name.endsWith('.mp4'));assert.equal(files.length,2);
    const firstPixels=await pixels(files[0],[.2,1.5]);assert.ok(isRed(firstPixels[0])&&isBlue(firstPixels[1]),JSON.stringify(firstPixels));
    const secondPixels=await pixels(files[1],[.2,.8,1.9,2.6]);assert.ok(isBlue(secondPixels[0])&&isRed(secondPixels[1])&&isBlue(secondPixels[2])&&isRed(secondPixels[3]),JSON.stringify(secondPixels));
    passed.push('Настоящие пробный и полный MP4: пиксели подтверждают время и паузы в клипе 2; клип 1 сохраняет прежний показ');
    for(const width of [736,320]){await page.setViewportSize({width,height:1050});await page.locator('#bannerFlowTiming').click();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(out,'time-range-'+width+'.png'),fullPage:true});}
    await page.locator('#bannerClipReset').click();await page.waitForTimeout(500);assert.equal(await page.locator('#bannerClipStart').inputValue(),'');
    assert.deepEqual(errors,[]);
    const report={passed,errors,project_id:saved,sample_job_id:sample.id,full_job_id:full.id,sample_pixels:samplePixels,first_clip_pixels:firstPixels,second_clip_pixels:secondPixels};
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
