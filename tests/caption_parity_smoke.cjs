// Compare the real preview glyph geometry with FFmpeg for all offered fonts.
// Run against an isolated integration_smoke --keep-server result.json, then
// python -B tests/caption_parity_measure.py <same result.json>.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPCANVAS_PLAYWRIGHT||'playwright');
(async()=>{
  const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const directory=path.join(manifest.directory,'caption-font-parity');fs.mkdirSync(directory,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CLIPCANVAS_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1400,height:1000}}),results=[];
  try {
    await page.goto(manifest.base);await page.waitForFunction(()=>window.ClipEditor&&connected);
    await page.evaluate(async id=>{
      await activateSource(await api('/api/assets/'+id));
      state.width=240;state.height=300;state.geometry.video={x:0,y:80,w:240,h:135,fit:'contain'};
      state.geometry.banner={x:0,y:0,w:80,h:40,fit:'contain'};
      state.background={enabled:false,color:'#0000ff',blur:0};state.banner.enabled=false;state.banners=[];
      state.cuts=[];state.advertising.enabled=false;state.encoder='cpu';state.quality='high';state.fps=24;
      state.splitMode='ranges';state.ranges=[{start:1,end:2}];
      state.subtitles={enabled:true,font:'Arial',size:20,color:'#ffffff',bold:true,italic:false,x:48,y:74,
        width:88,align:'center',line_spacing:1.4,style:'clean',outline:1,background:true,
        background_color:'#000000',background_opacity:.5,cues:[{start:0,end:3,text:'Контрольная\nреплика'}]};
      ClipEditor.openRoute('subtitles');syncControls();previewPart(0);
      document.getElementById('sampleMode').value='range';document.getElementById('sampleStart').value='1';document.getElementById('sampleEnd').value='2';
    },manifest.source_id);
    await page.waitForFunction(()=>video.readyState>=2);
    await page.evaluate(()=>new Promise(resolve=>{video.addEventListener('seeked',resolve,{once:true});video.currentTime=1.5;}));
    const fonts=['Arial','Georgia','Verdana','Tahoma','Times New Roman','Consolas'];
    for(const [index,font] of fonts.entries()){
      const result=await page.evaluate(({font,index})=>{
        state.subtitles.font=font;state.subtitles.bold=index%2===0;
        state.subtitles.cues[0].appearance={italic:index%3===0,x:48.25,y:74.25};
        canvas.width=state.width;canvas.height=state.height;draw();
        return {png:canvas.toDataURL('image/png').split(',')[1],request:renderConfig(true)};
      },{font,index});
      fs.writeFileSync(path.join(directory,`preview-${index}.png`),Buffer.from(result.png,'base64'));
      const created=await page.evaluate(async request=>post('/api/sample',request),result.request);
      let job;const deadline=Date.now()+120000;
      do {job=await (await page.request.get(manifest.base+'/api/jobs/'+created.id)).json();if(job.status==='error')throw Error(job.error);if(job.status==='done')break;await new Promise(resolve=>setTimeout(resolve,100));}while(Date.now()<deadline);
      assert.equal(job.status,'done');
      assert.equal(job.outputs.length,1);const media=job.files.find(f=>f.name.endsWith('.mp4'));
      const response=await page.request.get(manifest.base+media.url);assert.equal(response.status(),200);
      fs.writeFileSync(path.join(directory,`export-${index}.mp4`),await response.body());
      results.push({font,index,job:created.id,request:result.request});
    }
    fs.writeFileSync(path.join(directory,'requests.json'),JSON.stringify(results,null,2));console.log(directory);
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
