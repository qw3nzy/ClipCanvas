'use strict';

function videoEffectsSettings(value = {}) {
  const settings = {mirror: value.mirror === true};
  for (const key of ['brightness','contrast','saturation']) {
    const amount = value[key] ?? 1;
    settings[key] = Number.isFinite(amount) ? Math.max(0,Math.min(2,amount)) : 1;
  }
  return settings;
}
function videoEffectsFilter(value) {
  const settings = videoEffectsSettings(value);
  return ['brightness','contrast','saturation'].map(key => settings[key] === 1 ? '' :
    `${key === 'saturation' ? 'saturate' : key}(${settings[key]})`).filter(Boolean).join(' ');
}
if (typeof document === 'undefined') {
  module.exports = {videoEffectsSettings,videoEffectsFilter};
} else (() => {
  const panel = document.createElement('section'); panel.id='videoEffectsPanel';panel.className='editor-tool';
  panel.innerHTML = `<h2>Основное видео · отражение и цвет</h2>
    <label class="toggle-row"><span>Отразить слева направо</span><input id="videoMirror" type="checkbox"></label>
    <label class="range-field">Яркость <output id="videoBrightnessValue">100%</output><input id="videoBrightness" aria-label="Яркость основного видео" type="range" min="0" max="200" value="100"></label>
    <label class="range-field">Контраст <output id="videoContrastValue">100%</output><input id="videoContrast" aria-label="Контраст основного видео" type="range" min="0" max="200" value="100"></label>
    <label class="range-field">Насыщенность <output id="videoSaturationValue">100%</output><input id="videoSaturation" aria-label="Насыщенность основного видео" type="range" min="0" max="200" value="100"></label>
    <button id="resetVideoEffects" class="button secondary full">Сбросить отражение и цвет</button>
    <p class="muted">Для основного видео во всех клипах, включая размытый фон. 100% — исходный цвет; насыщенность 0% — чёрно-белое видео. Изменения видны сразу и сохраняются при экспорте. Надписи внутри самого видео тоже отражаются.</p>`;
  $('editorPanedesign').prepend(panel);
  const fields = [['videoBrightness','brightness'],['videoContrast','contrast'],['videoSaturation','saturation']];
  const checkpoint = () => window.ClipStudio?.checkpoint();
  function current() {return videoEffectsSettings(state.pro?.video_effects);}
  function sync() {
    const effects = current();$('videoMirror').checked=effects.mirror;
    for (const [id,key] of fields) {$(id).value=Math.round(effects[key]*100);$(id+'Value').value=Math.round(effects[key]*100)+'%';}
    for (const id of ['videoMirror','resetVideoEffects',...fields.map(([id])=>id)]) $(id).disabled=!state.sourceId||busy()||restoring;
  }
  function apply(value) {state.pro ||= {};state.pro.video_effects=videoEffectsSettings(value);changed();sync();}
  $('videoMirror').onchange=()=>{checkpoint();apply({...current(),mirror:$('videoMirror').checked});};
  for (const [id,key] of fields) {
    $(id).onpointerdown=checkpoint;
    $(id).onkeydown=event=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'].includes(event.key))checkpoint();};
    $(id).oninput=()=>apply({...current(),[key]:Number($(id).value)/100});
  }
  $('resetVideoEffects').onclick=()=>{checkpoint();apply({});};
  const previousFit=fitDraw;
  fitDraw=function(image,geometry) {
    if(image!==video)return previousFit(image,geometry);
    const effects=current(),filter=videoEffectsFilter(effects);
    ctx.save();
    try {
      if(effects.mirror){ctx.translate(2*geometry.x+geometry.w,0);ctx.scale(-1,1);}
      if(filter)ctx.filter=(ctx.filter==='none'?'':ctx.filter+' ')+filter;
      return previousFit(image,geometry);
    } finally {ctx.restore();}
  };
  const previousControls=syncControls;
  syncControls=function(...args){const result=previousControls(...args);sync();return result;};
  for(const key of ['changed','projectRestored','refreshButtons']){
    const previous=window.ClipEditor[key];window.ClipEditor[key]=function(...args){const result=previous?.apply(this,args);sync();return result;};
  }
  sync();
})();
