'use strict';

function videoCropSettings(value = {}) {
  const number = (key, fallback) => Number.isFinite(value[key]) ? value[key] : fallback;
  const x = Math.max(0,Math.min(.99,number('x',0))), y = Math.max(0,Math.min(.99,number('y',0)));
  return {enabled:value.enabled === true, x, y,
    w:Math.max(.01,Math.min(1-x,number('w',1))), h:Math.max(.01,Math.min(1-y,number('h',1)))};
}
function videoCropRect(width, height, value) {
  const crop = videoCropSettings(value);
  if (!crop.enabled) return {x:0,y:0,w:width,h:height};
  const w=Math.min(width,Math.max(2,Math.floor(width*crop.w+1e-9))), h=Math.min(height,Math.max(2,Math.floor(height*crop.h+1e-9)));
  return {x:Math.min(width-w,Math.floor(width*crop.x+1e-9)), y:Math.min(height-h,Math.floor(height*crop.y+1e-9)), w, h};
}
function videoCropAspect(value, ratio, sourceAspect) {
  const crop=videoCropSettings(value), cx=crop.x+crop.w/2, cy=crop.y+crop.h/2;
  if (!(ratio > 0 && sourceAspect > 0)) return crop;
  const q=ratio/sourceAspect, w=Math.min(2*Math.min(cx,1-cx),2*Math.min(cy,1-cy)*q), h=w/q;
  return videoCropSettings({enabled:true,x:cx-w/2,y:cy-h/2,w,h});
}
if (typeof document === 'undefined') {
  module.exports = {videoCropSettings,videoCropRect,videoCropAspect};
} else (() => {
  const panel=document.createElement('section');panel.id='videoCropPanel';panel.className='editor-tool';
  panel.innerHTML=`<h2>Кадрирование основного видео</h2>
    <label class="toggle-row"><span>Обрезать края видео</span><input id="videoCropEnabled" type="checkbox"></label>
    <button id="editVideoCrop" class="button secondary full">Выбрать область на кадре</button>
    <label class="field">Пропорции области<select id="videoCropRatio"></select></label>
    <div class="field-pair"><label>Убрать слева, %<input id="videoCropLeft" type="number" min="0" max="99" step=".1" value="0"></label><label>Справа, %<input id="videoCropRight" type="number" min="0" max="99" step=".1" value="0"></label></div>
    <div class="field-pair"><label>Убрать сверху, %<input id="videoCropTop" type="number" min="0" max="99" step=".1" value="0"></label><label>Снизу, %<input id="videoCropBottom" type="number" min="0" max="99" step=".1" value="0"></label></div>
    <div class="action-pair"><button id="fitVideoCrop" class="button secondary">Вписать в холст</button><button id="resetVideoCrop" class="text-button">Сбросить кадрирование</button></div>
    <p id="videoCropInfo" class="muted" role="status"></p><p class="muted">Для основного видео во всех клипах. Область вписывается в текущий макет; изменения видны в предпросмотре и сохраняются при экспорте.</p>`;
  $('videoEffectsPanel').before(panel);
  const dialog=document.createElement('dialog');dialog.id='videoCropDialog';dialog.className='video-crop-dialog';
  dialog.innerHTML=`<div class="dialog-heading"><h2>Выберите область кадра</h2><button id="cancelVideoCrop" class="icon-button" aria-label="Отменить кадрирование">×</button></div>
    <p class="muted">Двигайте рамку или её углы. Потяните за область вне рамки, чтобы выделить новую. Вырезанные края затемнены.</p>
    <label class="field">Пропорции<select id="cropDialogRatio"></select></label>
    <canvas id="videoCropCanvas" aria-label="Выбор области кадрирования мышью или касанием"></canvas>
    <p id="cropDialogInfo" class="muted" role="status"></p>
    <div class="action-pair"><button id="applyVideoCrop" class="button primary">Применить кадрирование</button><button id="cropDialogReset" class="button secondary">Весь кадр</button></div>`;
  document.body.append(dialog);
  for (const id of ['videoCropRatio','cropDialogRatio']) for (const [name,value] of [['Свободно','free'],['Как исходник','source'],['Как холст','canvas'],['16:9','16/9'],['9:16','9/16'],['1:1','1'],['4:5','4/5']]) $(id).add(new Option(name,value));
  const edgeFields=['videoCropLeft','videoCropRight','videoCropTop','videoCropBottom'];
  const checkpoint=()=>window.ClipStudio?.checkpoint();
  const current=()=>videoCropSettings(state.pro?.video_crop);
  const sourceRect=image=>videoCropRect(image.videoWidth||image.naturalWidth,image.videoHeight||image.naturalHeight,image===video?current():{});
  const aspect=()=> (video.videoWidth||state.sourceWidth)/(video.videoHeight||state.sourceHeight);
  const ratio=value=>value==='source'?aspect():value==='canvas'?state.width/state.height:value==='free'?0:value.split('/').reduce((a,b)=>Number(a)/Number(b));
  function apply(value) {state.pro ||= {};state.pro.video_crop=videoCropSettings(value);changed();sync();}
  function sync() {
    const crop=current(), unavailable=!state.sourceId||busy()||restoring;
    const selectedRatio=ratio($('videoCropRatio').value);
    if(selectedRatio>0&&Math.abs(crop.w/crop.h*aspect()-selectedRatio)>1e-7)$('videoCropRatio').value='free';
    $('videoCropEnabled').checked=crop.enabled;
    [crop.x,1-crop.x-crop.w,crop.y,1-crop.y-crop.h].forEach((amount,index)=>{if(document.activeElement!==$(edgeFields[index]))$(edgeFields[index]).value=Number((amount*100).toFixed(3));});
    for(const id of ['videoCropEnabled','editVideoCrop','videoCropRatio','fitVideoCrop','resetVideoCrop',...edgeFields])$(id).disabled=unavailable;
    const rect=videoCropRect(state.sourceWidth,state.sourceHeight,crop);
    $('videoCropInfo').textContent=!state.sourceId?'Сначала добавьте основное видео.':crop.enabled?`Оставлено ${rect.w} × ${rect.h} пикселей исходника.`:'Показан весь исходный кадр.';
    if(dialog.open&&(unavailable||draftSource!==state.sourceId||draftEpoch!==projectEpoch))dialog.close();
  }
  $('videoCropEnabled').onchange=()=>{checkpoint();apply({...current(),enabled:$('videoCropEnabled').checked});};
  for(const id of edgeFields)$(id).onchange=handle(()=>{
    const [left,right,top,bottom]=edgeFields.map(field=>finite($(field).value,0,99)/100);
    if(left+right>.99+1e-9||top+bottom>.99+1e-9)throw Error('Оставьте не менее 1% ширины и высоты исходного кадра.');
    checkpoint();$('videoCropRatio').value='free';apply({enabled:true,x:left,y:top,w:1-left-right,h:1-top-bottom});
  });
  $('videoCropRatio').onchange=()=>{const selected=ratio($('videoCropRatio').value);if(selected){checkpoint();apply(videoCropAspect(current(),selected,aspect()));}};
  $('resetVideoCrop').onclick=()=>{checkpoint();$('videoCropRatio').value='free';apply({});};
  $('fitVideoCrop').onclick=()=>{checkpoint();state.pro.framing.mode='manual';autoVideo();changed();};
  const cropCanvas=$('videoCropCanvas'), cropContext=cropCanvas.getContext('2d');
  let draft=null, draftSource=null, draftEpoch=0, gesture=null, regionResolve=null, infoPrefix='Оставлено';
  function paint() {
    if(!draft||video.readyState<2)return;
    const w=cropCanvas.width,h=cropCanvas.height,x=draft.x*w,y=draft.y*h,rw=draft.w*w,rh=draft.h*h;
    cropContext.drawImage(video,0,0,w,h);
    cropContext.fillStyle='rgba(0,0,0,.65)';
    cropContext.fillRect(0,0,w,y);cropContext.fillRect(0,y+rh,w,h-y-rh);cropContext.fillRect(0,y,x,rh);cropContext.fillRect(x+rw,y,w-x-rw,rh);
    cropContext.strokeStyle='#70f6bc';cropContext.lineWidth=2;cropContext.strokeRect(x,y,rw,rh);
    cropContext.fillStyle='#ffffff';for(const cx of [x,x+rw])for(const cy of [y,y+rh])cropContext.fillRect(cx-5,cy-5,10,10);
    const rect=videoCropRect(state.sourceWidth,state.sourceHeight,draft);
    $('cropDialogInfo').textContent=`${infoPrefix} ${rect.w} × ${rect.h} пикселей. Примените, чтобы увидеть результат в макете.`;
  }
  function selectRegion(value, options={}) {
    if(video.readyState<2)throw Error('Дождитесь загрузки кадра основного видео.');
    if(dialog.open||regionResolve)throw Error('Завершите текущий выбор области.');
    stopPlayback();draft={...videoCropSettings(value),enabled:true};draftSource=state.sourceId;draftEpoch=projectEpoch;
    cropCanvas.width=Math.min(960,video.videoWidth);cropCanvas.height=Math.round(cropCanvas.width/aspect());
    dialog.querySelector('h2').textContent=options.title||'Выберите область кадра';
    dialog.querySelector('p.muted').textContent=options.hint||'Двигайте рамку или её углы. Потяните за область вне рамки, чтобы выделить новую. Вырезанные края затемнены.';
    infoPrefix=options.infoPrefix||'Оставлено';
    $('applyVideoCrop').textContent=options.applyLabel||'Применить кадрирование';
    $('cancelVideoCrop').setAttribute('aria-label','Отменить выбор области');
    $('cropDialogRatio').value=options.ratio||'free';dialog.showModal();paint();
    return new Promise(resolve=>{regionResolve=resolve;});
  }
  $('editVideoCrop').onclick=handle(async()=>{
    const result=await selectRegion(current(),{ratio:$('videoCropRatio').value});
    if(!result)return;$('videoCropRatio').value=result.ratio;checkpoint();apply(result.region);
  });
  $('cancelVideoCrop').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{if(dialog.open)return;draft=null;gesture=null;const resolve=regionResolve;regionResolve=null;resolve?.(null);});
  $('cropDialogReset').onclick=()=>{draft=videoCropSettings({enabled:true});$('cropDialogRatio').value='free';paint();};
  $('cropDialogRatio').onchange=()=>{draft=videoCropAspect(draft,ratio($('cropDialogRatio').value),aspect());paint();};
  $('applyVideoCrop').onclick=handle(()=>{
    if(draftSource!==state.sourceId||draftEpoch!==projectEpoch||busy()||restoring)throw Error('Проект изменился. Выберите область ещё раз.');
    const result={region:{...draft},ratio:$('cropDialogRatio').value},resolve=regionResolve;
    regionResolve=null;draft=null;gesture=null;dialog.close();resolve?.(result);
  });
  const point=event=>{const box=cropCanvas.getBoundingClientRect();return {x:Math.max(0,Math.min(1,(event.clientX-box.left)/box.width)),y:Math.max(0,Math.min(1,(event.clientY-box.top)/box.height)),box};};
  cropCanvas.addEventListener('pointerdown',event=>{
    if(event.button!==0||!draft)return;event.preventDefault();const p=point(event),old={...draft};
    const right=old.x+old.w,bottom=old.y+old.h;
    const corner=[['nw',old.x,old.y],['ne',right,old.y],['sw',old.x,bottom],['se',right,bottom]].find(([,x,y])=>Math.abs(p.x-x)*p.box.width<18&&Math.abs(p.y-y)*p.box.height<18);
    const inside=p.x>=old.x&&p.x<=right&&p.y>=old.y&&p.y<=bottom;
    gesture={old,p,mode:corner?.[0]||(inside?'move':'new')};cropCanvas.setPointerCapture(event.pointerId);
  });
  cropCanvas.addEventListener('pointermove',event=>{
    if(!gesture)return;event.preventDefault();const p=point(event),{old,mode}=gesture;
    if(mode==='move')draft={...old,x:Math.max(0,Math.min(1-old.w,old.x+p.x-gesture.p.x)),y:Math.max(0,Math.min(1-old.h,old.y+p.y-gesture.p.y))};
    else {
      const anchor=mode==='new'?gesture.p:{x:mode.includes('w')?old.x+old.w:old.x,y:mode.includes('n')?old.y+old.h:old.y};
      const dx=p.x-anchor.x,dy=p.y-anchor.y,sx=dx<0?-1:1,sy=dy<0?-1:1,q=ratio($('cropDialogRatio').value)/aspect();
      let w=Math.max(.01,Math.abs(dx)),h=Math.max(.01,Math.abs(dy));
      if(q>0){w=Math.max(w,h*q);h=w/q;const scale=Math.min(1,(sx<0?anchor.x:1-anchor.x)/w,(sy<0?anchor.y:1-anchor.y)/h);w*=scale;h*=scale;}
      if(w>=.01&&h>=.01)draft=videoCropSettings({enabled:true,x:sx<0?anchor.x-w:anchor.x,y:sy<0?anchor.y-h:anchor.y,w,h});
    }
    paint();
  });
  for(const event of ['pointerup','pointercancel','lostpointercapture'])cropCanvas.addEventListener(event,()=>{gesture=null;});
  const previousControls=syncControls;syncControls=function(...args){const result=previousControls(...args);sync();return result;};
  for(const key of ['changed','projectRestored','refreshButtons']){const previous=window.ClipEditor[key];window.ClipEditor[key]=function(...args){const result=previous?.apply(this,args);sync();return result;};}
  const previousSource=activateSource;activateSource=async function(asset,keep=false,...args){if(!keep){state.pro ||= {};state.pro.video_crop=videoCropSettings();$('videoCropRatio').value='free';}return previousSource(asset,keep,...args);};
  window.ClipCrop={sourceRect,settings:videoCropSettings,rect:videoCropRect,selectRegion};sync();
})();
