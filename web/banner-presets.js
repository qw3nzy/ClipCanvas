'use strict';

function bubavpnGeometry(width, height, aspect = 1248/496) {
  const area = width*height*.27;
  let w = Math.sqrt(area*aspect), h = w/aspect;
  if (w > width) {w=width;h=area/w;}
  if (h > height) {h=height;w=area/h;}
  return {x:(width-w)/2,y:(height-h)/2,w,h,fit:'stretch',rotation:0};
}
if (typeof document === 'undefined') {
  module.exports = {bubavpnGeometry};
} else (() => {
  const editor = window.ClipEditor, tools = document.querySelector('.banner-tools');
  const label = document.createElement('label'); label.className='field';label.textContent='Преднастройка баннера';
  const picker = document.createElement('select');picker.id='bannerPreset';picker.setAttribute('aria-label','Преднастройка баннера');
  picker.add(new Option('Выберите преднастройку',''));picker.add(new Option('БубаVPN','bubavpn'));picker.options[1].disabled=true;label.append(picker);
  const description = document.createElement('p');description.className='muted';description.id='bannerPresetInfo';
  description.textContent='БубаVPN: готовое видео со звуком, без синего фона; не менее 25% площади, центр экрана и времени клипа. От 2 минут — дополнительный показ за каждую полную минуту.';
  const preset = document.createElement('section');preset.id='bannerPresets';preset.append(label,description);tools.querySelector('h2').after(preset);
  const mode = new Option('БубаVPN: середина и полные минуты','bubavpn');mode.hidden=true;$('proBannerMode').add(mode);
  let template=null,loading=null;
  async function load() {
    if (!connected || loading) return loading;
    loading=(async()=>{const result=await api('/api/banner-templates');template=result.templates.find(item=>item.name==='БубаVPN'&&item.schedule?.mode==='bubavpn');picker.options[1].disabled=!template;})();
    try {await loading;} finally {loading=null;}
  }
  picker.onchange=handle(async()=>{
    if (picker.value!=='bubavpn') return;
    if (!state.sourceId) {picker.value='';throw Error('Сначала добавьте основное видео.');}
    if (busy() || restoring) {picker.value='';throw Error('Дождитесь текущей задачи.');}
    const source=state.sourceId,epoch=projectEpoch;
    await load();if(!template){picker.value='';throw Error('Видео преднастройки не найдено. Обновите библиотеку баннеров.');}
    if(source!==state.sourceId||epoch!==projectEpoch){picker.value='';throw Error('Проект изменился. Выберите преднастройку ещё раз.');}
    const existing=state.banners.find(banner=>banner.asset_id===template.asset_id&&banner.schedule?.mode==='bubavpn');
    if(existing){editor.selectBanner(existing.id);toast('Преднастройка БубаVPN уже добавлена.');return;}
    const geometry=bubavpnGeometry(state.width,state.height);
    const current={...structuredClone(template),width:state.width,height:state.height,geometry,
      layout_rules:{...template.layout_rules,min_width:geometry.w,min_height:geometry.h}};
    await window.ClipWorkflow.insertTemplate(current);
    const duration=adSchedule(cuts()[Number($('bannerPart').value)||0]).at(-1)?.end||state.duration;
    previewPart(Number($('bannerPart').value)||0);
    const start=proScheduled([editor.getBanner()],duration,(Number($('bannerPart').value)||0)+1)[0]?.start||0;
    $('seek').value=Math.min(duration,start+Math.min(2,template.duration/2));$('seek').oninput();
    toast('БубаVPN добавлен: видео по центру, фон удалён, озвучка включена. Другие баннеры сохранены.');
  });
  const previousCanvas=setCanvas;
  setCanvas=function(...args){const result=previousCanvas(...args),presets=[...(state.banners||[]),...Object.values(state.pro.clip_overrides||{}).flatMap(override=>override.banners||[])].filter(banner=>banner.schedule?.mode==='bubavpn');for(const banner of presets){Object.assign(banner.geometry,bubavpnGeometry(state.width,state.height));Object.assign(banner.layout_rules,{min_width:banner.geometry.w,min_height:banner.geometry.h});}if(presets.length)changed();return result;};
  const previousConnected=editor.connected;editor.connected=function(){previousConnected?.();load().catch(()=>{});};
  $('refreshBannerTemplates').addEventListener('click',()=>load().catch(()=>{}));
  document.querySelector('[data-route="design"]').addEventListener('click',()=>load().catch(()=>{}));
  if(connected)load().catch(()=>{});
})();
