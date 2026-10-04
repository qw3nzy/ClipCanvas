const { chromium } = require(process.env.CLIPCANVAS_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const base = process.argv[2], output = path.resolve(process.argv[3]);
  fs.mkdirSync(output,{recursive:true});
  const browser = await chromium.launch({headless:true,...(process.env.CLIPCANVAS_CHROMIUM?{executablePath:process.env.CLIPCANVAS_CHROMIUM}:{})});
  const page = await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  try {
    await page.goto(base);
    await page.waitForFunction(()=>!document.getElementById('inspectLink').disabled);
    await page.getByText('Видео по ссылке',{exact:true}).click();
    await page.locator('#videoUrl').fill(process.env.CLIPCANVAS_QA_URL || 'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4');
    await page.locator('#inspectLink').click();
    await page.locator('#linkChoices').waitFor({state:'visible',timeout:120000});
    assert.ok(await page.locator('#linkQuality option').count());
    await page.locator('#downloadLink').click();
    await page.waitForFunction(()=>document.getElementById('sourceVideo').readyState>=2 && document.getElementById('assetSelect').options.length>1,null,{timeout:120000});
    assert.match(await page.locator('#fileDetails').textContent(),/\d+ × \d+ · \d\d:\d\d:\d\d/);
    assert.equal(await page.locator('#exportButton').isDisabled(),false);
    assert.deepEqual(errors,[]);
    await page.screenshot({path:path.join(output,'yoinks-import.png'),fullPage:true});
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({base,passed:['inspect public link in UI','quality picker','download into materials','playable browser preview'],errors},null,2));
    console.log('Yoinks browser workflow PASS');
  } catch(e) { console.error(await page.locator('#toast').textContent()); throw e; }
  finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
