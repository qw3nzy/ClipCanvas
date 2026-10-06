// Real folder selection, review/export and responsive UI on an isolated server.
'use strict';
const {chromium}=require(process.env.CLIPCANVAS_PLAYWRIGHT||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const evidence=JSON.parse(fs.readFileSync(path.join(manifest.directory,'batch-smoke','result.json'),'utf8'));
  const out=path.join(manifest.directory,'ui-batch');fs.mkdirSync(out,{recursive:true});
  fs.writeFileSync(path.join(manifest.directory,'batch-smoke','episodes','broken.mkv'),'not a movie');
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1536,height:1050}}),errors=[],passed=[];
  page.on('pageerror',error=>errors.push(error.message));
  const check=id=>page.locator('#'+id).check();
  async function waitJob(id, batch=false){const deadline=Date.now()+180000;while(Date.now()<deadline){const response=await page.request.get(manifest.base+(batch?'/api/batches/':'/api/jobs/')+id);const j=await response.json();if(['error','cancelled'].includes(j.status))throw Error(j.error||j.status);if(j.status==='done')return j;await new Promise(r=>setTimeout(r,150));}throw Error('Job timeout: '+id);}
  const waitBatch=id=>waitJob(id,true);
  try {
    await page.goto(manifest.base);await page.waitForFunction(()=>window.ClipPro&&connected);
    await page.locator('#workMode').selectOption('automatic');
    await page.waitForFunction(()=>document.getElementById('batchProfile').options.length>1);
    const profile=await page.evaluate(async()=> (await api('/api/delivery-profiles')).profiles.find(p=>p.name==='Reusable pipeline').id);
    await page.locator('#batchProfile').selectOption(profile);
    await page.locator('#batchFolder').setInputFiles(path.join(manifest.directory,'batch-smoke','episodes'));
    assert.match(await page.locator('#batchFileSummary').textContent(),/Видео: 3/);
    await page.locator('#batchSubtitles').selectOption('required');await check('batchReviewFirst');
    await page.locator('#batchRemoveBackground').uncheck();await page.locator('#batchCheck').uncheck();await page.locator('#batchZip').uncheck();
    await page.screenshot({path:path.join(out,'folder-settings.png'),fullPage:true});
    const response=page.waitForResponse(r=>r.url().endsWith('/api/batch')&&r.request().method()==='POST');
    await page.locator('#batchStart').click();await page.locator('#briefDialog').waitFor({state:'visible'});await page.locator('#briefConfirm').click();const reply=await response;assert.equal(reply.status(),200,await reply.text());
    const batchId=(await reply.json()).id;await waitBatch(batchId);
    await page.waitForFunction(()=>document.querySelectorAll('#batchRows tr').length===3&&document.getElementById('batchRows').textContent.includes('Ожидает принятия'));
    const batch=await page.evaluate(id=>api('/api/batches/'+id),batchId);
    assert.equal(batch.batch_items.filter(r=>r.status==='prepared').length,2);
    assert.equal(batch.batch_items.filter(r=>r.status==='error').length,1);
    passed.push('Browser folder picker uploads nested episodes and sidecars; invalid video does not abort folder');
    const row=batch.batch_items.find(r=>r.status==='prepared');
    await page.locator('#batchRows input[value="'+row.index+'"]').check();
    const approvalResponse=page.waitForResponse(r=>r.url().endsWith('/decision')&&r.request().method()==='POST');
    await page.locator('#batchAccept').click();const approval=await(await approvalResponse).json();
    await waitJob(approval.id);
    const afterExport=await page.evaluate(id=>api('/api/batches/'+id),batchId);
    if(afterExport.batch_items.find(r=>r.index===row.index).status==='review'){
      const decision=page.waitForResponse(r=>r.url().endsWith('/decision')&&r.request().method()==='POST');
      await page.locator('#batchAccept').click();const decisionReply=await decision;assert.equal((await decisionReply.json()).status,'saved');
      assert.ok(decisionReply.url().includes('/'+batchId+'/'), 'Acceptance targeted another batch: '+decisionReply.url());
      assert.deepEqual(decisionReply.request().postDataJSON(),{indices:[row.index],action:'accept'});
      const decided=await page.evaluate(id=>api('/api/batches/'+id),batchId);
      assert.equal(decided.batch_items.find(r=>r.index===row.index).status,'accepted');
    }
    await page.waitForFunction(()=>document.getElementById('batchRows').textContent.includes('Принято'));
    const accepted=await page.evaluate(id=>api('/api/batches/'+id),batchId);
    assert.ok(accepted.batch_items.find(r=>r.index===row.index).artifacts.some(p=>p.startsWith('accepted/')&&p.endsWith('.mp4')));
    passed.push('Acceptance button exports selected prepared episode and exposes accepted MP4');
    await page.reload();await page.waitForFunction(()=>window.ClipPro&&connected);
    await page.locator('#workMode').selectOption('automatic');
    await page.waitForFunction(id=>document.getElementById('batchHistory').value===id&&document.querySelectorAll('#batchRows tr').length===3,batchId);
    passed.push('Reload restores last batch and its persisted acceptance state');
    for(const width of [1536,736,320]) {
      await page.setViewportSize({width,height:1000});
      await page.screenshot({path:path.join(out,'automatic-'+width+'.png'),fullPage:true});
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth);
      assert.ok(overflow<=1,'Automatic UI horizontal overflow at '+width+': '+overflow);
    }
    passed.push('Automatic UI fits desktop, tablet and narrow mobile viewport');
    await page.setViewportSize({width:1536,height:1050});
    await page.locator('#batchRows tr').filter({has:page.locator('input[value="'+row.index+'"]')}).getByText('Открыть проект').click();
    await page.waitForFunction(()=>document.getElementById('workMode').value==='manual'&&state.sourceId&&video.readyState>=1);
    assert.equal(await page.evaluate(()=>state.projectId),row.project_id);
    await page.locator('#proFramingMode').evaluate(n=>{for(let p=n.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;});
    await page.locator('#proFocusPoints').fill('0; 20; 50\n4; 80; 50');await page.locator('#proSaveFocus').click();
    assert.equal(await page.evaluate(()=>state.pro.framing.points[1].x),.8);
    await page.locator('#proClearFocus').click();assert.equal(await page.evaluate(()=>state.pro.framing.points.length),0);
    await page.locator('#proClipBannerX').evaluate(n=>{for(let p=n.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;});
    await page.locator('#proReadClipBanner').click();
    const globalX=await page.evaluate(()=>state.banners[0].geometry.x);
    await page.locator('#proClipBannerX').fill('30');await page.locator('#proSaveClipBanner').click();
    assert.equal(await page.evaluate(()=>state.pro.clip_overrides['1'].banners[0].geometry.x),30);
    assert.equal(await page.evaluate(()=>state.banners[0].geometry.x),globalX);
    passed.push('Per-clip banner controls change only selected clip while preserving shared banner');
    await page.screenshot({path:path.join(out,'manual-focus.png'),fullPage:true});
    passed.push('Batch project opens in manual editor; focus points can be edited and cleared');
    await page.locator('#workMode').selectOption('automatic');
    await page.locator('#publicationTitle').evaluate(n=>{for(let p=n.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;});
    const campaign=await page.evaluate(async()=> (await api('/api/campaigns')).campaigns.find(c=>c.name==='Acceptance campaign').id);
    await page.locator('#batchCampaign').evaluate(n=>{for(let p=n.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;});
    await page.locator('#batchCampaign').selectOption(campaign);
    const title='UI metrics '+batchId.slice(0,8);
    await page.locator('#publicationTitle').fill(title);await page.locator('#publicationViews').fill('10');
    await page.locator('#savePublication').click();await page.getByText(title,{exact:true}).waitFor();
    await page.locator('#campaignResults .history-item').filter({hasText:title}).getByText('Изменить',{exact:true}).click();
    await page.locator('#publicationViews').fill('20');await page.locator('#savePublication').click();
    await page.waitForFunction(({title})=>document.querySelector('#campaignResults')?.textContent.includes(title)&&document.querySelector('#campaignResults')?.textContent.includes('20 просмотров'),{title});
    const publications=await page.evaluate(async title=>(await api('/api/campaigns')).publications.filter(p=>p.title===title),title);
    assert.equal(publications.length,1);assert.equal(publications[0].views,20);
    passed.push('Publication metrics update existing record without duplicates');
    const guide=await page.request.get(manifest.base+'/manual.html');assert.equal(guide.status(),200);
    assert.deepEqual(errors,[]);
    const result={passed,batch_id:batchId,screenshots:out};fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
