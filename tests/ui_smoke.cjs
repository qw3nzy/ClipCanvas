// Full browser workflow against an isolated local QA instance.
const { chromium } = require(process.env.CLIPCANVAS_PLAYWRIGHT || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const base = process.argv[2];
  const fixtureFolder = path.resolve(process.argv[3]);
  const output = path.resolve(process.argv[4]);
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.CLIPCANVAS_CHROMIUM ? {executablePath:process.env.CLIPCANVAS_CHROMIUM} : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  const posts = [];
  page.on('request', request => {
    if (request.method() === 'POST' && request.headers()['content-type']?.includes('application/json')) posts.push({ url: request.url(), data: request.postDataJSON() });
  });
  const value = (id, text) => page.locator('#' + id).fill(String(text)).then(() => page.locator('#' + id).dispatchEvent('change'));
  try {
    await page.goto(base);
    await page.waitForFunction(() => !document.getElementById('engineBadge').textContent.includes('Подключение'), null, { timeout: 20000 });
    const encoders = await page.evaluate(async () => (await (await fetch('/api/health')).json()).capabilities.render_encoders);
    assert.equal(await page.locator('#exportEncoder').inputValue(), 'auto');
    assert.equal(await page.locator('#exportEncoder option[value=nvidia]').isDisabled(), !encoders.includes('nvidia'));
    await page.locator('#videoFile').setInputFiles(path.join(fixtureFolder, 'source.mkv'));
    await page.waitForFunction(() => !document.getElementById('extractSubtitles').disabled, null, { timeout: 90000 });
    assert.equal(await page.locator('#audioTrack option').count(), 3);
    assert.equal(await page.locator('#subtitleTrack').inputValue(), '4');
    await page.locator('#audioTrack').selectOption('2');
    await page.locator('#exportEncoder').selectOption('cpu');
    await page.locator('#preparePreview').click();
    await page.waitForFunction(() => document.getElementById('sourceVideo').src.includes('/api/jobs/'), null, { timeout: 90000 });
    await page.waitForFunction(() => !document.getElementById('extractSubtitles').disabled);
    await page.locator('#extractSubtitles').click();
    await page.waitForFunction(() => document.querySelectorAll('#subtitleList textarea').length === 2, null, { timeout: 30000 });
    await value('canvasWidth', 240); await value('canvasHeight', 426);
    await page.locator('#applyCanvas').click();
    await page.locator('#subtitlesTab').click();
    await value('subtitleSize', 24);
    await page.locator('#subtitleStyle').selectOption('clean');
    await page.locator('#adProfile').selectOption('custom');
    await page.locator('#advertFile').setInputFiles(path.join(fixtureFolder, 'advert.mp4'));
    await page.waitForFunction(() => document.getElementById('advertInfo').textContent.includes('advert.mp4'));
    await value('advertStart', '00:00:02');
    await value('advertInterval', 4);
    await page.locator('#rangeMode').click();
    await value('cutStart', '00:00:00'); await value('cutEnd', '00:00:06');
    await page.locator('#addRange').click();
    await page.locator('#exportFps').selectOption('24');
    await page.locator('#partSelect').selectOption('0');
    await page.locator('#seek').fill('1');
    await page.locator('#seek').dispatchEvent('input');
    await page.locator('#saveProject').click();
    await page.waitForFunction(() => document.getElementById('projectList').textContent.includes('source.mkv'), null, { timeout: 10000 });
    await page.screenshot({ path: path.join(output, 'editor-desktop.png'), fullPage: true });
    for (const width of [736, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.locator('.export-row').screenshot({ path: path.join(output, `export-${width}.png`) });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.reload();
    await page.waitForFunction(() => document.getElementById('audioTrack').value === '2', null, { timeout: 90000 });
    assert.equal(await page.locator('#subtitleSize').inputValue(), '24');
    assert.equal(await page.locator('#exportEncoder').inputValue(), 'cpu');
    assert.equal(await page.locator('#adProfile').inputValue(), 'custom');
    assert.equal(await page.locator('#advertInterval').inputValue(), '4');
    assert.equal(await page.locator('#subtitleList textarea').count(), 2);
    assert.equal(await page.locator('#rangeList .range-item').count(), 1);
    // Editing a restored project must change the current object, not a stale closure.
    await value('advertInterval', 5);
    const savedFive = page.waitForResponse(response => response.url().endsWith('/api/projects') && response.request().method() === 'POST' && response.request().postDataJSON().settings.advertising.interval === 5);
    await page.locator('#saveProject').click();
    assert.equal((await savedFive).status(), 200);
    const savedRequest = [...posts].reverse().find(p => p.url.endsWith('/api/projects'));
    assert.equal(savedRequest.data.settings.advertising.interval, 5);
    await value('advertInterval', 4);
    await page.locator('#exportEncoder').selectOption(encoders.includes('nvidia') ? 'nvidia' : 'auto');
    await page.locator('#adProfile').selectOption('buba');
    assert.equal(await page.locator('#advertStart').isDisabled(), true);
    await page.locator('#adProfile').selectOption('custom');
    await value('advertStart', '00:00:02'); await value('advertInterval', 4);
    await page.locator('#advertRepeat').check();
    if (process.env.CLIPCANVAS_QA_EXPORT === '1') {
      const request = page.waitForResponse(response => response.url().endsWith('/api/render') && response.request().method() === 'POST');
      await page.locator('#exportButton').click();
      const result = await request;
      assert.equal(result.status(), 200, await result.text());
      assert.equal(result.request().postDataJSON().encoder, encoders.includes('nvidia') ? 'nvidia' : 'auto');
      const job = await result.json();
      await page.waitForFunction(async ({ base, id }) => {
        const response = await fetch(base + '/api/jobs/' + id);
        const job = await response.json();
        if (job.status === 'error') throw Error(job.error);
        return job.status === 'done';
      }, { base, id: job.id }, { timeout: 120000, polling: 500 });
      await page.waitForFunction(() => document.querySelector('#downloadList a[href$=".mp4"]'), null, { timeout: 10000 });
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ base, passed: ['upload', 'track selection', 'audio proxy', 'embedded subtitle extraction', 'advert setup', 'editable cuts', 'project persistence', 'restore-edit-save', 'BUBA profile', 'encoder detection, persistence and export'], errors }, null, 2));
    console.log('UI workflow PASS; evidence ' + output);
  } catch (error) {
    await page.screenshot({ path:path.join(output, 'failure.png'), fullPage:true }).catch(() => {});
    console.error('UI details', await page.locator('#toast').textContent(), await page.locator('#autosaveState').textContent(), errors);
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
