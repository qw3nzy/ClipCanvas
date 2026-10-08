'use strict';
const {test} = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {placedBannerGeometry,clippedCaption,formatClipTime,clipBannerSpans,clearedBannerSettings,resetProjectState,enabledClipBanner} = require('../web/usability.js');
const {proScheduled,proClipBanners} = require('../web/pro.js');
const {mergeRecognition,mergeCorrections,sourceFingerprint} = require('../web/subtitle-workflow.js');

test('ready positions preserve size, rotation and the original geometry', () => {
  const geometry = {x:1,y:2,w:100,h:30,rotation:20,fit:'contain'}, area = {x:0,y:50,w:240,h:120};
  assert.deepEqual(placedBannerGeometry(geometry,area,'video'),{...geometry,x:70,y:95});
  assert.equal(placedBannerGeometry(geometry,area,'top').y,50);
  assert.equal(placedBannerGeometry(geometry,area,'bottom').y,140);
  assert.equal(geometry.x,1);
});

test('clip time uses explicit minutes, seconds and millisecond precision across hour boundaries', () => {
  assert.equal(formatClipTime(0),'00:00.000');
  assert.equal(formatClipTime(5),'00:05.000');
  assert.equal(formatClipTime(65.125),'01:05.125');
  assert.equal(formatClipTime(3599.9996),'01:00:00.000');
});

test('source banner outside a selected clip has no fake zero interval; ads split actual visible spans', () => {
  const plan=[{kind:'source',start:0,end:2,sourceStart:10,sourceEnd:12},
    {kind:'advert',start:2,end:3},{kind:'source',start:3,end:5,sourceStart:12,sourceEnd:14}];
  assert.deepEqual(clipBannerSpans([{enabled:true,start:0,end:5,timebase:'source'}],plan),[]);
  assert.deepEqual(clipBannerSpans([{enabled:true,start:11,end:13,timebase:'source'}],plan),[{start:1,end:2},{start:3,end:4}]);
  assert.deepEqual(clipBannerSpans([{enabled:true,start:1,end:8,timebase:'output'}],plan),[{start:1,end:5}]);
});

test('remove an occurrence or a clip banner without returning copied or inherited placements', () => {
  const banner={id:'logo',enabled:true,start:0,end:1,schedule:{mode:'interval',start:5,duration:2,interval:8,excluded_occurrences:[1]}};
  assert.deepEqual(proScheduled([banner],24,2).map(b=>[b.start,b.end]),[[5,7],[21,23]]);
  assert.deepEqual(proClipBanners([banner],{banners:[banner],hidden_banner_ids:['logo']}),[]);
  assert.deepEqual(proClipBanners([banner],{banners_enabled:false}),[]);
  assert.equal(proClipBanners([banner],{}).length,1);
  const restored=enabledClipBanner({banners_enabled:false},[banner,{...banner,id:'old'}],'logo');
  assert.deepEqual(restored.hidden_banner_ids,['old']);assert.equal(proClipBanners([banner,{...banner,id:'old'}],restored).length,1);
});

test('project banner removal clears per-clip copies and schedules while preserving unrelated settings', () => {
  const current={banners:[{id:'a'},{id:'b'}],editor:{bannerProposal:{},other:true},pro:{audio:{source_volume:.5},clip_overrides:{2:{banners:[{id:'a'},{id:'b'}],banner_schedules:{a:{mode:'full'},b:{mode:'each'}},hidden_banner_ids:['a'],video:{x:5},banners_enabled:false}}},banner:{enabled:true},bannerId:'file'};
  const single=clearedBannerSettings(current,'a');
  assert.deepEqual(single.banners,[{id:'b'}]);assert.deepEqual(single.pro.clip_overrides[2].banners,[{id:'b'}]);
  assert.equal(single.pro.clip_overrides[2].banner_schedules.a,undefined);
  const all=clearedBannerSettings(current);
  assert.deepEqual(all.banners,[]);assert.deepEqual(all.pro.clip_overrides[2],{video:{x:5}});
  assert.equal(all.banner.enabled,false);assert.equal(all.bannerId,null);assert.equal(all.pro.audio.source_volume,.5);
  assert.equal(current.banners.length,2);
});

test('full project reset keeps source and identity, restores track defaults, and clears the complete montage', () => {
  const current={projectId:'same',name:'Project',sourceId:'source',duration:80,sourceWidth:1920,sourceHeight:1080,banners:[{}],cuts:[{}],pro:{clip_overrides:{2:{}}}};
  const defaults={width:1080,height:1920,banner:{enabled:false},advertising:{enabled:false},subtitles:{enabled:false,cues:[]},advertId:null,bannerId:null,quality:'standard'};
  const reset=resetProjectState(current,defaults,{recommended_audio_stream_index:2,recommended_subtitle_stream_index:3});
  assert.equal(reset.projectId,'same');assert.equal(reset.sourceId,'source');assert.equal(reset.name,'Project');
  assert.deepEqual(reset.ranges,[{start:0,end:80}]);assert.deepEqual(reset.banners,[]);assert.deepEqual(reset.cuts,[]);assert.deepEqual(reset.pro,{});
  assert.equal(reset.audioIndex,2);assert.equal(reset.advertId,null);
  reset.subtitles.cues.push({});assert.deepEqual(defaults.subtitles.cues,[]);
});
test('recognition preserves manual text, boundaries and styling while updating automatic cues', () => {
  const manual = {start:1,end:2,text:'Правка',appearance:{color:'#ff0000'},manual:true};
  const automatic = {start:2,end:4,text:'старый',manual:false};
  const next = [{start:1,end:2,text:'модель'},{start:2,end:4,text:'новый'}];
  assert.deepEqual(mergeRecognition([manual,automatic],next),[manual,{...next[1],manual:false}]);
  assert.equal(mergeRecognition([manual],next,true)[0].text,'модель');
  assert.equal(mergeCorrections([manual],[{index:0,before:'Правка',after:'Замена'}])[0].text,'Правка');
});
test('legacy imported cues are protected and words outside manual spans remain', () => {
  const saved = {start:1,end:2,text:'Ручной'};
  const fresh = {start:0,end:3,text:'а б в',words:[{start:.1,end:.5,text:'а'},{start:1.1,end:1.5,text:'б'},{start:2.1,end:2.5,text:'в'}]};
  const merged = mergeRecognition([saved],[fresh]);
  assert.deepEqual(merged.map(cue=>cue.text),['а','Ручной','в']);
  assert.equal(fresh.text,'а б в');
});
test('preview clips word text at the same montage edges used by exported SRT', () => {
  const cue = {start:0,end:4,text:'один два',words:[{start:.1,end:.6,text:'один'},{start:2.1,end:2.6,text:'два'}],render_lines:['один два']};
  assert.equal(clippedCaption(cue,2,4).text,'два');
  assert.equal(clippedCaption(cue,2,4).render_lines,undefined);
});
test('deleted manual spans stay absent after recognition and can be explicitly replaced', () => {
  const fresh=[{start:1,end:2,text:'Удалённая реплика'}],deleted=[{start:1,end:2}];
  assert.deepEqual(mergeRecognition([],fresh,false,deleted),[]);
  assert.equal(mergeRecognition([],fresh,true,deleted).length,1);
});
test('language and same-source project changes invalidate recognition', () => {
  const state = {sourceId:'same',audioIndex:0,speechLanguage:'ru',projectContext:1,subtitles:{cues:[]}};
  const before=sourceFingerprint(state); state.speechLanguage='en'; assert.notEqual(sourceFingerprint(state),before);
  state.speechLanguage='ru';state.projectContext=2;assert.notEqual(sourceFingerprint(state),before);
});

test('late save from the former project cannot assign its ID to a new same-source project', async () => {
  const script=fs.readFileSync(require.resolve('../web/app.js'),'utf8');
  const fragment=script.slice(script.indexOf('let pendingSave=null'),script.indexOf('function requestDraw()'));
  let complete;
  const result=new Promise(resolve=>{complete=resolve;});
  const state={projectId:null,sourceId:'same'};
  const context={state,projectRevision:0,saveInFlight:false,saveAgain:false,connected:true,STORAGE:'project',structuredClone,
    ClipBrowser:{randomUUID:()=> 'new-context'},localStorage:{setItem(){}},$(){return {};},projectData:()=>({source_id:state.sourceId,id:state.projectId}),post:()=>result,
    toast(){},refreshHistory:async()=>{},clearTimeout(){},setTimeout(){},saveTimer:null};
  vm.createContext(context);vm.runInContext(fragment,context);
  const pending=context.saveProject();context.resetProjectIdentity();complete({id:'old-project'});await pending;
  assert.equal(state.projectId,null);
});

test('BUBA speed above the customer limit remains selectable without resetting settings', () => {
  const script=fs.readFileSync(require.resolve('../web/app.js'),'utf8');
  const handler=script.slice(script.indexOf("$('advertSpeed').onchange="),script.indexOf("$('selectAdvert').onclick="));
  const field={value:'1.3'},state={advertising:{profile:'buba',speed:1}};
  const context={state,$:()=>field,handle:fn=>fn,sourcePreview(){},refreshAdHints(){},changed(){}};
  vm.createContext(context);vm.runInContext(handler,context);field.onchange();
  assert.equal(state.advertising.speed,1.3);
  assert.equal(field.value,'1.3');
});
