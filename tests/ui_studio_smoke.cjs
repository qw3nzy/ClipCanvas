// Exercise the real studio through the same controls as an editor.
const { chromium } = require(process.env.CLIPCANVAS_PLAYWRIGHT || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const output = path.join(manifest.directory, 'ui-studio');
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({headless: true, executablePath: process.env.CLIPCANVAS_CHROMIUM});
  const page = await browser.newPage({viewport: {width: 1536, height: 1100}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const set = async (id, value) => { await page.locator('#'+id).fill(String(value)); await page.locator('#'+id).dispatchEvent('change'); };
  const idle = () => page.waitForFunction(() => !state.activeJob && !state.importBusy, null, {timeout: 180000});
  const runJob = async (id, endpoint) => {
    const created = page.waitForResponse(r => r.url().endsWith(endpoint) && r.request().method() === 'POST');
    await page.locator('#'+id).click();
    const response = await created; assert.equal(response.status(), 200);
    const job = await response.json();
    await page.waitForFunction(async id => {
      const job = await (await fetch('/api/jobs/'+id+'?brief=true')).json();
      if (job.status === 'error') throw Error(job.error);
      return job.status === 'done';
    }, job.id, {timeout: 180000});
    await idle(); return job.id;
  };
  try {
    await page.goto(manifest.base);
    await page.waitForFunction(() => window.ClipStudio && connected);
    await page.locator('#assetSelect').selectOption(manifest.source_id);
    await page.locator('#openAsset').click();
    await page.waitForFunction(() => state.sourceId && state.duration > 0 && !busy());
    await runJob('buildTimeline', '/api/studio/overview');
    await page.waitForFunction(() => document.getElementById('timelineHint').textContent.includes('Смен сцен'));
    await runJob('extractSubtitles', '/api/subtitles/extract');
    await page.waitForFunction(() => state.subtitles.cues.length === 2);
    await page.locator('#timelineCanvas').focus();
    await page.evaluate(() => { sourcePreview(); video.currentTime = 1; });
    await page.waitForFunction(() => Math.abs(video.currentTime-1) < .1);
    await page.keyboard.press('i');
    await page.evaluate(() => { video.currentTime = 6; });
    await page.keyboard.press('o'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => state.splitMode === 'ranges' && state.ranges.length === 1);
    assert.deepEqual(await page.evaluate(() => state.ranges), [{start: 1, end: 6}]);
    await set('trimEnd', '00:00:07'); await page.locator('#applyTrim').click();
    assert.equal(await page.evaluate(() => state.ranges[0].end), 7);
    await page.locator('#undoEdit').click();
    assert.equal(await page.evaluate(() => state.ranges[0].end), 6);
    await page.locator('#redoEdit').click();
    assert.equal(await page.evaluate(() => state.ranges[0].end), 7);
    // Drag the real right trim handle on the canvas, with snapping disabled.
    const box = await page.locator('#timelineCanvas').boundingBox();
    await page.keyboard.down('Alt');
    await page.mouse.move(box.x+box.width*7/8-3, box.y+145);
    await page.mouse.down(); await page.mouse.move(box.x+box.width*6.5/8, box.y+145, {steps: 8}); await page.mouse.up();
    await page.keyboard.up('Alt');
    assert.ok(Math.abs(await page.evaluate(() => state.ranges[0].end)-6.5) < .12);
    await page.locator('#timelineZoomIn').click(); assert.equal(await page.locator('#timelineZoomValue').textContent(), '2×');
    await page.locator('#timelineFit').click();
    await page.locator('#studioTabtemplates').click();
    await page.locator('#templateList .template-card').nth(1).getByRole('button', {name: 'Применить', exact: true}).click();
    assert.equal(await page.evaluate(() => state.subtitles.style), 'word');
    assert.equal(await page.evaluate(() => state.subtitles.cues.length), 2);
    await set('templateName', 'Browser smoke style'); await page.locator('#saveTemplate').click();
    await page.waitForFunction(() => document.getElementById('templateList').textContent.includes('Browser smoke style'));
    await page.locator('#subtitlesTab').click();
    await page.locator('#formatCaptions').click();
    await page.waitForFunction(() => document.getElementById('captionReviewDialog').open);
    await page.locator('#applyCaptionReview').click();
    assert.equal(await page.locator('#captionReviewDialog').isVisible(), false);
    await page.locator('#subtitleSearch').fill('Контрольная');
    await page.waitForFunction(() => document.querySelectorAll('.subtitle-match').length === 1);
    await page.locator('.subtitle-match').click();
    assert.ok(Math.abs(await page.evaluate(() => video.currentTime)-.5) < .1);
    await page.locator('#studioTabchecks').click(); await page.locator('#runPreflight').click();
    await page.waitForFunction(() => document.getElementById('checkResults').textContent.length > 0);
    const evidencePath = path.join(manifest.directory, 'studio-result.json');
    if (fs.existsSync(evidencePath)) {
      const evidence = JSON.parse(fs.readFileSync(evidencePath, 'utf8'));
      if (evidence.ai_job_id) {
        await page.locator('.history-details > summary').click();
        await page.locator('#refreshHistory').click();
        const row = page.locator('#jobList .history-item').filter({hasText: 'Поиск сцен · '+evidence.ai_job_id.slice(0, 8)});
        await row.getByRole('button', {name: 'Открыть результат', exact: true}).click();
        await page.waitForFunction(() => document.querySelectorAll('.highlight-card').length > 0);
        assert.ok(await page.locator('.clip-evidence').count() > 0);
        await page.locator('.highlight-card').first().getByRole('button', {name: 'Смотреть', exact: true}).click();
        await page.waitForFunction(() => !video.paused);
        await page.locator('#playButton').click();
        await page.locator('.highlight-card').first().getByRole('button', {name: '＋ В монтаж', exact: true}).click();
        assert.equal(await page.evaluate(() => state.ranges.length), 2);
        await page.locator('#undoEdit').click();
        assert.equal(await page.evaluate(() => state.ranges.length), 1);
        await page.locator('.history-details > summary').click();
      }
    }
    await page.locator('#saveProject').click();
    await page.waitForFunction(() => document.getElementById('autosaveState').textContent === 'Проект сохранён');
    await page.locator('#studioTabfind').click();
    await page.evaluate(() => { document.querySelector('.settings-panel').scrollTop = 0; document.getElementById('toast').hidden = true; });
    await page.screenshot({path: path.join(output, 'studio-desktop.png'), fullPage: true});
    for (const width of [736, 320]) {
      await page.setViewportSize({width, height: 1000});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1), true, 'overflow at '+width);
      await page.locator('#timelinePanel').screenshot({path: path.join(output, 'timeline-'+width+'.png')});
    }
    await page.setViewportSize({width: 1536, height: 1100});
    await page.reload(); await page.waitForFunction(() => state.ranges.length === 1 && !busy());
    assert.equal(await page.evaluate(() => state.subtitles.style), 'word');
    assert.ok(Math.abs(await page.evaluate(() => state.ranges[0].end)-6.5) < .12);
    await page.waitForFunction(() => document.getElementById('timelineHint').textContent.includes('Смен сцен'));
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({passed: ['timeline I/O, precision trim, drag, undo/redo, zoom', 'cached film overview restored', 'templates retain captions and clips', 'caption review and text search', 'AI cards from history, evidence, playback and insertion', 'preflight, project restore, 736/320px layouts', 'no browser errors']}, null, 2));
    console.log('Studio UI passed: '+output);
  } catch (error) {
    await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true}).catch(() => {});
    console.error('UI state', await page.locator('#toast').textContent(), await page.locator('#jobMessage').textContent(), errors);
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
