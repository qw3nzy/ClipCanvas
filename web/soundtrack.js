'use strict';

function soundtrackSettings(value={}) {
  return {enabled:false,asset_id:null,volume:1,start:0,offset:0,loop:false,pause_for_ads:true,...value};
}
function soundtrackTiming(settings,duration,plan,clock,inAd=false) {
  let elapsed=clock-settings.start;
  if(settings.pause_for_ads)elapsed-=plan.filter(segment=>segment.kind==='advert').reduce((sum,segment)=>
    sum+Math.max(0,Math.min(clock,segment.end)-Math.max(settings.start,segment.start)),0);
  let time=settings.offset+Math.max(0,elapsed);
  if(settings.loop&&duration>0)time%=duration;
  return {time,visible:settings.enabled&&duration>0&&elapsed>=0&&time<duration&&!(inAd&&settings.pause_for_ads)};
}
if(typeof document==='undefined'){
  module.exports={soundtrackSettings,soundtrackTiming};
}else(()=>{
  const panel=document.createElement('section');panel.id='soundtrackPanel';panel.className='editor-tool';
  panel.innerHTML=`<h2>Звук видео и своя дорожка</h2>
    <label class="toggle-row"><span>Убрать звук основного видео</span><input id="sourceAudioMuted" type="checkbox"></label>
    <label class="field">Громкость исходника, %<input id="sourceSoundVolume" type="number" min="0" max="300" value="100"></label>
    <label class="button secondary full">Загрузить свой звук или музыку<input id="soundtrackFile" type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.wma,.mp4,.webm" hidden></label>
    <p id="soundtrackStatus" class="muted" role="status">Файл не выбран. Можно полностью отключить звук основного видео.</p>
    <label class="field">Ранее загруженный звук<select id="soundtrackAsset"><option value="">Выберите файл</option></select></label>
    <div class="action-pair"><button id="useSoundtrackAsset" class="button secondary">Использовать файл</button><button id="removeSoundtrack" class="text-button">Убрать свою дорожку</button></div>
    <div id="soundtrackControls" hidden>
      <label class="toggle-row"><span>Включить свою дорожку</span><input id="soundtrackEnabled" type="checkbox"></label>
      <label class="range-field">Громкость своей дорожки <output id="soundtrackVolumeValue">100%</output><input id="soundtrackVolume" aria-label="Громкость своей дорожки" type="range" min="0" max="100" value="100"></label>
      <div class="field-pair"><label>Начало в клипе<input id="soundtrackStart" placeholder="00:00.000" inputmode="decimal"></label><label>Начать файл с<input id="soundtrackOffset" placeholder="00:00.000" inputmode="decimal"></label></div>
      <label class="toggle-row"><span>Повторять файл до конца клипа</span><input id="soundtrackLoop" type="checkbox"></label>
      <label class="toggle-row"><span>Ставить на паузу во время отдельной рекламы</span><input id="soundtrackPauseAds" type="checkbox" checked></label>
      <button id="previewSoundtrack" class="button secondary full">Послушать с выбранным клипом</button>
      <p class="muted">В каждом клипе дорожка запускается заново. Время — минуты:секунды. Для замены звука включите «Убрать звук основного видео»; для смешивания оставьте его выключенным. Звук баннеров и рекламы настраивается отдельно.</p>
    </div>`;
  $('videoEffectsPanel').after(panel);
  const previewPanel=document.querySelector('.preview-panel');
  const checkpoint=()=>window.ClipStudio?.checkpoint(),metadata=new Map();
  let record=null,loading=null,uploading=false,synchronizing=false,pendingPlay=false,failed='';
  const current=()=>soundtrackSettings(state.pro?.soundtrack);
  function clearPlayer(){if(record){record.element.pause();record.element.removeAttribute('src');record.element.load();record.element.remove();}record=null;pendingPlay=false;}
  async function attach(assetId){
    if(loading?.id===assetId)return loading.promise;
    const task=(async()=>{
      const asset=metadata.get(assetId)||await api('/api/assets/'+assetId);metadata.set(assetId,asset);
      if(current().asset_id!==assetId)return;
      clearPlayer();const element=document.createElement('audio');element.id='soundtrackPlayer';element.preload='auto';element.hidden=true;document.body.append(element);
      record={assetId,asset,element};
      element.addEventListener('loadedmetadata',()=>{refresh();sync();});
      element.addEventListener('error',()=>{if(record?.element===element){failed=assetId;$('soundtrackStatus').textContent='Своя дорожка не загрузилась. Выберите файл повторно или обновите подключение.';}});
      element.src=engine+'/api/audio/media/'+assetId;element.load();refresh();
    })();loading={id:assetId,promise:task};
    try{await task;}finally{if(loading?.promise===task)loading=null;}
  }
  function sync(){
    if(synchronizing)return;synchronizing=true;
    try{
      const settings=current();
      if(!settings.asset_id){if(record)clearPlayer();return;}
      if(record?.assetId!==settings.asset_id){if(record)clearPlayer();if(failed!==settings.asset_id)attach(settings.asset_id).catch(error=>{failed=settings.asset_id;$('soundtrackStatus').textContent=error.message;});return;}
      const element=record.element,transport=selectedMedia(),context=window.ClipPro.previewContext();
      const plan=adSchedule(context.part),clock=window.ClipPro.outputClock(),inAd=previewIndex!==null&&segments[playSegment]?.kind==='advert';
      const target=soundtrackTiming(settings,record.asset.duration,plan,clock,inAd);
      const inside=previewIndex!==null||(video.currentTime>=context.part.start&&video.currentTime<context.part.end);
      const visible=target.visible&&inside&&!previewPanel.hidden&&!document.hidden;
      element.volume=Math.max(0,Math.min(1,Number($('previewVolume').value)*settings.volume));
      element.muted=video.muted||!visible;element.playbackRate=inAd?transport.playbackRate/state.advertising.speed:transport.playbackRate;
      if(!visible||element.readyState<2){element.pause();return;}
      const running=!transport.paused&&!transport.ended&&!transport.seeking;
      const time=Math.max(0,Math.min(element.duration-.005,target.time));
      if(!element.seeking&&Math.abs(element.currentTime-time)>(running?.12:.018))element.currentTime=time;
      if(!running){element.pause();return;}
      if(element.paused&&!pendingPlay){pendingPlay=true;element.play().then(()=>{if(!record||record.element!==element||selectedMedia().paused)element.pause();}).catch(error=>{if(error.name!=='AbortError')toast('Своя дорожка не запустилась. Нажмите воспроизведение ещё раз.');}).finally(()=>pendingPlay=false);}
    }finally{synchronizing=false;}
  }
  function refresh(){
    const settings=current(),asset=metadata.get(settings.asset_id),unavailable=!state.sourceId||busy()||restoring||uploading;
    $('sourceAudioMuted').checked=state.pro?.audio?.mute_source===true;
    if(document.activeElement!==$('sourceSoundVolume'))$('sourceSoundVolume').value=Math.round((state.pro?.audio?.source_volume??1)*100);
    $('sourceSoundVolume').disabled=$('proSourceVolume').disabled=$('sourceAudioMuted').checked||unavailable;
    $('soundtrackControls').hidden=!settings.asset_id;
    $('soundtrackStatus').textContent=asset?asset.name+' · '+formatClipTime(asset.duration)+(settings.enabled?'':' · дорожка выключена'):settings.asset_id?'Подготавливаем звуковую дорожку…':'Файл не выбран. Можно полностью отключить звук основного видео.';
    if(document.activeElement!==$('soundtrackAsset'))$('soundtrackAsset').value=settings.asset_id||'';
    for(const [id,key] of [['soundtrackEnabled','enabled'],['soundtrackLoop','loop'],['soundtrackPauseAds','pause_for_ads']])$(id).checked=settings[key];
    for(const [id,key] of [['soundtrackStart','start'],['soundtrackOffset','offset']])if(document.activeElement!==$(id))$(id).value=formatClipTime(settings[key]);
    $('soundtrackVolume').value=Math.round(settings.volume*100);$('soundtrackVolumeValue').value=Math.round(settings.volume*100)+'%';
    for(const id of ['sourceAudioMuted','soundtrackFile','soundtrackAsset','useSoundtrackAsset','removeSoundtrack','soundtrackEnabled','soundtrackLoop','soundtrackPauseAds','soundtrackStart','soundtrackOffset','soundtrackVolume','previewSoundtrack'])$(id).disabled=unavailable||(id==='removeSoundtrack'&&!settings.asset_id);
  }
  function apply(settings){state.pro.soundtrack=soundtrackSettings(settings);failed='';changed();refresh();sync();}
  async function loadLibrary(){if(!connected)return;const result=await api('/api/assets');const value=current().asset_id;$('soundtrackAsset').replaceChildren(new Option('Выберите файл',''));for(const asset of result.assets.filter(item=>item.kind==='audio')){metadata.set(asset.id,asset);$('soundtrackAsset').add(new Option(asset.name+' · '+formatClipTime(asset.duration),asset.id));}$('soundtrackAsset').value=value||'';refresh();sync();}
  async function use(asset){metadata.set(asset.id,asset);checkpoint();apply({...current(),asset_id:asset.id,enabled:true,offset:0});await attach(asset.id);previewPart(Number($('partSelect').value)||0);}
  $('sourceAudioMuted').onchange=()=>{checkpoint();state.pro.audio.mute_source=$('sourceAudioMuted').checked;changed();window.ClipVideoBanners.sync();refresh();};
  $('sourceSoundVolume').onchange=handle(()=>{const volume=finite($('sourceSoundVolume').value,0,300)/100;checkpoint();state.pro.audio.source_volume=volume;changed();window.ClipVideoBanners.sync();refresh();});
  $('soundtrackFile').onchange=handle(async event=>{
    const file=event.target.files[0];if(!file)return;if(busy()||restoring)throw Error('Дождитесь текущей задачи.');
    const context=projectEpoch,source=state.sourceId;uploading=true;refresh();
    try{const asset=await upload(file,'audio');if(projectEpoch!==context||state.sourceId!==source){await loadLibrary();throw Error('Проект изменился. Файл сохранён в библиотеке звука.');}await use(asset);await loadLibrary();toast(state.pro.audio.mute_source?'Своя дорожка добавлена. Исходный звук отключён.':'Своя дорожка добавлена и смешивается со звуком видео.');}
    finally{uploading=false;event.target.value='';refresh();}
  });
  $('useSoundtrackAsset').onclick=handle(async()=>{const id=$('soundtrackAsset').value;if(!id)throw Error('Выберите загруженный звуковой файл.');await use(metadata.get(id)||await api('/api/assets/'+id));});
  $('removeSoundtrack').onclick=()=>{checkpoint();apply({...current(),enabled:false,asset_id:null});};
  for(const [id,key]of [['soundtrackEnabled','enabled'],['soundtrackLoop','loop'],['soundtrackPauseAds','pause_for_ads']])$(id).onchange=()=>{checkpoint();apply({...current(),[key]:$(id).checked});};
  for(const [id,key]of [['soundtrackStart','start'],['soundtrackOffset','offset']])$(id).onchange=handle(()=>{const value=parseTime($(id).value);if(value>86400||value<0)throw Error('Время должно быть от 00:00 до 24 часов.');if(key==='offset'&&value>=(metadata.get(current().asset_id)?.duration||0))throw Error('Начало в звуковом файле должно быть раньше его конца.');checkpoint();apply({...current(),[key]:value});});
  $('soundtrackVolume').onpointerdown=checkpoint;$('soundtrackVolume').onkeydown=event=>{if(event.key.startsWith('Arrow')||['Home','End','PageUp','PageDown'].includes(event.key))checkpoint();};
  $('soundtrackVolume').oninput=()=>apply({...current(),volume:Number($('soundtrackVolume').value)/100});
  $('previewSoundtrack').onclick=handle(async()=>{previewPart(Number($('partSelect').value)||0);const time=Math.min(Number($('seek').max),current().start);$('seek').value=time;$('seek').oninput();await selectedMedia().play();sync();});
  for(const media of [video,advertVideo])for(const event of ['play','pause','seeking','seeked','timeupdate','ratechange','volumechange','ended'])media.addEventListener(event,sync);
  const previousDraw=draw;draw=function(...args){const result=previousDraw(...args);sync();return result;};
  for(const key of ['changed','projectRestored','refreshButtons']){const previous=window.ClipEditor[key];window.ClipEditor[key]=function(...args){const result=previous?.apply(this,args);refresh();sync();return result;};}
  const previousControls=syncControls;syncControls=function(...args){const result=previousControls(...args);refresh();sync();return result;};
  const previousConnected=window.ClipEditor.connected;window.ClipEditor.connected=function(){previousConnected?.();failed='';loadLibrary().catch(error=>toast(error.message));};
  window.ClipSoundtrack={sync,timing:soundtrackTiming};refresh();if(connected)loadLibrary().catch(()=>{});
})();
