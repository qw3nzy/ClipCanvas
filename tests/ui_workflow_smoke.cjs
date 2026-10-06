// Real browser and isolated engine: no large model downloads or AI inference.
'use strict';
const {chromium} = require(process.env.CLIPCANVAS_PLAYWRIGHT || 'playwright');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');

(async () => {
  const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const out = path.join(manifest.directory, 'ui-workflow'); fs.mkdirSync(out, {recursive: true});
  const browser = await chromium.launch({headless: true, executablePath: process.env.CLIPCANVAS_CHROMIUM});
  const page = await browser.newPage({viewport: {width: 1536, height: 1050}}), errors = [], passed = [];
  page.on('pageerror', error => errors.push(error.message));
  const route = key => page.locator('[data-route="'+key+'"]').click();
  const idle = () => page.waitForFunction(() => !busy() && !restoring && !jobStarting, null, {timeout: 120000});
  const openAncestors = id => page.locator('#'+id).evaluate(node => { for (let p = node.parentElement; p; p = p.parentElement) if (p.tagName === 'DETAILS') p.open = true; });
  const set = async (id, value) => { await openAncestors(id); await page.locator('#'+id).fill(String(value)); await page.locator('#'+id).dispatchEvent('change'); await page.locator('#'+id).evaluate(node => node.blur()); };
  const slider = (id, value) => page.locator('#'+id).evaluate((node, v) => { node.value = String(v); node.dispatchEvent(new Event('input', {bubbles: true})); }, value);
  const screenshot = name => page.screenshot({path: path.join(out, name+'.png'), fullPage: true});
  async function runJob(id, endpoint) {
    const response = page.waitForResponse(r => r.url().endsWith(endpoint) && r.request().method() === 'POST');
    await page.locator('#'+id).click();
    if(id==='runAutomatic'){await page.locator('#briefDialog').waitFor({state:'visible'});await page.locator('#briefConfirm').click();}
    const reply = await response;
    assert.equal(reply.status(), 200, await reply.text()); const created = await reply.json();
    await page.waitForFunction(async id => { const job = await (await fetch('/api/jobs/'+id)).json(); if (job.status === 'error') throw Error(job.error); return job.status === 'done'; }, created.id, {timeout: 120000});
    await idle(); return page.evaluate(id => api('/api/jobs/'+id), created.id);
  }
  try {
    await page.goto(manifest.base); await page.waitForFunction(() => window.ClipWorkflow && window.ClipSubtitles && connected);
    await route('projects'); await page.locator('#assetSelect').selectOption(manifest.source_id); await page.locator('#openAsset').click();
    await page.waitForFunction(id => state.sourceId === id && video.readyState >= 2 && !busy(), manifest.source_id, {timeout: 120000});
    await route('edit'); await set('partSeconds', 3);
    await page.locator('#partSelect').selectOption('1');
    assert.equal(await page.evaluate(() => previewIndex), 1);
    await page.locator('#fullscreenButton').click(); await page.waitForFunction(() => !!document.fullscreenElement);
    assert.equal(await page.evaluate(() => previewIndex), 1, 'fullscreen preserves selected clip');
    await page.locator('#previewScope').selectOption('source');
    await page.waitForFunction(() => previewIndex === null && Number($('seek').max) === state.duration);
    const sourceBounds = await page.locator('#stage').boundingBox();
    const fullscreenBounds = await page.locator('.preview-panel').boundingBox();
    assert.ok(Math.abs(fullscreenBounds.x) < 1 && Math.abs(fullscreenBounds.y) < 1, 'fullscreen begins at viewport origin');
    assert.equal(Math.round(fullscreenBounds.width), 1536);
    assert.equal(Math.round(fullscreenBounds.height), 1050);
    assert.ok(Math.abs(sourceBounds.width / sourceBounds.height - 256 / 144) < .01, 'source fullscreen fits source aspect');
    assert.ok(sourceBounds.width > 1000, 'wide source uses fullscreen width');
    await page.screenshot({path: path.join(out, 'source-fullscreen.png'), fullPage: false});
    await page.locator('#fullscreenButton').click(); await page.waitForFunction(() => !document.fullscreenElement);
    await page.locator('#fullscreenButton').click(); await page.waitForFunction(() => !!document.fullscreenElement);
    assert.equal(await page.evaluate(() => previewIndex), null, 're-enter fullscreen keeps full source');
    await page.locator('#fullscreenButton').click(); await page.waitForFunction(() => !document.fullscreenElement);
    passed.push('Clip/full-source fullscreen switches, source aspect/width, no scope reset on fullscreen');

    await page.locator('#rangeMode').click(); await set('cutStart', 0); await set('cutEnd', 2); await page.locator('#addRange').click();
    await set('cutStart', 4); await set('cutEnd', 6); await page.locator('#addRange').click();
    assert.deepEqual(await page.evaluate(() => state.ranges), [{start: 0, end: 2}, {start: 4, end: 6}]);
    await page.locator('#previewScope').selectOption('all');
    assert.deepEqual(await page.evaluate(() => segments.map(s => [s.start, s.end, s.sourceStart, s.sourceEnd])), [[0, 2, 0, 2], [2, 4, 4, 6]]);
    await route('export');
    assert.deepEqual(await page.evaluate(() => renderConfig(true).parts), [{start: 0, end: 2}], 'sample remains selected clip when full montage shown');
    passed.push('Full montage follows ordered ranges and sample export keeps selected clip');

    await route('design'); await set('canvasWidth', 600); await set('canvasHeight', 600); await page.locator('#applyCanvas').click();
    await page.locator('#previewScope').selectOption('clip');
    await page.locator('#addBannerFile').setInputFiles(path.join(manifest.directory, 'composition', 'key-alpha.png'));
    await page.waitForFunction(() => state.banners.length === 1 && !busy());
    await set('multiBannerName', 'Workflow QA'); await page.locator('#editBannerPosition').click();
    await set('layerX', 160); await set('layerY', 180); await set('layerW', 240); await set('layerH', 120);
    await slider('bannerRotation', 30);
    assert.equal(await page.evaluate(() => state.banners[0].geometry.rotation), 30);
    assert.equal(await page.locator('#selection').evaluate(node => node.style.transform), 'rotate(30deg)');
    assert.equal(await page.evaluate(() => renderConfig(false).banners[0].geometry.rotation), 30);
    const handle = await page.locator('.rotation-handle').boundingBox();
    assert.ok(handle, 'rotation handle visible');
    const initialAngle = await page.evaluate(() => state.banners[0].geometry.rotation);
    await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2); await page.mouse.down();
    await page.mouse.move(handle.x + handle.width / 2 + 55, handle.y + handle.height / 2 + 40, {steps: 8}); await page.mouse.up();
    assert.ok(Math.abs(await page.evaluate(() => state.banners[0].geometry.rotation) - initialAngle) > 5, 'dragging rotation marker rotates banner');
    await slider('bannerRotation', 30);
    await page.locator('#saveBannerTemplate').click();
    await page.waitForFunction(() => [...document.querySelectorAll('.banner-template-card strong')].some(node => node.textContent === 'Workflow QA'));
    await page.locator('.banner-library > summary').click();
    const card = page.locator('.banner-template-card').filter({has: page.locator('strong', {hasText: 'Workflow QA'})}).last();
    await card.getByRole('button', {name: 'Добавить', exact: true}).click();
    await page.waitForFunction(() => state.banners.length === 2);
    assert.equal(await page.evaluate(() => state.banners[1].geometry.rotation), 30);
    await card.dragTo(page.locator('#stage'), {targetPosition: {x: 150, y: 140}});
    await page.waitForFunction(() => state.banners.length === 3);
    assert.equal(await page.evaluate(() => state.banners[2].geometry.rotation), 30);
    const templates = await page.evaluate(() => api('/api/banner-templates'));
    assert.ok(templates.templates.some(t => t.name === 'Workflow QA' && t.geometry.rotation === 30));
    const rejectedInsertion = await page.evaluate(async () => {
      const template = (await api('/api/banner-templates')).templates.find(t => t.name === 'Workflow QA');
      const original = ClipEditor.loadBannerImage, projectId = state.projectId, count = state.banners.length;
      ClipEditor.loadBannerImage = async banner => { await original(banner); state.projectId = 'different-project'; };
      try { await ClipWorkflow.insertTemplate(template); return false; }
      catch (error) { return error.message.includes('Проект изменился') && count === state.banners.length; }
      finally { ClipEditor.loadBannerImage = original; state.projectId = projectId; }
    });
    assert.equal(rejectedInsertion, true, 'template loading cannot insert into another project');
    passed.push('Banner slider/handle rotation and engine template save, add, drag-to-canvas');

    // Background-only job exercises the real automatic backend without inference.
    const opaque = await page.evaluate(() => { const c = document.createElement('canvas'); c.width = 80; c.height = 40; const g = c.getContext('2d'); g.fillStyle = '#0033ff'; g.fillRect(0, 0, 80, 40); g.fillStyle = '#ffffff'; g.fillRect(25, 12, 30, 16); return c.toDataURL('image/png').split(',')[1]; });
    const opaquePath = path.join(out, 'opaque-blue.png'); fs.writeFileSync(opaquePath, Buffer.from(opaque, 'base64'));
    await page.locator('#addBannerFile').setInputFiles(opaquePath);
    await page.waitForFunction(() => state.banners.length === 4 && !busy());
    await page.evaluate(() => {
      state.subtitles.cues = [{start: 0, end: 1, text: 'Скрытая реплика должна сохраниться'}]; state.subtitles.enabled = false;
      for (const banner of state.banners) banner.chromakey.enabled = false;
      syncControls(); changed();
    });
    const before = await page.evaluate(() => ({banners: structuredClone(state.banners), subtitles: structuredClone(state.subtitles), cuts: structuredClone(state.cuts)}));
    await route('edit');
    for (const id of ['autoBanners', 'autoIntro', 'autoOutro', 'autoSubtitles']) await page.locator('#'+id).uncheck();
    await page.locator('#autoBackground').check();
    const automaticJob = await runJob('runAutomatic', '/api/ai/automatic');
    await page.waitForFunction(id => state.editor.automaticJobId === id, automaticJob.id);
    assert.deepEqual(await page.evaluate(() => state.subtitles), before.subtitles, 'hidden captions survive unrelated automatic operations');
    assert.ok(await page.evaluate(() => state.banners.some(b => b.chromakey.enabled)), 'real automatic chromakey updated a banner');
    assert.equal(await page.evaluate(() => previewIndex), -1);
    await page.locator('#undoEdit').click();
    assert.deepEqual(await page.evaluate(() => ({banners: state.banners, subtitles: state.subtitles, cuts: state.cuts})), before);
    passed.push('Real automatic background removal, disabled caption preservation, whole-montage preview and single undo');
    const templateOption = await page.locator('#autoBannerTemplate option').nth(1).getAttribute('value');
    await page.locator('#autoBannerTemplate').selectOption(templateOption);
    const withTemplate = await runJob('runAutomatic', '/api/ai/automatic');
    await page.waitForFunction(id => state.editor.automaticJobId === id, withTemplate.id);
    assert.equal(await page.evaluate(() => state.banners.length), before.banners.length + 1);
    await page.locator('#undoEdit').click();
    assert.deepEqual(await page.evaluate(() => ({banners: state.banners, subtitles: state.subtitles, cuts: state.cuts})), before);
    await page.locator('#autoBannerTemplate').selectOption('');
    passed.push('Automatic mode inserts selected engine template only on success and undo restores project');


    await page.evaluate(async () => {
      const signature = await ClipWorkflow.snapshotDigest(), project = structuredClone(renderConfig(false));
      state.banners[0].name = 'User edit after analysis'; changed();
      await ClipWorkflow.applyAutomatic({id: 'stale-ui-fixture', status: 'done', source_id: state.sourceId, client_snapshot: signature, project, request: {options: {subtitles: false}}});
    });
    assert.equal(await page.evaluate(() => state.banners[0].name), 'User edit after analysis');
    assert.match(await page.locator('#automaticStatus').textContent(), /Проект изменился/);
    // Change while SHA digest is awaiting completion, after the initial equality check.
    await page.evaluate(async () => {
      const signature = await ClipWorkflow.snapshotDigest(), project = structuredClone(renderConfig(false)), original = crypto.subtle.digest.bind(crypto.subtle);
      crypto.subtle.digest = async (...args) => { const result = await original(...args); state.banners[0].name = 'Edit during digest'; return result; };
      try { await ClipWorkflow.applyAutomatic({id: 'digest-race-fixture', status: 'done', source_id: state.sourceId, client_snapshot: signature, project}); }
      finally { crypto.subtle.digest = original; }
    });
    assert.equal(await page.evaluate(() => state.banners[0].name), 'Edit during digest');
    assert.match(await page.locator('#automaticStatus').textContent(), /Проект изменился/);
    passed.push('Stale automatic job and editing during digest both reject application');

    await route('design'); await page.locator('#saveProject').click();
    await page.waitForFunction(() => $('autosaveState').textContent === 'Проект сохранён');
    const saved = await page.evaluate(() => state.banners.map(b => ({asset_id: b.asset_id, rotation: b.geometry.rotation})));
    await page.reload(); await page.waitForFunction(() => window.ClipWorkflow && state.banners.length === 4 && !restoring && !busy());
    assert.deepEqual(await page.evaluate(() => state.banners.map(b => ({asset_id: b.asset_id, rotation: b.geometry.rotation}))), saved);
    await route('export'); await page.locator('#sampleMode').selectOption('clip');
    const sample = await runJob('renderOne', '/api/sample');
    assert.ok(sample.files.some(file => file.name.endsWith('.mp4') && !file.internal));
    assert.equal(sample.request.banners[0].geometry.rotation, 30);
    assert.deepEqual(sample.request.parts, [{start: 0, end: 2}]);
    passed.push('Rotations survive save/reload and real short MP4 export');

    await route('subtitles'); assert.equal(await page.locator('#subtitleQuality').inputValue(), 'large-v3');
    assert.equal(await page.locator('#createAISubtitles').isVisible(), true);
    assert.equal(await page.locator('.caption-tools').isVisible(), false, 'advanced caption controls collapsed');
    for (const width of [1536, 736, 320]) {
      await page.setViewportSize({width, height: 1050});
      for (const section of ['edit', 'design', 'subtitles']) {
        await route(section);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'No horizontal overflow at '+width+' in '+section);
        await screenshot(section+'-'+width);
      }
    }
    assert.deepEqual(errors, []);
    passed.push('One-action subtitles visible, advanced controls collapsed, 1536/736/320 responsive screenshots, no JavaScript errors');
    fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify({passed, errors, automatic_job: automaticJob.id, sample_job: sample.id, out}, null, 2));
    console.log(JSON.stringify({passed, errors, out}, null, 2));
  } catch (error) {
    console.error('Diagnostics', await page.evaluate(() => ({source: state.sourceId, previewIndex, segments, banners: state.banners, busy: busy(), job: state.activeJob, toast: $('toast').textContent, automatic: $('automaticStatus')?.textContent})).catch(() => null));
    await screenshot('failure').catch(() => {});
    fs.writeFileSync(path.join(out, 'failure.json'), JSON.stringify({error: error.message, passed, errors}, null, 2));
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
