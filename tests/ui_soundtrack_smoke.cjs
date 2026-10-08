'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPCANVAS_PLAYWRIGHT||'playwright');
(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),folder=path.join(manifest.directory,'soundtrack');
  const seeded=JSON.parse(fs.readFileSync(path.join(folder,'result.json'),'utf8')),out=path.join(manifest.directory,'ui-soundtrack');fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1536,height:1050}}),errors=[],passed=[];page.on('pageerror',e=>errors.push(e.message));
  const idle=()=>page.waitForFunction(()=>!busy()&&!restoring&&!jobStarting,null,{timeout:120000});
  const seek=async second=>{await page.evaluate(t=>{$('seek').value=t;$('seek').oninput();},second);await page.waitForFunction(()=>!selectedMedia().seeking);await page.waitForTimeout(120);};
  const set=async(id,value)=>{await page.locator('#'+id).fill(String(value));await page.locator('#'+id).dispatchEvent('change');};
  try{
    await page.goto(manifest.base);await page.waitForFunction(()=>connected&&window.ClipSoundtrack);
    assert.equal(await page.locator('#soundtrackFile').isDisabled(),true);
    await page.locator('#videoFile').setInputFiles(path.join(folder,'source.mp4'));await idle();
    await page.evaluate(()=>{setCanvas(240,300);state.geometry.video={x:0,y:0,w:240,h:300,fit:'stretch'};state.splitMode='ranges';state.ranges=[{start:0,end:6}];state.background.enabled=false;state.subtitles.enabled=false;state.encoder='cpu';state.fps=24;syncControls();renderParts();changed();});
    await page.locator('[data-route="design"]').click();await page.locator('#sourceAudioMuted').check();
    await page.waitForFunction(()=>video.volume===0&&video.muted===false);
    await page.locator('#soundtrackFile').setInputFiles(path.join(folder,'two-tones.wav'));
    await page.waitForFunction(()=>document.getElementById('soundtrackPlayer')?.readyState>=2&&!document.getElementById('soundtrackFile').disabled);
    assert.equal(await page.locator('#soundtrackControls').isVisible(),true);
    assert.equal(await page.locator('#soundtrackAsset').inputValue(),await page.evaluate(()=>state.pro.soundtrack.asset_id));
    await set('soundtrackStart','00:01');await set('soundtrackOffset','00:00.250');await page.locator('#soundtrackLoop').check();
    await page.locator('#soundtrackVolume').evaluate(input=>{input.dispatchEvent(new PointerEvent('pointerdown'));input.value='50';input.dispatchEvent(new Event('input'));});
    await seek(1.2);await page.locator('#playButton').click();
    await page.waitForFunction(()=>{const music=$('soundtrackPlayer');return music&&!music.paused&&!music.muted&&music.volume===.5&&Math.abs(music.currentTime-(video.currentTime-.75))<.18;});
    assert.equal(await page.evaluate(()=>video.volume),0);await page.evaluate(()=>stopPlayback());
    await page.evaluate(async id=>{state.banners=[{id:'voice',enabled:true,asset_id:id,start:0,end:6,volume:1,loop:true,
      geometry:{x:0,y:0,w:80,h:40},schedule:{mode:'full'},chromakey:{enabled:false}}];await ClipEditor.loadBannerImage(state.banners[0]);ClipEditor.renderBannerList();changed();},seeded.banner_id);
    await seek(.5);await page.locator('#playButton').click();await page.waitForFunction(()=>[...document.querySelectorAll('video[data-banner-id]')].some(banner=>!banner.paused&&!banner.muted)&&video.volume===0);
    await page.evaluate(()=>stopPlayback());passed.push('Real uploaded music plays in sync at 50%; original is muted while voiced banners remain audible');
    await page.evaluate(async id=>{await activateAdvert(await api('/api/assets/'+id),true);state.advertising={...state.advertising,enabled:true,profile:'custom',start:1,repeat:false,speed:1};state.pro.soundtrack.start=0;state.pro.soundtrack.offset=0;state.banners=[];ClipEditor.renderBannerList();syncControls();renderParts();previewPart(0);changed();},manifest.advert_id);
    await seek(.6);await page.locator('#playButton').click();await seek(1.4);
    await page.waitForFunction(()=>$('soundtrackPlayer').paused&&selectedMedia()===advertVideo&&advertVideo.volume===1);
    await seek(2.3);await page.waitForFunction(()=>!$('soundtrackPlayer').paused&&Math.abs($('soundtrackPlayer').currentTime-(window.ClipPro.outputClock()-1))<.18);
    await page.locator('#soundtrackPauseAds').uncheck();await seek(1.4);await page.waitForFunction(()=>!$('soundtrackPlayer').paused&&Math.abs($('soundtrackPlayer').currentTime-window.ClipPro.outputClock())<.18);
    await page.evaluate(()=>stopPlayback());passed.push('Advertising pauses music and resumes at same position; optional music during ad works; ad sound stays enabled');
    await page.locator('#soundtrackPanel').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'soundtrack-desktop.png')});
    const settings=await page.evaluate(()=>structuredClone(state.pro.soundtrack));await page.evaluate(()=>saveProject(true));const project=await page.evaluate(()=>state.projectId);
    await page.reload();await idle();await page.waitForFunction(()=>state.pro.audio?.mute_source&&$('soundtrackPlayer')?.readyState>=2);
    assert.deepEqual(await page.evaluate(()=>state.pro.soundtrack),settings);
    await page.locator('[data-route="design"]').click();await page.locator('#removeSoundtrack').click();await page.waitForFunction(()=>!document.getElementById('soundtrackPlayer'));
    assert.equal(await page.evaluate(()=>state.pro.audio.mute_source),true);
    await page.locator('body').click({position:{x:800,y:145}});await page.keyboard.press('Control+z');await page.waitForFunction(()=>state.pro.soundtrack.asset_id&&$('soundtrackPlayer')?.readyState>=2);
    await page.locator('#soundtrackAsset').selectOption(seeded.formats[0].id);await page.locator('#useSoundtrackAsset').click();await page.waitForFunction(id=>state.pro.soundtrack.asset_id===id&&$('soundtrackPlayer')?.readyState>=2,seeded.formats[0].id);
    await page.evaluate(async()=>{const legacy=projectData();delete legacy.settings.pro;await applyProject(legacy);});await idle();
    assert.equal(await page.locator('#sourceAudioMuted').isChecked(),false);assert.equal(await page.evaluate(()=>renderConfig().soundtrack.enabled),false);
    await page.locator('#sourceAudioMuted').check();await page.evaluate(()=>$('newProject').onclick());await idle();assert.equal(await page.locator('#sourceAudioMuted').isChecked(),false);
    passed.push('Project persists mute and music; remove/Ctrl+Z/library reuse work; old/new projects have neutral sound');
    await page.locator('#videoFile').setInputFiles(path.join(folder,'source.mp4'));await idle();await page.locator('[data-route="design"]').click();
    await page.locator('#soundtrackAsset').selectOption(seeded.music_id);await page.locator('#useSoundtrackAsset').click();await page.waitForFunction(id=>state.pro.soundtrack.asset_id===id&&$('soundtrackPlayer')?.readyState>=2,seeded.music_id);
    assert.equal(await page.locator('#soundtrackControls').isVisible(),true);assert.equal(await page.locator('#sourceSoundVolume').isDisabled(),false);
    for(const width of [736,320]){await page.setViewportSize({width,height:1050});await page.locator('#soundtrackPanel').scrollIntoViewIfNeeded();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(out,'soundtrack-'+width+'.png'),fullPage:true});}
    assert.deepEqual(errors,[]);passed.push('1536/736/320 px: no JavaScript errors or horizontal overflow');
    const result={passed,errors,project_id:project};fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
  }catch(error){console.error('Preview state',await page.evaluate(()=>({errors:[],source:{paused:video.paused,volume:video.volume,time:video.currentTime},music:$('soundtrackPlayer')?{paused:$('soundtrackPlayer').paused,time:$('soundtrackPlayer').currentTime,ready:$('soundtrackPlayer').readyState}:null,clock:window.ClipPro.outputClock(),toast:$('toast').textContent})));throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
