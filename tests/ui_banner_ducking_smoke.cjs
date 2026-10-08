'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPCANVAS_PLAYWRIGHT || 'playwright');
(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const out=path.join(manifest.directory,'ui-banner-ducking');fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1536,height:1050}}),errors=[],passed=[];
  page.on('pageerror',error=>errors.push(error.message));
  const idle=()=>page.waitForFunction(()=>!busy()&&!restoring&&!jobStarting,null,{timeout:120000});
  const seek=async second=>{await page.evaluate(t=>{video.currentTime=t;requestDraw();},second);await page.waitForFunction(t=>!video.seeking&&Math.abs(video.currentTime-t)<.05,second);await page.waitForTimeout(100);};
  const gain=async expected=>{await page.waitForFunction(value=>Math.abs(video.volume-value)<.001,expected);};
  async function waitJob(job){
    const deadline=Date.now()+120000;
    while(Date.now()<deadline){const result=await page.evaluate(id=>api('/api/jobs/'+id),job.id);if(result.status==='error')throw Error(result.error);if(result.status==='done')return result;await page.waitForTimeout(250);}
    throw Error('MP4 export timed out');
  }
  try{
    await page.goto(manifest.base);await page.waitForFunction(()=>connected&&window.ClipUsability);
    await page.locator('#videoFile').setInputFiles(path.join(manifest.directory,'pro-export','halves.mp4'));await idle();
    await page.evaluate(()=>{setCanvas(240,300);state.splitMode='ranges';state.ranges=[{start:0,end:3}];state.encoder='cpu';state.fps=24;state.subtitles.enabled=false;state.background.enabled=false;state.pro.audio.limiter=false;syncControls();renderParts();changed();previewPart(0);});
    await page.locator('[data-route="design"]').click();
    await page.locator('#addBannerFile').setInputFiles(path.join(manifest.directory,'pro-export','banner.png'));await idle();
    await page.evaluate(()=>{state.banners[0].schedule={mode:'interval',start:.5,duration:.5,interval:1.5};ClipEditor.selectBanner(state.banners[0].id);changed();});
    await page.locator('#bannerFlowSound').click();await page.waitForTimeout(250);
    assert.equal(await page.locator('#proDuckTrigger').inputValue(),'visible');
    assert.equal(await page.locator('#proDuckVolume').isDisabled(),true);
    await page.locator('#proDucking').check();await page.waitForTimeout(250);
    await seek(.7);await gain(.25);await seek(1.2);await gain(1);await seek(2.2);await gain(.25);
    await page.locator('#previewVolume').evaluate(node=>{node.value='.5';node.dispatchEvent(new Event('input',{bubbles:true}));});await gain(.125);
    await page.locator('#proDuckTrigger').selectOption('sounding');await gain(.5);
    await page.locator('#proDuckTrigger').selectOption('visible');await gain(.125);
    await page.locator('#proDuckVolume').evaluate(node=>{ClipStudio.checkpoint();node.value='40';node.dispatchEvent(new Event('input',{bubbles:true}));});await gain(.2);
    await page.locator('body').click({position:{x:800,y:145}});await page.keyboard.press('Control+z');await gain(.125);
    passed.push('Картинка и повторы приглушают видео только во время показа; режим со звуком, уровень, общая громкость и Ctrl+Z работают');
    await page.evaluate(()=>saveProject(true));const project=await page.evaluate(()=>state.projectId);
    await page.reload();await idle();await page.waitForFunction(()=>state.banners.length===1&&ClipVideoBanners.info(state.banners[0].asset_id));
    await page.locator('[data-route="design"]').click();await page.evaluate(()=>previewPart(0));await seek(.7);
    await gain(.25);assert.equal(await page.locator('#proDucking').isChecked(),true);assert.equal(await page.locator('#proDuckTrigger').inputValue(),'visible');
    await page.locator('#bannerFlowSound').click();await page.waitForTimeout(250);
    await page.screenshot({path:path.join(out,'ducking-desktop.png')});
    await page.locator('#addBannerFile').setInputFiles(manifest.advert_fixture);await idle();
    await page.evaluate(()=>{const banner=state.banners.at(-1);Object.assign(banner,{volume:.4,loop:true,schedule:{mode:'each',start:.5,duration:1}});ClipEditor.selectBanner(banner.id);previewPart(0);changed();});
    await page.waitForFunction(()=>ClipVideoBanners.get(ClipPro.previewBanners().at(-1))?.readyState>=2);
    await page.locator('#previewVolume').evaluate(node=>{node.value='.5';node.dispatchEvent(new Event('input',{bubbles:true}));});
    await seek(.7);await gain(.125);
    assert.ok(await page.evaluate(()=>Math.abs(ClipVideoBanners.get(ClipPro.previewBanners().at(-1)).volume-.2)<.001));
    await page.evaluate(()=>{state.banners.at(-1).loop=false;state.banners.at(-1).schedule.duration=2;changed();});
    await seek(1.8);await gain(.5);await seek(2.2);await gain(.125);
    passed.push('Настройка сохраняется после открытия; звук баннера не приглушается, конец незапетлённого видео возвращает исходную громкость');
    await page.evaluate(()=>{state.banners=state.banners.slice(0,1);state.pro.clip_overrides={1:{banners_enabled:false}};changed();});
    await seek(.7);await gain(.5);
    await page.evaluate(()=>{state.pro.clip_overrides={};state.pro.audio={ducking:true,duck_volume:.25,limiter:false};ClipPro.projectRestored();changed();});
    await gain(.5);assert.equal(await page.locator('#proDuckTrigger').inputValue(),'sounding');
    await page.locator('#proDuckTrigger').selectOption('visible');await gain(.125);
    await page.evaluate(()=>saveProject(true));
    const sample=await waitJob(await page.evaluate(()=>post('/api/sample',renderConfig(true))));
    const full=await waitJob(await page.evaluate(()=>post('/api/render',renderConfig(false))));
    for(const [label,job] of [['sample',sample],['full',full]]){
      assert.equal(job.request.audio.duck_trigger,'visible');assert.equal(job.request.audio.duck_volume,.25);
      const file=job.files.find(file=>file.name.endsWith('.mp4'));assert.ok(file);
      const response=await page.request.get(manifest.base+file.url);assert.equal(response.status(),200);fs.writeFileSync(path.join(out,label+'.mp4'),await response.body());
    }
    passed.push('Удалённые баннеры не приглушают звук; старое приглушение сохраняет режим; пробный и полный MP4 используют выбранный уровень');
    for(const width of [736,320]){
      await page.setViewportSize({width,height:1050});await page.waitForTimeout(250);await page.locator('#bannerDucking').scrollIntoViewIfNeeded();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
      await page.screenshot({path:path.join(out,'ducking-'+width+'.png'),fullPage:true});
    }
    assert.deepEqual(errors,[]);
    const result={passed,errors,project_id:project,sample_job_id:sample.id,full_job_id:full.id};
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
