'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPCANVAS_PLAYWRIGHT||'playwright');
(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),folder=path.join(manifest.directory,'video-crop');
  const seeded=JSON.parse(fs.readFileSync(path.join(folder,'result.json'),'utf8'));
  const out=path.join(manifest.directory,'ui-video-crop');fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1536,height:1050}}),errors=[],passed=[];
  page.on('pageerror',error=>errors.push(error.message));
  const idle=()=>page.waitForFunction(()=>!busy()&&!restoring&&!jobStarting,null,{timeout:120000});
  const pixel=(x,y)=>page.evaluate(([x,y])=>{draw();return [...ctx.getImageData(Math.round(x*canvas.width/state.width),Math.round(y*canvas.height/state.height),1,1).data].slice(0,3);},[x,y]);
  const edge=async(id,value)=>{await page.locator('#'+id).fill(String(value));await page.locator('#'+id).dispatchEvent('change');};
  const standardCrop={enabled:true,x:.5,y:.25,w:.5,h:.5};
  try{
    await page.goto(manifest.base);await page.waitForFunction(()=>connected&&document.querySelector('#videoCropPanel'));
    assert.equal(await page.locator('#editVideoCrop').isDisabled(),true);
    await page.locator('#videoFile').setInputFiles(path.join(folder,'source.mp4'));await idle();
    await page.evaluate(async asset=>{
      setCanvas(240,300);state.geometry.video={x:20,y:50,w:200,h:120,fit:'stretch'};state.background={enabled:false,color:'#111827',blur:24};
      state.splitMode='ranges';state.ranges=[{start:0,end:3}];state.subtitles.enabled=false;
      const banner={id:'white',name:'Белый баннер',enabled:true,asset_id:asset,start:0,end:3,volume:0,
        geometry:{x:28,y:70,w:24,h:24,fit:'stretch'},schedule:{mode:'each',start:0,duration:3},chromakey:{enabled:false}};
      state.banners=[banner];await ClipEditor.loadBannerImage(banner);ClipEditor.renderBannerList();syncControls();renderParts();resizeStage();previewPart(0);video.currentTime=.5;changed();
    },seeded.banner_id);
    await page.locator('[data-route="design"]').click();await page.waitForFunction(()=>video.readyState>=2&&!video.seeking);
    const unrelated=await page.evaluate(()=>JSON.stringify([state.banners,state.subtitles,state.advertising,state.geometry,state.ranges,state.pro.audio]));
    await edge('videoCropLeft',50);await edge('videoCropTop',25);await edge('videoCropBottom',25);
    assert.deepEqual(await page.evaluate(()=>state.pro.video_crop),standardCrop);
    assert.deepEqual(await page.evaluate(()=>renderConfig(false).video_crop),standardCrop);
    assert.deepEqual(await page.evaluate(()=>renderConfig(true).video_crop),standardCrop);
    for(const [key,x,y] of [['left',60,110],['right',180,110]]){
      const actual=await pixel(x,y),expected=seeded.outputs.stretch[key];
      assert.ok(actual.every((channel,index)=>Math.abs(channel-expected[index])<=8),{key,actual,expected});
    }
    assert.ok(Math.min(...await pixel(36,80))>230);
    assert.equal(await page.evaluate(()=>JSON.stringify([state.banners,state.subtitles,state.advertising,state.geometry,state.ranges,state.pro.audio])),unrelated);
    passed.push('Numeric cropping matches exported MP4 pixels; independent banners and project settings are preserved');
    await page.locator('#editVideoCrop').click();await page.locator('#cropDialogReset').click();
    const box=await page.locator('#videoCropCanvas').boundingBox();
    await page.mouse.move(box.x+box.width-2,box.y+box.height-2);await page.mouse.down();
    await page.mouse.move(box.x+box.width*.75,box.y+box.height*.8,{steps:5});await page.mouse.up();
    await page.mouse.move(box.x+box.width*.4,box.y+box.height*.4);await page.mouse.down();
    await page.mouse.move(box.x+box.width*.5,box.y+box.height*.45,{steps:5});await page.mouse.up();
    await page.screenshot({path:path.join(out,'crop-dialog-desktop.png')});
    await page.locator('#applyVideoCrop').click();
    const dragged=await page.evaluate(()=>state.pro.video_crop);
    assert.ok(Math.abs(dragged.x-.1)<.02&&Math.abs(dragged.y-.05)<.02&&Math.abs(dragged.w-.75)<.02&&Math.abs(dragged.h-.8)<.02,dragged);
    await page.locator('#editVideoCrop').click();await page.locator('#cropDialogReset').click();await page.locator('#cancelVideoCrop').click();
    assert.deepEqual(await page.evaluate(()=>state.pro.video_crop),dragged);
    await page.locator('#videoCropRatio').selectOption('9/16');
    const portrait=await page.evaluate(()=>state.pro.video_crop);assert.ok(Math.abs(portrait.w/portrait.h*320/180-9/16)<1e-8);
    await page.locator('#fitVideoCrop').click();const fit=await page.evaluate(()=>state.geometry.video);
    assert.equal(fit.fit,'contain');assert.ok(Math.abs((fit.x+fit.w/2)-120)<1e-8);
    passed.push('Mouse resize/move, cancellation, portrait preset, and fitting the retained area into canvas work');
    await page.evaluate(value=>{state.pro.video_crop=value;state.geometry.video={x:20,y:50,w:200,h:120,fit:'stretch'};syncControls();changed();},standardCrop);
    assert.equal(await page.locator('#videoCropRatio').inputValue(),'free');
    await page.locator('#videoCropEnabled').uncheck();assert.ok((await pixel(60,110))[0]>200);
    await page.locator('#videoCropEnabled').check();assert.ok((await pixel(60,110))[1]>80);
    await page.locator('#resetVideoCrop').click();assert.equal(await page.evaluate(()=>state.pro.video_crop.enabled),false);
    await page.locator('body').click({position:{x:800,y:145}});await page.keyboard.press('Control+z');
    await page.waitForFunction(()=>state.pro.video_crop.enabled);assert.deepEqual(await page.evaluate(()=>state.pro.video_crop),standardCrop);
    await page.evaluate(()=>saveProject(true));const project=await page.evaluate(()=>state.projectId);
    await page.reload();await idle();await page.waitForFunction(()=>state.sourceId&&state.pro.video_crop?.enabled);
    assert.deepEqual(await page.evaluate(()=>state.pro.video_crop),standardCrop);
    await page.locator('[data-route="design"]').click();
    const profileName='QA crop '+Date.now();
    await page.locator('#proProfileName').fill(profileName);await page.locator('#proSaveProfile').click();
    await page.waitForFunction(name=>[...$('proProfile').options].some(option=>option.text===name),profileName);
    const profile=await page.evaluate(name=>[...$('proProfile').options].find(option=>option.text===name).value,profileName);
    await page.locator('#resetVideoCrop').click();await page.locator('#proProfile').selectOption(profile);await page.locator('#proApplyProfile').click();
    await page.waitForFunction(()=>state.pro.video_crop.enabled);assert.deepEqual(await page.evaluate(()=>state.pro.video_crop),standardCrop);
    passed.push('Toggle, reset, Ctrl+Z, saved project, and reusable profile retain the crop');
    for(const width of [736,320]){
      await page.setViewportSize({width,height:1050});await page.locator('#videoCropPanel').scrollIntoViewIfNeeded();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
      await page.screenshot({path:path.join(out,'crop-'+width+'.png'),fullPage:true});
      await page.locator('#editVideoCrop').click();
      const dialog=await page.locator('#videoCropDialog').boundingBox();assert.ok(dialog.x>=0&&dialog.x+dialog.width<=width+1);
      assert.equal(await page.locator('#applyVideoCrop').evaluate(button=>button.scrollWidth>button.clientWidth+1),false);
      await page.screenshot({path:path.join(out,'crop-dialog-'+width+'.png')});await page.keyboard.press('Escape');
    }
    await page.evaluate(async()=>{const legacy=projectData();delete legacy.settings.pro.video_crop;await applyProject(legacy);});await idle();
    assert.equal(await page.evaluate(()=>renderConfig().video_crop.enabled),false);
    await page.evaluate(()=>{state.pro.video_crop={enabled:true,x:.5,y:0,w:.5,h:1};$('newProject').onclick();});await idle();
    assert.equal(await page.evaluate(()=>state.pro.video_crop?.enabled===true),false);
    await page.locator('#videoFile').setInputFiles(path.join(folder,'source.mp4'));await idle();
    assert.equal(await page.evaluate(()=>state.pro.video_crop.enabled),false);
    assert.deepEqual(errors,[]);passed.push('Legacy/new sources start uncropped; 1536/736/320 px without JavaScript errors or horizontal overflow');
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({passed,errors,project_id:project},null,2));console.log(JSON.stringify({passed,errors},null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
