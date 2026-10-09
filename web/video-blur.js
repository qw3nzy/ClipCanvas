'use strict';

function activeVideoBlurs(regions, time) {
  return (regions||[]).filter(region=>region.enabled!==false&&time>=(region.start||0)&&(region.end==null||time<region.end));
}
function videoBlurSigma(strength, width, height) {
  return Math.max(.1,Math.max(1,Math.min(100,Number(strength)||50))*.001*Math.min(width,height));
}
if(typeof document==='undefined') {
  module.exports={activeVideoBlurs,videoBlurSigma};
} else (()=>{
  const panel=document.createElement('section');panel.id='videoBlurPanel';panel.className='editor-tool';
  panel.innerHTML=`<h2>Блюр отдельных областей</h2>
    <button id="addVideoBlur" class="button secondary full">Добавить область размытия</button>
    <label class="field">Область<select id="videoBlurSelect"><option value="">Нет областей</option></select></label>
    <div id="videoBlurControls" hidden>
      <label class="toggle-row"><span>Размывать область</span><input id="videoBlurEnabled" type="checkbox"></label>
      <label class="field">Название<input id="videoBlurName" maxlength="70"></label>
      <button id="editVideoBlur" class="button secondary full">Изменить область на кадре</button>
      <label class="range-field">Сила размытия <output id="videoBlurStrengthValue">50%</output><input id="videoBlurStrength" type="range" min="1" max="100" value="50"></label>
      <div class="field-pair"><label>Начало в исходнике<input id="videoBlurStart" placeholder="00:00" inputmode="decimal"></label><label>Конец в исходнике<input id="videoBlurEnd" placeholder="До конца" inputmode="decimal"></label></div>
      <p class="muted">Время — минуты:секунды исходного видео. Пустой конец означает до конца видео. Вырезки и нарезка учитываются.</p>
      <div class="action-pair"><button id="videoBlurWhole" class="button secondary">На всём видео</button><button id="removeVideoBlur" class="text-button">Удалить область</button></div>
    </div>
    <p id="videoBlurInfo" class="muted" role="status"></p>
    <p class="muted">Можно добавить несколько областей. Рамка неподвижна относительно исходного кадра. Размытие видно в предпросмотре и сохраняется при экспорте.</p>`;
  $('videoCropPanel').after(panel);
  const frameCanvas=document.createElement('canvas'),frameContext=frameCanvas.getContext('2d');
  const patchCanvas=document.createElement('canvas'),patchContext=patchCanvas.getContext('2d');
  let selected='',signature='';
  const regions=()=>state.pro?.video_blurs||[];
  const current=()=>regions().find(region=>region.id===selected);
  const checkpoint=()=>window.ClipStudio?.checkpoint();
  const invalidate=()=>{signature='';};
  function apply(patch) {const region=current();if(!region)return;Object.assign(region,patch);invalidate();changed();sync();}
  function sync() {
    const list=regions(),unavailable=!state.sourceId||busy()||restoring;
    if(!list.some(region=>region.id===selected))selected=list[0]?.id||'';
    const options=JSON.stringify(list.map(region=>[region.id,region.name]));
    if($('videoBlurSelect').dataset.options!==options){$('videoBlurSelect').replaceChildren();if(!list.length)$('videoBlurSelect').add(new Option('Нет областей',''));for(const region of list)$('videoBlurSelect').add(new Option(region.name||'Область размытия',region.id));$('videoBlurSelect').dataset.options=options;}
    $('videoBlurSelect').value=selected;const region=current();$('videoBlurControls').hidden=!region;
    if(region){$('videoBlurEnabled').checked=region.enabled!==false;$('videoBlurStrength').value=region.strength??50;$('videoBlurStrengthValue').value=(region.strength??50)+'%';
      for(const [id,value]of [['videoBlurName',region.name||''],['videoBlurStart',formatClipTime(region.start||0)],['videoBlurEnd',region.end==null?'':formatClipTime(region.end)]])if(document.activeElement!==$(id))$(id).value=value;}
    for(const id of ['addVideoBlur','videoBlurSelect','videoBlurEnabled','videoBlurName','editVideoBlur','videoBlurStrength','videoBlurStart','videoBlurEnd','videoBlurWhole','removeVideoBlur'])$(id).disabled=unavailable||(id==='addVideoBlur'&&list.length>=20);
    $('videoBlurInfo').textContent=!state.sourceId?'Сначала добавьте основное видео.':list.length?`Областей: ${list.length} из 20. Активны сейчас: ${activeVideoBlurs(list,video.currentTime||0).length}.`:'Выберите на кадре область, которую нужно размыть.';
  }
  async function pick(existing) {
    const source=state.sourceId,epoch=projectEpoch,id=existing?.id;
    const result=await ClipCrop.selectRegion(existing||{enabled:true,x:.35,y:.35,w:.3,h:.3},
      {title:'Выберите область размытия',applyLabel:'Применить область',infoPrefix:'Выбрано',
       hint:'Двигайте рамку или её углы. Потяните вне рамки, чтобы выбрать новую область. Размытие применяется внутри рамки.'});
    if(!result)return;
    if(source!==state.sourceId||epoch!==projectEpoch||busy()||restoring)throw Error('Проект изменился. Выберите область ещё раз.');
    if(id&&!regions().some(region=>region.id===id))throw Error('Область уже удалена.');
    if(!id&&regions().length>=20)throw Error('Можно добавить до 20 областей.');
    checkpoint();
    const {x,y,w,h}=result.region;
    if(existing)Object.assign(regions().find(region=>region.id===id),{x,y,w,h});
    else{state.pro.video_blurs ||= [];const region={id:ClipBrowser.randomUUID(),name:'Область '+(regions().length+1),enabled:true,x,y,w,h,strength:50,start:0,end:null};state.pro.video_blurs.push(region);selected=region.id;}
    invalidate();changed();sync();
  }
  $('addVideoBlur').onclick=handle(()=>{if(regions().length>=20)throw Error('Можно добавить до 20 областей.');return pick();});
  $('editVideoBlur').onclick=handle(()=>{const region=current();if(region)return pick(region);});
  $('videoBlurSelect').onchange=()=>{selected=$('videoBlurSelect').value;sync();};
  $('videoBlurEnabled').onchange=()=>{checkpoint();apply({enabled:$('videoBlurEnabled').checked});};
  $('videoBlurName').onchange=()=>{checkpoint();apply({name:$('videoBlurName').value.trim()||'Область размытия'});};
  $('videoBlurStrength').onpointerdown=checkpoint;
  $('videoBlurStrength').onkeydown=event=>{if(event.key.startsWith('Arrow')||['Home','End','PageUp','PageDown'].includes(event.key))checkpoint();};
  $('videoBlurStrength').oninput=()=>apply({strength:Number($('videoBlurStrength').value)});
  for(const id of ['videoBlurStart','videoBlurEnd'])$(id).onchange=handle(()=>{
    const start=parseTime($('videoBlurStart').value),end=$('videoBlurEnd').value.trim()?parseTime($('videoBlurEnd').value):null;
    if(start<0||start>=state.duration||!Number.isFinite(start)||end!=null&&(!Number.isFinite(end)||end<=start||end>state.duration+.001))throw Error('Задайте начало внутри видео и конец позже начала. Конец можно оставить пустым.');
    checkpoint();apply({start,end});
  });
  $('videoBlurWhole').onclick=()=>{checkpoint();apply({start:0,end:null});};
  $('removeVideoBlur').onclick=()=>{if(!current())return;checkpoint();state.pro.video_blurs=regions().filter(region=>region.id!==selected);selected='';invalidate();changed();sync();};
  function frame(image) {
    if(image!==video||image.readyState<2)return image;
    const active=activeVideoBlurs(regions(),image.currentTime||0);if(!active.length)return image;
    const width=image.videoWidth,height=image.videoHeight,key=JSON.stringify([state.sourceId,image.currentSrc,width,height,image.currentTime,active]);
    if(signature===key)return frameCanvas;
    if(frameCanvas.width!==width||frameCanvas.height!==height){frameCanvas.width=width;frameCanvas.height=height;}
    frameContext.clearRect(0,0,width,height);frameContext.drawImage(image,0,0,width,height);
    for(const region of active){
      const r=ClipCrop.rect(width,height,{...region,enabled:true}),sigma=videoBlurSigma(region.strength,width,height),pad=Math.ceil(sigma*3);
      patchCanvas.width=r.w+pad*2;patchCanvas.height=r.h+pad*2;
      patchContext.drawImage(frameCanvas,r.x,r.y,r.w,r.h,pad,pad,r.w,r.h);
      patchContext.drawImage(frameCanvas,r.x,r.y,1,r.h,0,pad,pad,r.h);
      patchContext.drawImage(frameCanvas,r.x+r.w-1,r.y,1,r.h,pad+r.w,pad,pad,r.h);
      patchContext.drawImage(frameCanvas,r.x,r.y,r.w,1,pad,0,r.w,pad);
      patchContext.drawImage(frameCanvas,r.x,r.y+r.h-1,r.w,1,pad,pad+r.h,r.w,pad);
      for(const dx of [0,1])for(const dy of [0,1])patchContext.drawImage(frameCanvas,r.x+dx*(r.w-1),r.y+dy*(r.h-1),1,1,dx?(pad+r.w):0,dy?(pad+r.h):0,pad,pad);
      frameContext.save();frameContext.beginPath();frameContext.rect(r.x,r.y,r.w,r.h);frameContext.clip();frameContext.filter=`blur(${sigma}px)`;frameContext.drawImage(patchCanvas,r.x-pad,r.y-pad);frameContext.restore();
    }
    signature=key;return frameCanvas;
  }
  for(const event of ['loadeddata','seeked','emptied'])video.addEventListener(event,invalidate);
  video.addEventListener('timeupdate',sync);
  const previousControls=syncControls;syncControls=function(...args){const result=previousControls(...args);sync();return result;};
  for(const key of ['changed','projectRestored','refreshButtons']){const previous=window.ClipEditor[key];window.ClipEditor[key]=function(...args){const result=previous?.apply(this,args);invalidate();sync();return result;};}
  const previousSource=activateSource;activateSource=async function(asset,keep=false,...args){if(!keep){state.pro ||= {};state.pro.video_blurs=[];selected='';invalidate();}return previousSource(asset,keep,...args);};
  window.ClipBlur={frame,active:activeVideoBlurs,sigma:videoBlurSigma};sync();
})();
