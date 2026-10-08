'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPCANVAS_PLAYWRIGHT||'playwright');
(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),folder=path.join(manifest.directory,'video-effects');
  const seeded=JSON.parse(fs.readFileSync(path.join(folder,'result.json'),'utf8'));
  const out=path.join(manifest.directory,'ui-video-effects');fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1536,height:1050}}),errors=[],passed=[];
  page.on('pageerror',error=>errors.push(error.message));
  const idle=()=>page.waitForFunction(()=>!busy()&&!restoring&&!jobStarting,null,{timeout:120000});
  const pixel=async(x,y)=>page.evaluate(([x,y])=>{draw();return [...ctx.getImageData(Math.round(x*canvas.width/state.width),Math.round(y*canvas.height/state.height),1,1).data].slice(0,3);},[x,y]);
  const slider=async(id,value)=>page.locator('#'+id).evaluate((input,value)=>{input.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));},String(value));
  const unrelated=()=>page.evaluate(()=>JSON.stringify([state.banners,state.subtitles,state.advertising,state.geometry,state.background,state.ranges,state.pro.audio]));
  try{
    await page.goto(manifest.base);await page.waitForFunction(()=>connected&&document.querySelector('#videoEffectsPanel'));
    assert.equal(await page.locator('#videoMirror').isDisabled(),true);
    await page.locator('#videoFile').setInputFiles(path.join(folder,'asymmetric-source.mp4'));await idle();
    await page.evaluate(async asset=>{
      setCanvas(240,300);state.geometry.video={x:20,y:50,w:200,h:120,fit:'stretch'};state.background={enabled:false,color:'#111827',blur:24};
      state.splitMode='ranges';state.ranges=[{start:0,end:3}];state.subtitles.enabled=false;
      const banner={id:'white',name:'Белый баннер',enabled:true,asset_id:asset,start:0,end:3,volume:0,
        geometry:{x:28,y:70,w:24,h:24,fit:'stretch'},schedule:{mode:'each',start:0,duration:3},chromakey:{enabled:false}};
      state.banners=[banner];await ClipEditor.loadBannerImage(banner);ClipEditor.renderBannerList();
      syncControls();renderParts();resizeStage();previewPart(0);video.currentTime=.5;changed();
    },seeded.banner_id);
    await page.locator('[data-route="design"]').click();await page.waitForFunction(()=>video.readyState>=2&&!video.seeking);
    const before=await unrelated();assert.ok((await pixel(60,120))[0]>200);assert.ok((await pixel(180,120))[2]>200);
    await page.locator('#videoMirror').check();assert.ok((await pixel(60,120))[2]>200);assert.ok((await pixel(180,120))[0]>200);
    assert.ok(Math.min(...await pixel(36,80))>230);
    await slider('videoBrightness',125);await slider('videoContrast',110);await slider('videoSaturation',0);
    assert.equal(await unrelated(),before);
    assert.deepEqual(await page.evaluate(()=>[renderConfig(false).video_effects,renderConfig(true).video_effects]),
      Array(2).fill({mirror:true,brightness:1.25,contrast:1.1,saturation:0}));
    for(const [key,x,y] of [['left',60,120],['right',180,120],['gray',120,110]]){
      const actual=await pixel(x,y),expected=seeded.outputs.correction[key];assert.ok(actual.every((channel,index)=>Math.abs(channel-expected[index])<=8),{key,actual,expected});
    }
    assert.equal(await page.locator('#videoBrightnessValue').textContent(),'125%');
    await page.evaluate(()=>sourcePreview());await page.locator('#fullscreenButton').click();
    await page.waitForFunction(()=>document.fullscreenElement);const fullscreen=await pixel(60,120);
    assert.ok(fullscreen.every(channel=>Math.abs(channel-seeded.outputs.correction.left[0])<=8),fullscreen);
    await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.fullscreenElement);await page.evaluate(()=>previewPart(0));
    await page.locator('#videoEffectsPanel').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'effects-desktop.png')});
    passed.push('Live canvas and fullscreen mirror source only; RGB correction agrees with actual MP4 within 8 levels; other settings preserved');
    await page.evaluate(()=>saveProject(true));const project=await page.evaluate(()=>state.projectId);
    await page.reload();await idle();await page.waitForFunction(()=>state.sourceId&&state.pro.video_effects?.mirror===true);
    assert.deepEqual(await page.evaluate(()=>state.pro.video_effects),{mirror:true,brightness:1.25,contrast:1.1,saturation:0});
    await page.locator('[data-route="design"]').click();await page.locator('#resetVideoEffects').click();
    assert.deepEqual(await page.evaluate(()=>state.pro.video_effects),{mirror:false,brightness:1,contrast:1,saturation:1});
    await page.locator('body').click({position:{x:800,y:145}});await page.keyboard.press('Control+z');
    await page.waitForFunction(()=>state.pro.video_effects.mirror&&state.pro.video_effects.saturation===0);
    await page.evaluate(async()=>{const legacy=projectData();delete legacy.settings.pro;await applyProject(legacy);});await idle();
    assert.deepEqual(await page.evaluate(()=>renderConfig().video_effects),{mirror:false,brightness:1,contrast:1,saturation:1});
    await page.locator('#videoMirror').check();
    await page.evaluate(()=>$('newProject').onclick());await idle();assert.equal(await page.locator('#videoMirror').isChecked(),false);
    passed.push('Saved project restores effects; reset and Ctrl+Z work; legacy and new projects start neutral');
    await page.locator('#videoFile').setInputFiles(path.join(folder,'asymmetric-source.mp4'));await idle();await page.locator('[data-route="design"]').click();
    for(const width of [736,320]){await page.setViewportSize({width,height:1050});await page.locator('#videoEffectsPanel').scrollIntoViewIfNeeded();await page.waitForTimeout(200);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(out,'effects-'+width+'.png'),fullPage:true});}
    assert.deepEqual(errors,[]);passed.push('1536/736/320 px: controls accessible, no JavaScript errors or horizontal overflow');
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({passed,errors,project_id:project},null,2));console.log(JSON.stringify({passed,errors,project_id:project},null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
