'use strict';

function placedBannerGeometry(geometry, area, position) {
  const result = {...geometry};
  result.x = area.x + (area.w - result.w) / 2;
  result.y = position === 'top' ? area.y : position === 'bottom' ? area.y + area.h - result.h : area.y + (area.h - result.h) / 2;
  return result;
}
function clippedCaption(cue, start, end) {
  if (!cue?.words?.length || (start <= cue.start && end >= cue.end)) return cue;
  const words = cue.words.filter(word => word.end > start && word.start < end).map(word => ({...word,start:Math.max(start,word.start),end:Math.min(end,word.end)}));
  return {...cue, start:Math.max(start,cue.start), end:Math.min(end,cue.end), words,
    render_lines:undefined, text:words.map(word => word.text.trim()).join(' ')};
}
function formatClipTime(seconds) {
  const value = Math.round(Math.max(0, seconds) * 1000), hours = Math.floor(value / 3600000);
  const parts = [Math.floor(value / 60000) % 60, Math.floor(value / 1000) % 60];
  if (hours) parts.unshift(hours);
  return parts.map(part => String(part).padStart(2,'0')).join(':') + '.' + String(value % 1000).padStart(3,'0');
}
function clipBannerSpans(banners, plan) {
  const duration = plan.at(-1)?.end || 0, spans = [];
  for (const banner of banners) {
    if (!banner.enabled) continue;
    if (banner.timebase === 'output') {
      const start = Math.max(0,banner.start), end = Math.min(duration,banner.end);
      if (end > start) spans.push({start,end});
    } else for (const segment of plan.filter(item => item.kind === 'source')) {
      const start = Math.max(banner.start,segment.sourceStart), end = Math.min(banner.end,segment.sourceEnd);
      if (end > start) spans.push({start:segment.start+start-segment.sourceStart,end:segment.start+end-segment.sourceStart});
    }
  }
  return spans.sort((a,b) => a.start-b.start);
}
function clearedBannerSettings(current, id = null) {
  const result = {banners:structuredClone(current.banners || []),pro:structuredClone(current.pro || {}),editor:structuredClone(current.editor || {})};
  result.banners = id === null ? [] : result.banners.filter(banner => banner.id !== id);
  for (const override of Object.values(result.pro.clip_overrides || {})) {
    if (id === null) for (const key of ['banners','banner_schedules','banners_enabled','hidden_banner_ids']) delete override[key];
    else {
      if (override.banners) override.banners = override.banners.filter(banner => banner.id !== id);
      if (override.banner_schedules) delete override.banner_schedules[id];
      if (override.hidden_banner_ids) override.hidden_banner_ids = override.hidden_banner_ids.filter(value => value !== id);
    }
  }
  delete result.editor.bannerProposal;
  if (id === null) Object.assign(result,{banner:{...structuredClone(current.banner),enabled:false},bannerId:null});
  return result;
}
function resetProjectState(current, defaults, asset) {
  const next = structuredClone(defaults);
  for (const key of ['projectId','name','sourceId','duration','sourceWidth','sourceHeight']) next[key] = current[key];
  next.audioIndex = asset?.recommended_audio_stream_index ?? null;
  next.subtitleIndex = asset?.recommended_subtitle_stream_index ?? null;
  Object.assign(next,{banners:[],cuts:[],editor:{},studio:{},pro:{},splitMode:'ranges',ranges:current.duration?[{start:0,end:current.duration}]:[]});
  return next;
}
function enabledClipBanner(override, banners, id) {
  const hidden = override.banners_enabled === false ? (override.banners || banners).map(banner => banner.id) : override.hidden_banner_ids || [];
  return {...override,banners_enabled:true,hidden_banner_ids:[...new Set(hidden.filter(value => value !== id))]};
}
if (typeof document === 'undefined') {
  module.exports = {placedBannerGeometry, clippedCaption, formatClipTime, clipBannerSpans, clearedBannerSettings, resetProjectState, enabledClipBanner};
} else (() => {
  const editor = window.ClipEditor, checkpoint = () => window.ClipStudio?.checkpoint();
  const node = (tag, cls, text) => { const item = document.createElement(tag); item.className = cls || ''; if (text !== undefined) item.textContent = text; return item; };
  const button = (text, action, id) => { const item = node('button', 'button secondary', text); item.type = 'button'; item.onclick = handle(action); if (id) item.id = id; return item; };
  const group = title => { const item = node('section', 'editor-tool banner-step'); item.append(node('h3', '', title)); return item; };
  const version = node('div','release-note'); version.id = 'clipcanvasVersion';
  version.append(node('strong','','ClipCanvas 3.1.8'),node('span','','Блюр отдельных областей · Кадрирование · Своя музыка'));
  document.querySelector('.workspace-heading').before(version);
  const controls = $('multiBannerControls'), tools = document.querySelector('.banner-tools');
  tools.querySelector('h2').textContent = 'Видеобаннеры: настройка по шагам';
  tools.querySelector('.banner-library').open = false;
  const flow = node('nav','banner-flow'); flow.setAttribute('aria-label','Настройка видеобаннера'); tools.querySelector('h2').after(flow);
  let lastSelectedBanner = null;
  function jumpTo(target) {
    const panel = target.closest('.media-panel');
    if (panel.scrollHeight > panel.clientHeight && getComputedStyle(panel).overflowY !== 'visible') panel.scrollTop += target.getBoundingClientRect().top-panel.getBoundingClientRect().top-flow.offsetHeight-24;
    else target.scrollIntoView({block:'start'});
  }
  const position = group('2 · Положение и размер');
  position.innerHTML += '<p class="muted">Выберите готовое положение или перетащите баннер на видео. Потяните за угол, чтобы изменить размер.</p><div id="bannerPositions" class="action-pair"></div><label class="range-field">Ширина от холста <output id="bannerSizeValue"></output><input id="bannerSize" type="range" min="5" max="100" step="1"></label>';
  controls.append(position);
  const setGeometry = geometry => {
    const banner = editor.getBanner(); if (!banner) return;
    const dx = geometry.x-banner.geometry.x, dy = geometry.y-banner.geometry.y;
    for (const point of banner.motion || []) { if (point.x != null) point.x += dx; if (point.y != null) point.y += dy; }
    Object.assign(banner.geometry, geometry); editor.selectBanner(banner.id); changed();
  };
  for (const [id,label,preset] of [['bannerCenterVideo','Центр видео','video'],['bannerCenterCanvas','Центр холста','center'],['bannerTop','Сверху','top'],['bannerBottom','Снизу','bottom']]) {
    $('bannerPositions').append(button(label, () => {
      const banner = editor.getBanner(); if (!banner) return; checkpoint();
      const area = preset === 'video' ? state.geometry.video : {x:0,y:0,w:state.width,h:state.height};
      setGeometry(placedBannerGeometry(banner.geometry, area, preset));
    }, id));
  }
  $('bannerSize').onpointerdown = checkpoint; $('bannerSize').onkeydown = checkpoint;
  $('bannerSize').oninput = () => {
    const banner = editor.getBanner(); if (!banner) return;
    const geometry = banner.geometry, width = state.width * Number($('bannerSize').value) / 100, height = geometry.h * width / geometry.w;
    setGeometry({...geometry,x:geometry.x+(geometry.w-width)/2,y:geometry.y+(geometry.h-height)/2,w:width,h:height});
  };
  position.append($('editBannerPosition').parentElement);
  $('editBannerPosition').textContent = 'Точные координаты';
  const timing = group('3 · Когда показывать');
  controls.append(timing);
  const sourceTimes = $('multiBannerStart').closest('.field-pair');
  const timingHint = $('bannerDuration').nextElementSibling;
  const clipPicker = $('bannerPart').closest('label'); clipPicker.firstChild.textContent = 'Отрезок, для которого задаём время';
  const manager = node('section','clip-banner-manager'); manager.id = 'clipBannerManager';
  manager.innerHTML = '<h3>Баннеры выбранного клипа</h3><p class="muted">Здесь видны все баннеры и их показы. Удаление действует только в выбранном клипе.</p><details id="clipBannerDetails" open><summary id="clipBannerCount"></summary><div id="clipBannerList"></div></details><details id="clipBannerInactive"><summary>Не показываются в этом клипе</summary><div id="clipBannerInactiveList"></div></details>';
  $('bannerSelect').closest('label').after(manager); manager.querySelector('p').after(clipPicker);
  const clipRange = node('div','banner-clip-range');
  clipRange.innerHTML = '<p id="bannerClipContext" class="muted"></p><div class="field-pair"><label>Начало показа<input id="bannerClipStart" type="text" inputmode="decimal" placeholder="00:00.000" aria-describedby="bannerClipTimeHelp"></label><label>Конец показа<input id="bannerClipEnd" type="text" inputmode="decimal" placeholder="00:05.000" aria-describedby="bannerClipTimeHelp"></label></div><p id="bannerClipTimeHelp" class="muted">Минуты:секунды. Например, 00:10 → 00:15: показ с десятой до пятнадцатой секунды этого клипа.</p><label class="toggle-row"><span>Повторять этот показ в выбранном клипе</span><input id="bannerClipRepeat" type="checkbox"></label><label id="bannerClipPauseField" class="field">Пауза после конца показа<input id="bannerClipPause" type="text" inputmode="decimal" placeholder="00:20.000"><small>Через эту паузу баннер снова появится на ту же длительность.</small></label><p id="bannerClipDraft" class="muted" aria-live="polite"></p><button id="bannerClipApply" class="button primary full">Применить время к этому клипу</button><div class="action-pair"><button id="bannerClipWhole" class="button secondary">Весь выбранный клип</button><button id="bannerClipReset" class="text-button">Вернуть общее время</button></div>';
  timing.append(clipRange, $('bannerDuration'), timingHint);
  const sharedTiming = node('details','settings-details'); sharedTiming.id = 'bannerSharedTiming';
  sharedTiming.append(node('summary','','Общее расписание для нескольких клипов'),node('p','muted','Эти настройки относятся ко всем указанным клипам. Кнопка «Применить время к этому клипу» выше сохраняет отдельный диапазон только для выбранного отрезка.'));
  timing.append(sharedTiming); sharedTiming.append(sourceTimes);
  $('multiBannerStart').closest('label').firstChild.textContent = 'Начало общей привязки, с';
  $('multiBannerEnd').closest('label').firstChild.textContent = 'Конец общей привязки, с';
  const anchorHeading = $('bannerTimeline').previousElementSibling;
  anchorHeading.textContent = 'Общая привязка к кадрам'; sharedTiming.append(anchorHeading,$('bannerTimeline'));
  const advanced = document.querySelector('.pro-banner');
  advanced.querySelector('summary').textContent = 'Сложные настройки: движение, поворот и слои';
  for (const id of ['proBannerMode','proBannerStart','proBannerClips']) sharedTiming.append($(id).closest(id === 'proBannerStart' ? '.field-pair' : 'label'));
  const modeNames = {source:'Сохранённая привязка к кадрам исходника',each:'Один показ в клипе',start:'Один показ с начала клипа',end:'Один показ перед концом клипа',full:'Весь клип без перерыва',interval:'Несколько показов с паузами'};
  for (const option of $('proBannerMode').options) option.textContent = modeNames[option.value];
  $('proBannerMode').closest('label').firstChild.textContent = 'Как показывать баннер';
  $('proBannerClips').closest('label').firstChild.textContent = 'В каких клипах (пусто — во всех)';
  const gap = node('label','field'); gap.id = 'bannerGapField'; gap.innerHTML = 'Пауза между показами, с<input id="bannerRepeatGap" type="number" min="0" max="86400" step=".1"><small>После конца показа баннер исчезает на это время, затем появляется снова.</small>';
  $('proBannerStart').closest('.field-pair').after(gap);
  advanced.append($('proBannerInterval').closest('label'));
  $('proBannerInterval').closest('label').firstChild.textContent = 'Точный период: от начала до следующего начала, с';
  $('bannerRepeatGap').onchange = handle(() => {
    const duration = Number($('proBannerDuration').value), pause = finite($('bannerRepeatGap').value,0,86400);
    if (duration+pause>86400) throw Error('Длительность показа вместе с паузой должна быть не больше суток.');
    $('proBannerInterval').value = duration+pause; return $('proBannerInterval').onchange();
  });
  const changeDuration = $('proBannerDuration').onchange;
  $('proBannerDuration').onchange = function(...args) {
    if ($('proBannerMode').value === 'interval') $('proBannerInterval').value = Number($('proBannerDuration').value)+Number($('bannerRepeatGap').value);
    return changeDuration.apply(this,args);
  };
  const repeatExplanation = node('p','banner-explanation'); repeatExplanation.innerHTML = '<strong>Два разных повтора</strong><br>«Несколько показов» включает и выключает баннер по расписанию. «Повторять видео» ниже проигрывает файл по кругу, пока длится один показ.';
  sharedTiming.append(repeatExplanation);
  let clipTimingSignature = '';
  function timingContext() {
    const index = Number($('bannerPart').value) + 1, part = cuts()[index-1], base = editor.getBanner();
    if (!part || !base) return null;
    const override = state.pro.clip_overrides?.[index], plan = adSchedule(part), duration = plan.at(-1)?.end || 0;
    const banner = (override?.banners || state.banners).find(item => item.id === base.id);
    const local = override?.banner_schedules?.[base.id];
    const effective = banner && {...banner,schedule:local || banner.schedule};
    const removed = override?.banners_enabled === false || override?.hidden_banner_ids?.includes(base.id);
    return {index,part,base,plan,duration,local,effective,spans:effective && !removed ? clipBannerSpans(proScheduled([effective],duration,index),plan) : []};
  }
  function readClipRange(context) {
    const start = parseTime($('bannerClipStart').value), end = parseTime($('bannerClipEnd').value);
    if (start < 0 || end-start < .099 || end > context.duration+.001) throw Error('Укажите начало и конец внутри клипа: от 00:00 до '+formatClipTime(context.duration)+'. Конец должен быть позже начала минимум на 0,1 с.');
    const pause = $('bannerClipRepeat').checked ? parseTime($('bannerClipPause').value) : 0;
    if (end-start+pause > 86400) throw Error('Показ вместе с паузой должен быть не больше суток.');
    return {mode:$('bannerClipRepeat').checked?'interval':'each',start,duration:end-start,interval:end-start+pause,clips:[]};
  }
  function draftClipRange() {
    const context = timingContext(); if (!context) return;
    $('bannerClipPauseField').hidden = !$('bannerClipRepeat').checked;
    try {
      const schedule = readClipRange(context), second = schedule.start+schedule.interval;
      $('bannerClipDraft').textContent = 'Показ с '+formatClipTime(schedule.start)+' до '+formatClipTime(schedule.start+schedule.duration)+' · длительность '+formatClipTime(schedule.duration)+(schedule.mode === 'interval' ? (second<context.duration ? '. Следующий: '+formatClipTime(second)+'–'+formatClipTime(Math.min(context.duration,second+schedule.duration))+'.' : '. В этом клипе следующий показ уже не поместится.') : '.');
    } catch { $('bannerClipDraft').textContent = 'Укажите начало и конец показа, затем примените время к выбранному клипу.'; }
  }
  for (const id of ['bannerClipStart','bannerClipEnd','bannerClipPause']) $(id).oninput = draftClipRange;
  $('bannerClipRepeat').onchange = draftClipRange;
  function applyClipRange() {
    const context = timingContext(); if (!context) throw Error('Выберите клип и баннер.');
    if (!context.effective) throw Error('В отдельных настройках этого клипа нет выбранного баннера. Сначала скопируйте его в клип.');
    const schedule = readClipRange(context); checkpoint();
    const override = state.pro.clip_overrides[context.index] ||= {};
    Object.assign(override,enabledClipBanner(override,state.banners,context.base.id));
    (override.banner_schedules ||= {})[context.base.id] = schedule;
    clipTimingSignature = ''; changed(); refreshBanner();
    toast('Время баннера сохранено только для клипа '+context.index+'.');
  }
  $('bannerClipApply').onclick = handle(applyClipRange);
  $('bannerClipWhole').onclick = handle(() => {
    const context = timingContext(); if (!context) throw Error('Выберите клип.');
    $('bannerClipStart').value = formatClipTime(0); $('bannerClipEnd').value = formatClipTime(context.duration); $('bannerClipRepeat').checked = false; applyClipRange();
  });
  $('bannerClipReset').onclick = handle(() => {
    const context = timingContext(); if (!context?.local) return;
    checkpoint(); delete state.pro.clip_overrides[context.index].banner_schedules[context.base.id]; clipTimingSignature = ''; changed(); refreshBanner();
  });
  $('bannerPart').addEventListener('change',refreshBanner);
  let managerSignature = '';
  function clipInventory() {
    const index = Number($('bannerPart').value)+1, part = cuts()[index-1];
    if (!part) return {index,duration:0,entries:[],override:{}};
    const plan = adSchedule(part), duration = plan.at(-1)?.end || 0, override = state.pro.clip_overrides?.[index] || {};
    const entries = (override.banners || state.banners || []).map((banner,order) => {
      const effective = {...banner,schedule:override.banner_schedules?.[banner.id] || banner.schedule};
      const removed = override.banners_enabled === false || override.hidden_banner_ids?.includes(banner.id);
      const resolved = removed ? [] : proScheduled([effective],duration,index);
      return {banner,effective,order,spans:clipBannerSpans(resolved,plan),resolved,removed};
    });
    return {index,duration,entries,override};
  }
  function mutateClip(action) {
    if (busy() || restoring) throw Error('Дождитесь текущей задачи.');
    const inventory = clipInventory(); if (!inventory.duration) throw Error('Выберите существующий клип.');
    checkpoint(); const override = state.pro.clip_overrides[inventory.index] ||= {};
    action(override,inventory); managerSignature = ''; clipTimingSignature = ''; changed(); refreshBanner();
  }
  function removeClipBanner(id) {
    mutateClip(override => {
      override.hidden_banner_ids = [...new Set([...(override.hidden_banner_ids || []),id])];
      if (override.banner_schedules) delete override.banner_schedules[id];
    });
    toast('Баннер убран только из выбранного клипа. Ctrl+Z отменяет удаление.');
  }
  function removeOccurrence(id,occurrence) {
    mutateClip((override,inventory) => {
      const item = inventory.entries.find(item => item.banner.id === id); if (!item) return;
      const schedule = structuredClone(item.effective.schedule || {mode:'source'});
      schedule.excluded_occurrences = [...new Set([...(schedule.excluded_occurrences || []),occurrence])];
      (override.banner_schedules ||= {})[id] = schedule;
    });
    toast('Выбранный показ удалён. Другие показы сохранены.');
  }
  function refreshClipManager() {
    const inventory = clipInventory(), signature = JSON.stringify([state.projectContext,inventory,busy()]);
    if (signature === managerSignature) return; managerSignature = signature;
    const active = inventory.entries.filter(item => item.spans.length), inactive = inventory.entries.filter(item => !item.spans.length);
    $('clipBannerCount').textContent = 'Показываются: '+active.length+' · клип '+inventory.index;
    $('clipBannerList').replaceChildren(); $('clipBannerInactiveList').replaceChildren(); $('clipBannerInactive').hidden = !inactive.length;
    if (!active.length) $('clipBannerList').append(node('p','muted',inventory.override.banners_enabled === false ? 'Все баннеры в этом клипе выключены. Общие настройки не добавят их обратно.' : 'В этом клипе нет активных показов.'));
    for (const item of [...active,...inactive]) {
      const card = node('div','clip-banner-card'); card.dataset.bannerId = item.banner.id;
      card.append(node('strong','','Баннер '+(item.order+1)+' · '+(item.banner.name || 'Без названия')));
      card.append(node('p','muted',item.spans.length ? item.spans.slice(0,20).map(span=>formatClipTime(span.start)+'–'+formatClipTime(span.end)).join('; ')+(item.spans.length>20?'…':'') : item.removed ? 'Убран из этого клипа.' : 'Не попадает в этот клип или выключен.'));
      const actions = node('div','action-pair');
      if (state.banners.some(banner => banner.id === item.banner.id)) actions.append(button('Настроить время',() => {editor.selectBanner(item.banner.id);jumpTo(timing);}));
      if (item.removed) actions.append(button('Вернуть в клип',() => mutateClip(override => {Object.assign(override,enabledClipBanner(override,state.banners,item.banner.id));})));
      else actions.append(button('Удалить из клипа',() => removeClipBanner(item.banner.id)));
      card.append(actions);
      if (['interval','bubavpn'].includes(item.effective.schedule?.mode) && item.resolved.length) {
        card.append(node('p','muted','Повторы по расписанию — можно удалить отдельный показ:'));
        for (const show of item.resolved.slice(0,20)) {
          const occurrence = Number(show.id.split('-').at(-1));
          const remove = button('Удалить показ '+formatClipTime(show.start),() => removeOccurrence(item.banner.id,occurrence),'');
          remove.className = 'text-button'; remove.dataset.occurrence = occurrence; card.append(remove);
        }
      }
      for (const control of card.querySelectorAll('button')) control.disabled = busy();
      (item.spans.length ? $('clipBannerList') : $('clipBannerInactiveList')).append(card);
    }
    $('clearClipBanners').disabled = !inventory.duration || busy();
    $('restoreClipBanners').hidden = inventory.override.banners_enabled !== false;
  }
  const resetDialog = node('dialog'); resetDialog.id = 'settingsResetDialog';
  resetDialog.innerHTML = '<h2 id="settingsResetTitle"></h2><p id="settingsResetDescription"></p><div class="action-pair"><button id="settingsResetCancel" class="button secondary">Отмена</button><button id="settingsResetConfirm" class="button danger">Подтвердить сброс</button></div>';
  document.body.append(resetDialog); let resetAction = null;
  function confirmReset(title,description,action) {
    if (busy() || restoring) throw Error('Дождитесь текущей задачи.');
    const context = state.projectContext, source = state.sourceId;
    resetAction = async () => {
      if (busy() || restoring || context !== state.projectContext || source !== state.sourceId) throw Error('Проект изменился. Откройте сброс ещё раз.');
      await action();
    };
    $('settingsResetTitle').textContent = title; $('settingsResetDescription').textContent = description; resetDialog.showModal();
  }
  $('settingsResetCancel').onclick = () => resetDialog.close();
  resetDialog.addEventListener('close',() => { resetAction = null; });
  $('settingsResetConfirm').onclick = handle(async () => {
    $('settingsResetConfirm').disabled = true;
    try { await resetAction?.(); resetDialog.close(); } finally { $('settingsResetConfirm').disabled = false; }
  });
  const clipActions = node('div','action-pair');
  clipActions.append(button('Удалить все баннеры из этого клипа',() => confirmReset('Убрать все баннеры из выбранного клипа?','Общие и отдельные баннеры перестанут показываться только в клипе '+clipInventory().index+'. Другие клипы и файлы сохранятся. Ctrl+Z отменяет действие.',() => {mutateClip(override => {override.banners_enabled = false;delete override.banner_schedules;delete override.banners;delete override.hidden_banner_ids;});toast('В выбранном клипе больше нет баннеров.');}),'clearClipBanners'));
  clipActions.append(button('Разрешить баннеры в этом клипе',() => mutateClip(override => {override.banners_enabled = true;}),'restoreClipBanners'));
  manager.append(clipActions);
  function removeProjectBanners(id = null) {
    if (busy()) throw Error('Дождитесь текущей задачи.'); checkpoint();
    Object.assign(state,clearedBannerSettings(state,id)); delete state.geometry.editorBanner;
    editor.selectBanner(state.banners[0]?.id || null); selectLayer('video'); clipTimingSignature = ''; managerSignature = ''; changed(); refreshBanner();
  }
  $('removeBanner').textContent = 'Удалить баннер из всего проекта';
  $('removeBanner').onclick = handle(() => {const banner = editor.getBanner();if (banner) removeProjectBanners(banner.id);});
  manager.append(button('Очистить все настройки баннеров проекта',() => confirmReset('Очистить все настройки баннеров проекта?','Будут удалены все размещения, повторы и отдельные настройки баннеров во всех клипах. Файлы баннеров, шаблоны и готовые MP4 останутся. Ctrl+Z отменяет действие.',() => {removeProjectBanners();toast('Все настройки баннеров проекта очищены.');}),'clearProjectBanners'));
  async function resetCurrentProject() {
    clearTimeout(saveTimer); if (pendingSave) await pendingSave;
    checkpoint(); stopPlayback(); resetProjectIdentity();
    const context = state.projectContext;
    Object.assign(state,resetProjectState(state,PROJECT_DEFAULTS,sourceAsset),{projectContext:context});
    // Keep already-loaded media available for undo; the reset project has no ad/banner references.
    sourcePreview(); autoVideo(); fillTracks(); syncControls(); renderSubtitles(); renderParts(); resizeStage();
    window.ClipStudio?.projectRestored({keepUndo:true}); editor.projectRestored(); window.ClipPro?.projectRestored();
    $('advertInfo').textContent = 'Используется весь рекламный ролик со звуком.';
    $('sampleResult').removeAttribute('src'); $('sampleResult').hidden = true; $('sampleDownload').hidden = true;
    clipTimingSignature = ''; managerSignature = ''; if (state.duration) previewPart(0); changed();
    await saveProject(true); refreshBanner(); toast('Настройки проекта сброшены. Ctrl+Z отменяет сброс.');
  }
  const resetProject = button('Сбросить все настройки проекта',() => confirmReset('Сбросить все настройки проекта?','Будут очищены баннеры, реклама, субтитры, вырезки, нарезка, оформление, звук, требования и настройки отдельных клипов. Останется полный исходник одним клипом с начальными настройками. Название проекта, исходный файл, готовые результаты и другие проекты сохранятся. Ctrl+Z отменяет сброс.',resetCurrentProject),'resetProjectSettings');
  resetProject.classList.add('project-reset'); $('saveProject').before(resetProject);
  const sound = group('4 · Звук и эффекты');
  controls.append(sound);
  sound.append($('videoBannerControls'));
  const ducking = node('section','banner-ducking'); ducking.id = 'bannerDucking';
  ducking.append(node('h4','','Звук основного видео во время баннера'),$('proDucking').closest('label'),$('proDuckTrigger').closest('label'),$('proDuckVolume').closest('label'),$('proDuckHint'));
  ducking.append(node('p','muted','Приглушение слышно в предпросмотре и сохраняется в пробном и полном MP4. Звук самого баннера настраивается отдельно выше.'));
  sound.append(ducking);
  $('bannerVideoLoop').closest('label').querySelector('span').textContent = 'Повторять видео внутри одного показа';
  for (const id of ['bannerVideoOffset','bannerVideoSpeed']) advanced.append($(id).closest('label'));
  sound.append($('proOpacity').closest('label'), $('proFadeIn').closest('.field-pair'), $('bannerKeyEnabled').closest('details'));
  $('proFadeIn').closest('label').firstChild.textContent = 'Плавное появление, с';
  $('proFadeOut').closest('label').firstChild.textContent = 'Плавное исчезновение, с';
  $('proOpacity').closest('label').firstChild.textContent = 'Видимость ';
  $('proOpacity').onpointerdown = checkpoint; $('proOpacity').onkeydown = checkpoint;
  $('proOpacity').oninput = () => { const banner = editor.getBanner(); if (banner) { banner.opacity = Number($('proOpacity').value); $('proOpacityValue').textContent = Math.round(banner.opacity*100)+'%'; changed(); } };
  for (const id of ['bannerKeyEnabled','bannerKeyColor','bannerTolerance','bannerEdge']) { $(id).onpointerdown = checkpoint; $(id).onkeydown = checkpoint; }
  sound.append(advanced); advanced.append(document.querySelector('.banner-rotation'), $('saveBannerTemplate'));
  advanced.append($('bannerInstructions').closest('section').parentElement);
  const preview = group('5 · Посмотрите результат');
  preview.append(button('Посмотреть выбранный баннер', () => {
    const banner = editor.getBanner(); if (!banner) return;
    previewPart(Number($('bannerPart').value) || 0);
    const scheduled = window.ClipPro.previewBanners().find(item => item.id === banner.id || item.id.startsWith(banner.id+'-clip-'));
    if (!scheduled) { toast('Этот баннер не показывается в выбранном клипе. Измените список клипов или расписание.'); return; }
    const start = scheduled?.timebase === 'output' ? scheduled.start : editor.sourceToOutput(banner.start);
    $('seek').value = Math.max(0,start)+.02; $('seek').oninput(); editor.selectBanner(banner.id); requestDraw();
  }, 'previewBanner'));
  controls.append(position, timing, sound, preview);
  for (const [id,label,target] of [['bannerFlowFile','1 · Файл',tools.querySelector('label')],['bannerFlowPosition','2 · Положение',position],['bannerFlowTiming','3 · Время',timing],['bannerFlowSound','4 · Звук',sound]]) flow.append(button(label,()=>jumpTo(target),id));
  flow.append(button('5 · Посмотреть',()=>$('previewBanner').click(),'bannerFlowPreview'));

  function refreshBanner() {
    refreshClipManager();
    const banner = editor.getBanner(); if (!banner) return;
    if (document.activeElement !== $('bannerSize')) $('bannerSize').value = Math.min(100,Math.max(5,banner.geometry.w/state.width*100));
    $('bannerSizeValue').textContent = Math.round(banner.geometry.w/state.width*100)+'%';
    sourceTimes.hidden = Boolean(banner.schedule?.mode && banner.schedule.mode !== 'source');
    timing.querySelector('h3').textContent = '3 · Когда показывать · клип '+(Number($('bannerPart').value)+1);
    const schedule = banner.schedule || {mode:'source'}, mode = schedule.mode || 'source';
    const context = timingContext();
    if (context) {
      const {index,part,duration,spans,local,effective} = context;
      $('bannerClipContext').textContent = 'Клип '+index+' · исходник '+formatClipTime(part.start)+'–'+formatClipTime(part.end)+'. Длина готового клипа '+formatClipTime(duration)+'. Начало клипа — 00:00, время включает рекламные вставки.';
      const signature = JSON.stringify([state.projectContext,index,effective,duration,spans]);
      if (signature !== clipTimingSignature) {
        clipTimingSignature = signature;
        $('bannerClipStart').value = spans.length ? formatClipTime(spans[0].start) : '';
        $('bannerClipEnd').value = spans.length ? formatClipTime(['interval','bubavpn'].includes(effective.schedule?.mode) ? spans[0].end : spans.at(-1).end) : '';
        $('bannerClipRepeat').checked = Boolean(spans.length && effective?.schedule?.mode === 'interval');
        $('bannerClipPause').value = formatClipTime(Math.max(0,(effective?.schedule?.interval??30)-(effective?.schedule?.duration??10)));
        draftClipRange();
      }
      $('bannerClipReset').hidden = !local;
      $('bannerClipApply').disabled = duration < .1 || busy(); $('bannerClipWhole').disabled = duration < .1 || busy();
      $('bannerDuration').textContent = spans.length ? 'Показы в клипе '+index+': '+spans.slice(0,8).map(span => formatClipTime(span.start)+'–'+formatClipTime(span.end)).join('; ')+(spans.length>8?'…':'')+(local?' · своё время для этого клипа':'') : 'В этом клипе показа нет. Задайте диапазон выше или нажмите «Весь выбранный клип».';
      timingHint.textContent = effective?.schedule?.mode === 'source' && effective.timebase !== 'output' ? 'Сохранённый показ следует кадрам исходника и прерывается на рекламе. Применение нового диапазона выше задаст непрерывный показ в выбранном готовом клипе.' : 'Начало и конец относятся к выбранному готовому клипу. Другие клипы сохраняют своё время. Каждый повтор запускает видео баннера заново.';
    }
    for (const [id,value] of [['proBannerMode',mode],['proBannerStart',schedule.start??5],['proBannerDuration',schedule.duration??10],['proBannerInterval',schedule.interval??30],['proBannerClips',(schedule.clips||[]).join(', ')]]) if (document.activeElement !== $(id)) $(id).value = value;
    $('proBannerStart').disabled = ['full','start'].includes(mode);
    $('proBannerDuration').disabled = mode === 'full';
    $('proBannerInterval').disabled = mode !== 'interval';
    $('proBannerStart').closest('.field-pair').hidden = ['source','full'].includes(mode);
    gap.hidden = mode !== 'interval';
    if (document.activeElement !== $('bannerRepeatGap')) $('bannerRepeatGap').value = Math.max(0,(schedule.interval??30)-(schedule.duration??10)).toFixed(2);
    $('proBannerStart').closest('label').firstChild.textContent = mode === 'end' ? 'Отступ от конца, с' : 'Начало в готовом клипе, с';
    for (const item of $('bannerPositions').children) item.disabled = banner.locked;
    $('bannerSize').disabled = banner.locked;
    if (lastSelectedBanner !== banner.id) {
      lastSelectedBanner = banner.id;
      tools.querySelector('.banner-library').open = false;
      if (document.body.dataset.section === 'design') requestAnimationFrame(()=>jumpTo(position));
    }
  }
  for (const key of ['changed','sync','projectRestored']) {
    const previous = editor[key]; editor[key] = function(...args) { const result = previous?.apply(this,args); refreshBanner(); return result; };
  }
  $('bannerSelect').addEventListener('change',refreshBanner);

  const warningDialog = node('dialog'); warningDialog.id = 'exportWarningDialog';
  warningDialog.innerHTML = '<h2>Экспорт с предупреждениями</h2><p id="exportWarningSummary"></p><ul id="exportWarningList"></ul><p class="muted">Экспорт использует текущие настройки. Замечания сохранятся вместе с результатом.</p><div class="action-pair"><button id="exportWarningBack" class="button secondary">Вернуться к настройкам</button><button id="exportWarningContinue" class="button primary">Всё равно экспортировать</button></div>';
  document.body.append(warningDialog);
  const exportIntro = group('Экспорт с предупреждениями'); exportIntro.classList.add('export-intro'); exportIntro.id = 'exportIntro';
  exportIntro.append(node('p','','Замечания к баннерам, субтитрам, длительности и размеру файла показываются перед экспортом. Вы сами выбираете: вернуться или экспортировать текущий монтаж.'),node('p','muted','Готовый MP4 сохраняется, даже если превышен заданный размер.'));
  const exportActions = node('div','action-pair');
  const quickExport = button('Экспортировать клипы',()=>$('exportButton').click(),'quickExport'); quickExport.className = 'button primary';
  exportActions.append(quickExport,button('Пробный MP4',()=>$('renderOne').click(),'quickSample'));
  exportIntro.append(exportActions); $('editorPaneexport').prepend(exportIntro);
  let warningResolve = null;
  function confirmWarnings(issues, summary = '') {
    if (warningResolve) throw Error('Завершите текущее подтверждение.');
    $('exportWarningSummary').textContent = summary;
    const list = $('exportWarningList'); list.replaceChildren();
    for (const issue of issues) list.append(node('li','', (issue.part ? 'Клип '+issue.part+': ' : '')+issue.message));
    return new Promise(resolve => { warningResolve = resolve; warningDialog.showModal(); });
  }
  const answer = value => { const resolve = warningResolve; warningResolve = null; warningDialog.close(); resolve?.(value); };
  $('exportWarningBack').onclick = () => answer(false); $('exportWarningContinue').onclick = () => answer(true);
  warningDialog.addEventListener('cancel', event => { event.preventDefault(); answer(false); });
  warningDialog.addEventListener('close', () => { if (warningResolve) answer(false); });
  const captionContext = () => JSON.stringify([state.sourceId,state.audioIndex,state.subtitleIndex,state.speechLanguage,state.projectContext]);
  const captionSignature = () => ClipBrowser.sha256(ClipSubtitles.sourceFingerprint(state));
  const originalStart = startJob;
  startJob = async function(endpoint,payload,type) {
    payload = structuredClone(payload);
    const caption = ['transcribe','extract','subtitle-review'].includes(type), beforeCaption = caption ? ClipSubtitles.sourceFingerprint(state) : null;
    const signature = caption ? await captionSignature() : null;
    if (caption && beforeCaption !== ClipSubtitles.sourceFingerprint(state)) throw Error('Субтитры изменились. Повторите распознавание.');
    const context = type === 'subtitle-review' ? captionContext() : null;
    if (caption) payload.client_snapshot = signature;
    if (['/api/render','/api/sample'].includes(endpoint)) {
      const snapshot = JSON.stringify(projectData().settings), result = await post('/api/studio/preflight',payload);
      if (result.issues.length && !await confirmWarnings(result.issues, 'Проверьте замечания для выбранного экспорта.')) return {status:'cancelled', files:[]};
      if (snapshot !== JSON.stringify(projectData().settings)) throw Error('Настройки изменились. Повторите экспорт.');
    }
    const job = await originalStart(endpoint,payload,type);
    if (context) job.client_context = context;
    if (job.issues?.length) showJobWarnings(job.issues);
    return job;
  };
  const warningPanel = node('ul','notice'); warningPanel.id = 'jobWarnings'; $('downloadList').after(warningPanel);
  function showJobWarnings(issues) { warningPanel.replaceChildren(); for (const issue of issues || []) warningPanel.append(node('li','',issue.message)); warningPanel.hidden = !warningPanel.childElementCount; }
  showJobWarnings([]);
  const originalApply = applyJobResult;
  applyJobResult = async function(job,type) {
    if (['transcribe','extract'].includes(type) && job.status === 'done') {
      const before = ClipSubtitles.sourceFingerprint(state), signature = await captionSignature();
      if (!job.request?.client_snapshot || job.request.client_snapshot !== signature || before !== ClipSubtitles.sourceFingerprint(state)) { toast('Исходник, дорожка, язык или текст изменились. Субтитры сохранены в истории и не применены.'); return; }
      job = {...job,cues:ClipSubtitles.mergeRecognition(state.subtitles.cues,job.cues || [],false,state.editor?.subtitleDeleted || [])};
      checkpoint();
    }
    if (job.issues) showJobWarnings(job.issues);
    return originalApply(job,type);
  };
  const previousCue = activeCue;
  activeCue = function(time) { const cue = previousCue(time), segment = previewIndex === null ? null : segments[playSegment]; return segment?.kind === 'source' ? clippedCaption(cue,segment.sourceStart,segment.sourceEnd) : cue; };

  // One confirmation covers the whole batch, including warnings found later.
  const originalPost = post;
  post = async function(endpoint,payload) {
    if (endpoint === '/api/batch' || /\/api\/batches\/[^/]+\/decision$/.test(endpoint) && payload.action !== 'reject' || /\/api\/jobs\/[^/]+\/retry$/.test(endpoint)) {
      const batch = /\/batches\/([^/]+)\/decision$/.exec(endpoint);
      let issues = [];
      if (batch) { const job = await api('/api/batches/'+batch[1]); issues = (job.batch_items || []).filter(row => payload.indices.includes(row.index)).flatMap(row => (row.issues || []).map(issue => ({...issue,message:row.relative_path+': '+issue.message}))); }
      const fullBatch = endpoint === '/api/batch';
      const retry = /\/jobs\/([^/]+)\/retry$/.exec(endpoint);
      if (retry) {
        const job = await api('/api/jobs/'+retry[1]);
        issues = job.type === 'batch' ? (job.batch_items || []).flatMap(row => row.issues || []) : job.issues || [];
        if (['render','sample'].includes(job.type) && job.request) issues = [...issues,...(await originalPost('/api/studio/preflight',job.request)).issues];
      }
      if (fullBatch) issues.push({message:'Замечания к длительности, размеру файла, баннерам и субтитрам не останавливают пакет. Для каждого видео они будут показаны в таблице и отчёте.'});
      if ((fullBatch || issues.length) && !await confirmWarnings(issues,'Единое решение для всех выбранных элементов.')) throw Error('Экспорт отменён. Настройки сохранены.');
    }
    return originalPost(endpoint,payload);
  };

  const cleanup = group('История и библиотека материалов'); cleanup.id = 'historyCleanup';
  cleanup.append(node('p','muted','Очистка убирает записи из списков. Проекты, исходные файлы, результаты, модели, шаблоны и настройки сохраняются. Активные задачи и связанные этапы защищены.'));
  cleanup.append(button('Очистить историю и библиотеку',async () => {
    const counts = await api('/api/history');
    $('historyCleanupDescription').textContent = 'Будут очищены: история открытых видео ('+counts.opened_videos.length+'), завершённые, ошибочные и отменённые задачи ('+counts.jobs+'), операции с проектами ('+counts.project_operations.length+'), общий список материалов ('+counts.assets+'). Защищённых задач: '+counts.protected_jobs+'. Исходные файлы и готовые результаты сохранятся.';
    clearDialog.showModal();
  },'clearHistory'));
  const results = node('details','settings-details'); results.append(node('summary','','Сохранённые результаты'), node('div','download-list')); results.lastChild.id = 'savedResults';
  results.addEventListener('toggle',async () => { if (results.open) renderSavedResults(); }); cleanup.append(results);
  $('editorPanesettings').append(cleanup);
  const events = node('details','settings-details'); events.innerHTML = '<summary>История открытых видео и действий с проектами</summary><ul id="historyEvents"></ul>';
  cleanup.append(events);
  async function refreshEvents() { const result = await api('/api/history'), list = $('historyEvents'); list.replaceChildren(); for (const event of [...result.opened_videos,...result.project_operations].sort((a,b) => b.at.localeCompare(a.at))) list.append(node('li','',(event.action ? ({save:'Сохранение',open:'Открытие',delete:'Удаление'}[event.action] || event.action) : 'Видео')+': '+event.name)); }
  events.addEventListener('toggle',() => { if (events.open) refreshEvents().catch(error=>toast(error.message)); });
  const clearDialog = node('dialog'); clearDialog.id = 'historyCleanupDialog';
  clearDialog.innerHTML = '<h2>Очистить списки?</h2><p id="historyCleanupDescription"></p><div class="action-pair"><button id="historyCleanupCancel" class="button secondary">Отмена</button><button id="historyCleanupConfirm" class="button primary">Очистить</button></div>';
  document.body.append(clearDialog); $('historyCleanupCancel').onclick = () => clearDialog.close();
  const library = $('projectList').closest('.editor-library');
  document.querySelector('.editor-grid').append(library);
  library.prepend(node('p','muted','Очистка истории сохраняет проекты. Для удаления настроек отдельного проекта нажмите «Удалить проект» рядом с его названием. Исходные файлы и готовые MP4 останутся.'));
  library.prepend(button('Очистить историю и материалы',()=>$('clearHistory').click(),'projectHistoryCleanup'));
  const projectDialog = node('dialog'); projectDialog.id = 'projectDeleteDialog';
  projectDialog.innerHTML = '<h2>Удалить сохранённый проект?</h2><p id="projectDeleteName"></p><p>Будут удалены настройки этого проекта. Исходные видео, баннеры, шаблоны и готовые результаты сохранятся.</p><div class="action-pair"><button id="projectDeleteCancel" class="button secondary">Отмена</button><button id="projectDeleteConfirm" class="button danger">Удалить проект</button></div>';
  document.body.append(projectDialog);
  let deletingProject = null;
  function deleteProject(project) { if (busy()) throw Error('Дождитесь текущей задачи.'); deletingProject = project; $('projectDeleteName').textContent = project.name || 'Проект'; projectDialog.showModal(); }
  $('projectDeleteCancel').onclick = () => projectDialog.close();
  $('projectDeleteConfirm').onclick = handle(async () => {
    const project = deletingProject; if (!project) return;
    $('projectDeleteConfirm').disabled = true;
    try {
      if (busy()) throw Error('Дождитесь текущей задачи.');
      await pendingSave?.catch(()=>{});
      if (state.projectId === project.id) clearTimeout(saveTimer);
      await api('/api/projects/'+project.id,{method:'DELETE'});
      if (state.projectId === project.id) $('newProject').click();
      try { const saved = JSON.parse(localStorage.getItem(STORAGE)); if (saved?.id === project.id) localStorage.removeItem(STORAGE); } catch {}
      await refreshHistory(); projectDialog.close(); toast('Проект удалён. Исходные файлы и результаты сохранены.');
    } finally { $('projectDeleteConfirm').disabled = false; }
  });
  let historyRevision = null;
  function clearBrowserHistory() {
    localStorage.removeItem('clipcanvas-last-batch');
    $('historyEvents').replaceChildren();
    state.editor ||= {}; delete state.editor.boundaryJobId; delete state.editor.bannerProposal;
    window.ClipPro?.historyCleared()?.catch(error => toast(error.message));
    if (!state.activeJob) { $('jobPanel').hidden = true; $('downloadList').replaceChildren(); showJobWarnings([]); }
    // Preserve the current project and its media references in the browser.
  }
  async function syncHistory() {
    const result = await api('/api/history'), saved = localStorage.getItem('clipcanvas-history-revision');
    if (saved !== String(result.revision)) {
      clearBrowserHistory();
      try {
        const project = JSON.parse(localStorage.getItem(STORAGE));
        if (project?.settings?.activeJob && !result.protected_job_ids.includes(project.settings.activeJob.id)) project.settings.activeJob = null;
        if (project) localStorage.setItem(STORAGE,JSON.stringify(project));
      } catch {}
    }
    historyRevision = result.revision; localStorage.setItem('clipcanvas-history-revision',String(result.revision));
  }
  $('historyCleanupConfirm').onclick = handle(async () => {
    $('historyCleanupConfirm').disabled = true;
    try { const result = await post('/api/history/clear',{}); historyRevision = result.revision; localStorage.setItem('clipcanvas-history-revision',String(result.revision)); clearBrowserHistory(); await Promise.all([refreshAssets(),refreshHistory(),renderSavedResults()]); clearDialog.close(); toast('История и библиотека очищены. Проекты и файлы сохранены.'); }
    finally { $('historyCleanupConfirm').disabled = false; }
  });
  async function renderSavedResults() { try { const result = await api('/api/results'); renderLinks(result.jobs.flatMap(job => job.files || []),$('savedResults')); } catch (error) { toast(error.message); } }
  const priorActivate = activateSource;
  activateSource = async function(...args) { const result = await priorActivate(...args); if (!args[1]) post('/api/history/open',{source_id:state.sourceId}).catch(()=>{}); return result; };
  const priorConnected = editor.connected;
  editor.connected = function(...args) { const result = priorConnected?.apply(this,args); syncHistory().catch(()=>{}); return result; };
  if (connected) syncHistory().catch(()=>{});
  const priorRefreshButtons = editor.refreshButtons;
  editor.refreshButtons = function(...args) {
    const result = priorRefreshButtons?.apply(this,args);
    resetProject.disabled = !state.sourceId || busy(); refreshClipManager(); return result;
  };
  window.ClipUsability = {confirmWarnings,captionContext,captionSignature,syncHistory,placedBannerGeometry,clippedCaption,refreshBanner,deleteProject};
  refreshBanner();
  if (connected) refreshHistory().catch(()=>{});
})();
