'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPCANVAS_PLAYWRIGHT||'playwright');
(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),folder=path.join(manifest.directory,'banner-preset');
  const seeded=JSON.parse(fs.readFileSync(path.join(folder,'result.json'),'utf8'));
  const out=path.join(manifest.directory,'ui-banner-preset');fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1536,height:1050}}),errors=[],passed=[];page.on('pageerror',error=>errors.push(error.message));
  const idle=()=>page.waitForFunction(()=>!busy()&&!restoring&&!jobStarting,null,{timeout:120000});
  try{
    await page.goto(manifest.base);await page.waitForFunction(()=>connected&&document.querySelector('#bannerPreset option[value="bubavpn"]')&&!document.querySelector('#bannerPreset option[value="bubavpn"]').disabled);
    assert.equal(await page.evaluate(()=>state.banners.length),0);
    await page.locator('#videoFile').setInputFiles(path.join(folder,'long-source.mp4'));await idle();
    await page.evaluate(async asset=>{state.splitMode='ranges';state.ranges=[{start:0,end:8}];state.pro.audio.source_volume=.6;state.subtitles.enabled=false;
      const old={id:'old-banner',name:'Старый баннер',enabled:true,asset_id:asset,start:0,end:1,volume:.3,loop:true,schedule:{mode:'each',start:0,duration:1},geometry:{x:20,y:20,w:100,h:50,fit:'stretch'},chromakey:{enabled:false}};
      await ClipEditor.loadBannerImage(old);state.banners.push(old);ClipEditor.renderBannerList();syncControls();renderParts();changed();previewPart(0);
    },seeded.asset_id);
    const before=await page.evaluate(()=>structuredClone({old:state.banners[0],pro:state.pro,subtitles:state.subtitles,advertising:state.advertising,video:state.geometry.video,ranges:state.ranges}));
    await page.locator('[data-route="design"]').click();await page.locator('#bannerPreset').selectOption('bubavpn');
    await page.waitForFunction(()=>state.banners.length===2&&state.banners[1].schedule.mode==='bubavpn'&&ClipVideoBanners.info(state.banners[1].asset_id));await idle();
    assert.deepEqual(await page.evaluate(()=>structuredClone({old:state.banners[0],pro:state.pro,subtitles:state.subtitles,advertising:state.advertising,video:state.geometry.video,ranges:state.ranges})),before);
    const config=await page.evaluate(()=>state.banners[1]);assert.equal(config.volume,1);assert.equal(config.loop,false);assert.equal(config.locked,true);assert.equal(config.chromakey.enabled,false);
    const area=await page.evaluate(()=>{const g=state.banners[1].geometry;return [g.w*g.h/(state.width*state.height),g.x+g.w/2,g.y+g.h/2,state.width/2,state.height/2];});
    assert.ok(area[0]>=.25);assert.ok(Math.abs(area[1]-area[3])<.00001&&Math.abs(area[2]-area[4])<.00001);
    await page.locator('#bannerPreset').selectOption('');await page.locator('#bannerPreset').selectOption('bubavpn');await page.waitForTimeout(350);assert.equal(await page.evaluate(()=>state.banners.length),2);
    passed.push('Выбор БубаVPN добавляет готовый баннер с озвучкой и прозрачным фоном, сохраняет остальные настройки и не создаёт дубль');
    for(const [duration,count] of [[119.9,1],[120,2],[180,3]]){
      const shows=await page.evaluate(duration=>{state.ranges=[{start:0,end:duration}];renderParts();previewPart(0);changed();return ClipPro.previewBanners().filter(banner=>banner.name==='БубаVPN').map(banner=>[banner.start,banner.end]);},duration);
      assert.equal(shows.length,count);assert.ok(Math.abs((shows[0][0]+shows[0][1])/2-duration/2)<.00001);
    }
    for(const [width,height] of [[1920,1080],[1080,1920]]){
      const box=await page.evaluate(({width,height})=>{setCanvas(width,height);const g=state.banners[1].geometry;return [g.w*g.h/(width*height),g.x+g.w/2-width/2,g.y+g.h/2-height/2];},{width,height});
      assert.ok(box[0]>=.25&&Math.abs(box[1])<.00001&&Math.abs(box[2])<.00001);
    }
    passed.push('Показы 1/2/3 следуют полной длительности клипа; центр и площадь сохраняются при смене формата');
    await page.evaluate(()=>{state.ranges=[{start:0,end:8}];renderParts();previewPart(0);changed();});
    await page.evaluate(()=>{$('seek').value=4;$('seek').oninput();});
    await page.waitForFunction(()=>{const banner=ClipPro.previewBanners().find(b=>b.name==='БубаVPN'),media=ClipVideoBanners.get(banner);return media?.readyState>=2&&!media.seeking&&ClipVideoBanners.timing(banner).visible;});
    await page.waitForTimeout(250);
    const pixel=await page.evaluate(()=>[...ctx.getImageData(Math.floor(canvas.width/2),Math.floor(canvas.height/2),1,1).data]);assert.ok(pixel[1]>80&&pixel[2]<30,pixel);
    await page.locator('#bannerPreset').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'preset-desktop.png')});
    await page.evaluate(()=>saveProject(true));const project=await page.evaluate(()=>state.projectId);
    await page.reload();await idle();await page.waitForFunction(()=>state.banners.length===2&&state.banners[1].schedule.mode==='bubavpn');
    assert.equal(await page.evaluate(()=>state.banners[1].volume),1);assert.equal(await page.evaluate(()=>state.pro.audio.source_volume),.6);
    await page.locator('[data-route="design"]').click();await page.evaluate(()=>ClipEditor.selectBanner(state.banners[1].id));
    await page.locator('#removeBanner').click();await page.waitForFunction(()=>state.banners.length===1);
    await page.locator('body').click({position:{x:800,y:145}});await page.keyboard.press('Control+z');await page.waitForFunction(()=>state.banners.length===2);
    for(const width of [736,320]){await page.setViewportSize({width,height:1050});await page.waitForTimeout(250);await page.locator('#bannerPresets').scrollIntoViewIfNeeded();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(out,'preset-'+width+'.png'),fullPage:true});}
    assert.deepEqual(errors,[]);passed.push('Проект сохраняет преднастройку; удаление и Ctrl+Z работают; 1536/736/320 px без ошибок и переполнения');
    const result={passed,errors,project_id:project};fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
