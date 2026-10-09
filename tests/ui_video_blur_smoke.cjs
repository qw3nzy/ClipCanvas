'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPCANVAS_PLAYWRIGHT||'playwright');
(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),folder=path.join(manifest.directory,'video-blur');
  const seeded=JSON.parse(fs.readFileSync(path.join(folder,'result.json'),'utf8'));
  const out=path.join(manifest.directory,'ui-video-blur');fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1536,height:1050}}),errors=[],passed=[];
  page.on('pageerror',error=>errors.push(error.message));
  const idle=()=>page.waitForFunction(()=>!busy()&&!restoring&&!jobStarting,null,{timeout:120000});
  const seek=async time=>{await page.evaluate(time=>{video.currentTime=time;requestDraw();},time);await page.waitForFunction(()=>video.readyState>=2&&!video.seeking);};
  const variance=(x=100,y=65,w=32,h=32)=>page.evaluate(([x,y,w,h])=>{
    draw();const sx=canvas.width/state.width,sy=canvas.height/state.height;
    const data=ctx.getImageData(Math.round(x*sx),Math.round(y*sy),Math.round(w*sx),Math.round(h*sy)).data;
    let sum=0,squares=0,n=data.length/4;for(let i=0;i<data.length;i+=4){sum+=data[i];squares+=data[i]*data[i];}return squares/n-(sum/n)**2;
  },[x,y,w,h]);
  const choose=async(x1,y1,x2,y2)=>{const box=await page.locator('#videoCropCanvas').boundingBox();await page.mouse.move(box.x+box.width*x1,box.y+box.height*y1);await page.mouse.down();await page.mouse.move(box.x+box.width*x2,box.y+box.height*y2,{steps:5});await page.mouse.up();await page.locator('#applyVideoCrop').click();};
  const timeField=async(id,value)=>{await page.locator('#'+id).fill(value);await page.locator('#'+id).dispatchEvent('change');};
  const strength=async value=>page.locator('#videoBlurStrength').evaluate((input,value)=>{input.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));},String(value));
  try{
    await page.goto(manifest.base);await page.waitForFunction(()=>connected&&document.querySelector('#videoBlurPanel'));
    assert.equal(await page.locator('#addVideoBlur').isDisabled(),true);
    await page.locator('#videoFile').setInputFiles(path.join(folder,'grid.mp4'));await idle();
    await page.evaluate(async asset=>{
      setCanvas(320,240);state.geometry.video={x:0,y:0,w:320,h:180,fit:'stretch'};state.background={enabled:false,color:'#111827',blur:24};
      state.splitMode='ranges';state.ranges=[{start:0,end:4}];state.subtitles.enabled=false;
      const banner={id:'red',name:'Красный баннер',enabled:true,asset_id:asset,start:0,end:4,volume:0,
        geometry:{x:145,y:75,w:30,h:30,fit:'stretch'},schedule:{mode:'each',start:0,duration:4},chromakey:{enabled:false}};
      state.banners=[banner];await ClipEditor.loadBannerImage(banner);ClipEditor.renderBannerList();syncControls();renderParts();resizeStage();previewPart(0);changed();
    },seeded.banner_id);
    await page.locator('[data-route="design"]').click();await seek(.5);
    const before=await variance();assert.ok(before>2000,before);
    const unrelated=await page.evaluate(()=>JSON.stringify([state.geometry,state.banners,state.subtitles,state.pro.audio,state.ranges]));
    await page.locator('#addVideoBlur').click();await page.locator('#cancelVideoCrop').click();assert.equal(await page.evaluate(()=>state.pro.video_blurs.length),0);
    await page.locator('#addVideoBlur').click();await choose(.25,.25,.75,.75);
    await page.waitForFunction(()=>state.pro.video_blurs.length===1);await strength(70);
    const region=await page.evaluate(()=>state.pro.video_blurs[0]);assert.ok(Math.abs(region.x-.25)<.01&&Math.abs(region.w-.5)<.01,region);
    assert.ok(await variance()<before*.05);
    assert.ok(await variance(8,140)>2000);
    assert.equal(await page.evaluate(()=>JSON.stringify([state.geometry,state.banners,state.subtitles,state.pro.audio,state.ranges])),unrelated);
    const banner=await page.evaluate(()=>{draw();const sx=canvas.width/state.width,sy=canvas.height/state.height;return [...ctx.getImageData(Math.round(152*sx),Math.round(82*sy),1,1).data];});assert.ok(banner[0]>220&&banner[1]<30,banner);
    await strength(1);assert.ok(await variance()>1000);await strength(70);
    await timeField('videoBlurStart','00:01');await timeField('videoBlurEnd','00:03');
    await seek(.5);assert.ok(await variance()>2000);await seek(1.5);const blurred=await variance();assert.ok(blurred<100,blurred);
    await seek(3);assert.ok(await variance()>2000);
    await page.locator('#videoBlurName').fill('Лицо');await page.locator('#videoBlurName').dispatchEvent('change');
    assert.equal(await page.locator('#videoBlurSelect').textContent(),'Лицо');
    assert.deepEqual(await page.evaluate(()=>renderConfig(false).video_blurs),await page.evaluate(()=>renderConfig(true).video_blurs));
    passed.push('Frame selection, strength, source start/end, preview/export mask, and independent banner pixels work');
    await seek(1.5);await page.locator('#videoBlurEnabled').uncheck();assert.ok(await variance()>2000);await page.locator('#videoBlurEnabled').check();assert.ok(await variance()<100);
    await page.locator('#editVideoBlur').click();await page.locator('#cancelVideoCrop').click();assert.equal(await page.evaluate(()=>state.pro.video_blurs.length),1);
    await page.locator('#addVideoBlur').click();await choose(.025,.7,.275,.95);await page.waitForFunction(()=>state.pro.video_blurs.length===2);
    await page.locator('#removeVideoBlur').click();await page.waitForFunction(()=>state.pro.video_blurs.length===1);
    assert.equal(await page.evaluate(()=>state.pro.video_blurs[0].id),region.id);
    await page.locator('#removeVideoBlur').click();assert.equal(await page.evaluate(()=>state.pro.video_blurs.length),0);
    await page.locator('body').click({position:{x:800,y:145}});await page.keyboard.press('Control+z');await page.waitForFunction(()=>state.pro.video_blurs.length===1);
    assert.equal(await page.evaluate(()=>state.pro.video_blurs[0].name),'Лицо');
    await page.locator('#videoBlurWhole').click();assert.deepEqual(await page.evaluate(()=>[state.pro.video_blurs[0].start,state.pro.video_blurs[0].end]),[0,null]);
    passed.push('Multiple independent areas, cancellation, disabling, deleting, whole-video interval, and Ctrl+Z work');
    await page.evaluate(()=>saveProject(true));const project=await page.evaluate(()=>state.projectId);
    const saved=await page.evaluate(()=>structuredClone(state.pro.video_blurs));await page.reload();await idle();await page.waitForFunction(()=>state.pro.video_blurs?.length===1&&state.sourceId);
    assert.deepEqual(await page.evaluate(()=>state.pro.video_blurs),saved);
    const profileName='QA blur '+Date.now();
    await page.locator('[data-route="design"]').click();await page.locator('#proProfileName').fill(profileName);await page.locator('#proSaveProfile').click();
    await page.waitForFunction(name=>[...$('proProfile').options].some(option=>option.text===name),profileName);
    const profile=await page.evaluate(name=>[...$('proProfile').options].find(option=>option.text===name).value,profileName);
    await page.locator('#removeVideoBlur').click();await page.locator('#proProfile').selectOption(profile);await page.locator('#proApplyProfile').click();
    await page.waitForFunction(()=>state.pro.video_blurs.length===1);assert.deepEqual(await page.evaluate(()=>state.pro.video_blurs),saved);
    await seek(1.5);await page.locator('#videoBlurPanel').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'blur-desktop.png')});
    for(const width of [736,320]){
      await page.setViewportSize({width,height:1050});await page.locator('#videoBlurPanel').scrollIntoViewIfNeeded();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
      await page.screenshot({path:path.join(out,'blur-'+width+'.png'),fullPage:true});
      await page.locator('#editVideoBlur').click();const dialog=await page.locator('#videoCropDialog').boundingBox();assert.ok(dialog.x>=0&&dialog.x+dialog.width<=width+1);
      await page.screenshot({path:path.join(out,'blur-dialog-'+width+'.png')});await page.keyboard.press('Escape');
    }
    await page.evaluate(async()=>{const legacy=projectData();delete legacy.settings.pro.video_blurs;await applyProject(legacy);});await idle();assert.deepEqual(await page.evaluate(()=>renderConfig().video_blurs),[]);
    await page.locator('#addVideoBlur').click();await page.evaluate(()=>$('newProject').onclick());await idle();await page.waitForFunction(()=>!$('videoCropDialog').open);
    assert.equal(await page.evaluate(()=>state.pro.video_blurs?.length||0),0);
    await page.locator('#videoFile').setInputFiles(path.join(folder,'grid.mp4'));await idle();assert.deepEqual(await page.evaluate(()=>state.pro.video_blurs),[]);
    assert.deepEqual(errors,[]);passed.push('Projects/profiles retain areas; old/new sources remain clear; 1536/736/320 px without errors or overflow');
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({passed,errors,project_id:project},null,2));console.log(JSON.stringify({passed,errors},null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
