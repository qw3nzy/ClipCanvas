'use strict';

// These pure rules mirror clipcanvas_pro.py and are exercised without a browser.
function proInterpolate(points, time, key, fallback, hold = false) {
  if (!points?.length) return fallback;
  let previous = fallback;
  const values = [...points].sort((a, b) => a.time - b.time).map(p => [p.time, previous = p[key] ?? previous]);
  if (time <= values[0][0]) return values[0][1];
  for (let i = 1; i < values.length; i++) {
    const [a, x] = values[i - 1], [b, y] = values[i];
    if (time < b) return hold ? x : x + (y - x) * (time - a) / (b - a);
  }
  return values.at(-1)[1];
}
function bubavpnStarts(duration, bannerDuration) {
  const length = Math.min(duration,bannerDuration), count = Math.min(1000,Math.max(1,Math.floor(duration/60))), centers = [duration/2];
  for (let i=1;i<count;i++) {
    const edges = [length/2,...[...centers].sort((a,b)=>a-b),duration-length/2];
    let gap = [edges[0],edges[1]];
    for (let n=1;n<edges.length-1;n++) if (edges[n+1]-edges[n] >= gap[1]-gap[0]) gap = [edges[n],edges[n+1]];
    centers.push((gap[0]+gap[1])/2);
  }
  return {starts:centers.map(center=>center-length/2),length};
}
function proScheduled(banners, duration, index, schedules = {}) {
  return banners.flatMap(base => {
    const original = schedules[base.id] ? {...base, schedule:schedules[base.id]} : base;
    if (!original.enabled) return [];
    const s = {mode: 'source', start: 5, duration: 10, interval: 30, clips: [], ...original.schedule};
    if (s.clips.length && !s.clips.includes(index)) return [];
    if (s.mode === 'source') return s.excluded_occurrences?.includes(0) ? [] : [original];
    const preset = s.mode === 'bubavpn' ? bubavpnStarts(duration,s.duration) : null;
    const begin = s.mode === 'start' || s.mode === 'full' ? 0 : s.mode === 'end' ? Math.max(0, duration - s.start - s.duration) : s.start;
    const count = s.mode === 'interval' ? Math.min(1000, Math.max(0, Math.ceil((duration - begin) / s.interval))) : 1;
    return Array.from({length: preset?.starts.length ?? count}, (_, n) => {
      if (s.excluded_occurrences?.includes(n)) return null;
      const start = preset?.starts[n] ?? begin + n * s.interval, end = Math.min(duration, start + (preset?.length ?? (s.mode === 'full' ? duration : s.duration)));
      return {...original, id: `${original.id || 'banner'}-clip-${index}-${n}`, start, end, timebase: 'output', schedule: {mode: 'source'}};
    }).filter(b => b && b.end > b.start);
  });
}
function proClipBanners(banners, override = {}) {
  if (override.banners_enabled === false) return [];
  return (override.banners || banners).filter(banner => !override.hidden_banner_ids?.includes(banner.id)).map(banner => override.banner_schedules?.[banner.id] ? {...banner,schedule:override.banner_schedules[banner.id]} : banner);
}
function proAudioSettings(audio = {}) {
  return {mute_source:false,source_volume:1,advert_volume:1,ducking:false,duck_volume:.25,fade:0,normalize:false,limiter:true,target_lufs:-16,
    duck_trigger:audio.ducking?'sounding':'visible',...audio};
}
function proDuckGain(audio, activeBanners, canSound = () => true) {
  if (!audio.ducking) return 1;
  const active = activeBanners.some(banner => banner.enabled && (audio.duck_trigger === 'visible' || (banner.volume > 0 && canSound(banner))));
  return active ? audio.duck_volume ?? .25 : 1;
}
if (typeof document === 'undefined') {
  module.exports = {proInterpolate, proScheduled, proClipBanners, proAudioSettings, proDuckGain, bubavpnStarts};
} else (() => {
  const editor = window.ClipEditor;
  const node = (tag, cls, text) => { const n = document.createElement(tag); n.className = cls || ''; if (text !== undefined) n.textContent = text; return n; };
  const button = (text, fn, cls = 'button secondary') => { const n = node('button', cls, text); n.type = 'button'; n.onclick = handle(fn); return n; };
  const checkpoint = () => window.ClipStudio?.checkpoint();
  const guide=node('a','button secondary','Полное руководство');guide.href='manual.html';guide.target='_blank';$('closeHelp').before(guide);
  const defaults = () => ({audio: proAudioSettings(),
    video_effects: {mirror:false, brightness:1, contrast:1, saturation:1},
    video_crop: {enabled:false, x:0, y:0, w:1, h:1},
    video_blurs: [],
    soundtrack: {enabled:false,asset_id:null,volume:1,start:0,offset:0,loop:false,pause_for_ads:true},
    framing: {mode: 'manual', subject: 'Главный говорящий персонаж', x: .5, y: .5, interpolation: 'linear', points: []},
    delivery: {platform: 'custom', max_duration: 0, max_size_mb: 0, min_banner_width: 0, min_banner_height: 0, min_banner_seconds: 0, require_banner: false, require_subtitles: false, check_safe_zones: false, safe_top: .12, safe_bottom: .20, safe_right: .17, caption_lines: 2},
    text_layers: [], clip_overrides: {}});
  function ensure() { const d = defaults(); state.pro ||= {}; for (const key of Object.keys(d)) state.pro[key] = key === 'audio' ? proAudioSettings(state.pro.audio) : Array.isArray(d[key]) ? state.pro[key] || [] : {...d[key], ...state.pro[key]}; return state.pro; }
  let profiles = [], bannerTemplates = [], lastBanner = '', lastFocus = '', changing = false;
  const oldRender = renderConfig;
  renderConfig = function(sample = false) {
    const config = oldRender(sample), pro = ensure();
    for (const key of Object.keys(defaults())) config[key] = structuredClone(pro[key]);
    if (sample) {
      const index = Number($('sampleClip').value) + 1;
      config.clip_overrides = pro.clip_overrides[index] ? {'1': structuredClone(pro.clip_overrides[index])} : {};
      for (const banner of config.banners) if (banner.schedule?.clips?.length) banner.schedule.clips = banner.schedule.clips.includes(index) ? [1] : [1000];
    }
    return config;
  };
  function baseConfig() {
    if (state.sourceId) return renderConfig(false);
    const pro = ensure();
    return {source_id: '0'.repeat(32), canvas: {width: state.width, height: state.height}, video: structuredClone(state.geometry.video),
      background: structuredClone(state.background), banners: structuredClone(state.banners || []), advertising: {enabled: false},
      subtitles: {...structuredClone(state.subtitles), cues: []}, parts: [{start: 0, end: 1}], cuts: [], quality: state.quality, encoder: state.encoder, fps: state.fps, zip: false,
      ...structuredClone(pro)};
  }
  function owner() {
    const list = cuts();
    if (previewIndex === -1) {
      let offset = 0, remaining = playSegment;
      for (let i = 0; i < list.length; i++) { const plan = adSchedule(list[i]); if (remaining < plan.length) return {index: i + 1, part: list[i], offset, duration: plan.at(-1)?.end || 0}; remaining -= plan.length; offset += plan.at(-1)?.end || 0; }
    }
    const index = previewIndex ?? (Number($('partSelect').value) || 0), part = list[index] || {start: 0, end: state.duration};
    return {index: index + 1, part, offset: 0, duration: adSchedule(part).at(-1)?.end || 0};
  }
  function outputClock() { if (!state.duration) return 0; return previewIndex === null ? editor.sourceToOutput(video.currentTime || 0) : timelineOffset() - owner().offset; }
  function previewBanners() {
    if (!state.duration) return state.banners || [];
    const o = owner(), override = ensure().clip_overrides[o.index];
    return proScheduled(proClipBanners(state.banners || [],override), o.duration, o.index);
  }
  function previewDuckGain(banners) {
    if (!state.duration || (previewIndex !== null && segments[playSegment]?.kind === 'advert')) return 1;
    const pro = ensure(), audio = pro.clip_overrides[owner().index]?.audio || pro.audio;
    const active = banners.filter(banner => window.ClipVideoBanners.timing(banner).visible);
    return proDuckGain(audio,active,banner => Boolean(window.ClipVideoBanners.info(banner.asset_id)?.audio));
  }
  function previewAudio() {
    const pro=ensure(),audio={...pro.audio,...pro.clip_overrides[owner().index]?.audio};
    audio.mute_source=pro.audio.mute_source||audio.mute_source;
    return audio;
  }
  const priorDraw = draw;
  draw = function() {
    ensure();
    const override = state.duration ? state.pro.clip_overrides[owner().index] : null;
    const geometry = state.geometry.video, subtitles = state.subtitles;
    if (override?.video) state.geometry.video = override.video;
    if (override?.subtitles) state.subtitles = {...state.subtitles, ...override.subtitles};
    if (override?.subtitles_enabled !== undefined) state.subtitles = {...state.subtitles, enabled: override.subtitles_enabled};
    try { priorDraw(); drawTexts(override?.text_layers || state.pro.text_layers); } finally { state.geometry.video = geometry; state.subtitles = subtitles; }
  };
  const previousFit = fitDraw;
  fitDraw = function(image, geometry) {
    const framing = (state.duration ? ensure().clip_overrides[owner().index]?.framing : null) || ensure().framing;
    if (image !== video || geometry !== state.geometry.video || geometry.fit !== 'cover' || framing.mode === 'manual' || !image.videoWidth) return previousFit(image, geometry);
    const x = proInterpolate(framing.points, video.currentTime, 'x', framing.x, framing.interpolation === 'hold');
    const y = proInterpolate(framing.points, video.currentTime, 'y', framing.y, framing.interpolation === 'hold');
    const crop = window.ClipCrop?.sourceRect(image) || {x:0,y:0,w:image.videoWidth,h:image.videoHeight};
    const scale = Math.max(geometry.w / crop.w, geometry.h / crop.h), sw = geometry.w / scale, sh = geometry.h / scale;
    ctx.drawImage(window.ClipBlur?.frame(image)||image, crop.x+Math.max(0, Math.min(crop.w - sw, image.videoWidth * x - crop.x - sw / 2)), crop.y+Math.max(0, Math.min(crop.h - sh, image.videoHeight * y - crop.y - sh / 2)), sw, sh, geometry.x, geometry.y, geometry.w, geometry.h);
  };
  function drawBanner(image, b) {
    const clock = (b.timebase === 'output' ? outputClock() : video.currentTime) - b.start, g = b.geometry;
    const scale = proInterpolate(b.motion, clock, 'scale', 1), opacity = proInterpolate(b.motion, clock, 'opacity', 1);
    let alpha = (b.opacity ?? 1) * opacity;
    if (b.fade_in) alpha *= Math.max(0, Math.min(1, clock / b.fade_in));
    if (b.fade_out) alpha *= Math.max(0, Math.min(1, (b.end - b.start - clock) / b.fade_out));
    ctx.save(); ctx.globalAlpha = alpha;
    fitDraw(image, {...g, x: proInterpolate(b.motion, clock, 'x', g.x), y: proInterpolate(b.motion, clock, 'y', g.y), w: g.w * scale, h: g.h * scale}); ctx.restore();
  }
  function drawTexts(layers) {
    if (document.fullscreenElement && previewIndex === null) return;
    const inAd = previewIndex !== null && segments[playSegment]?.kind === 'advert';
    for (const t of layers) {
      const clock = t.timebase === 'source' ? video.currentTime : outputClock();
      if (!t.enabled || clock < t.start || clock >= t.end || inAd && t.timebase === 'source') continue;
      ctx.save(); ctx.font = `${t.bold ? 'bold ' : ''}${t.size}px "${t.font || 'Arial'}"`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      const x = state.width * t.x / 100, y = state.height * t.y / 100, lines = t.text.split('\n');
      ctx.strokeStyle = '#000'; ctx.lineWidth = t.background ? 12 : 4; ctx.fillStyle = t.color;
      lines.forEach((line, index) => { const at = y + (index - (lines.length - 1) / 2) * t.size * 1.15; ctx.strokeText(line, x, at); ctx.fillText(line, x, at); }); ctx.restore();
    }
  }

  const advanced = node('details', 'settings-details pro-banner');
  advanced.innerHTML = `<summary>Расписание, прозрачность и движение</summary><label class="field">Показывать<select id="proBannerMode"><option value="source">На выбранных кадрах исходника</option><option value="each">В каждой готовой части</option><option value="start">В начале каждой части</option><option value="end">В конце каждой части</option><option value="full">Всю каждую часть</option><option value="interval">Повторять в каждой части</option></select></label>
    <div class="field-pair"><label>Начало / отступ, с<input id="proBannerStart" type="number" min="0" value="5"></label><label>Длительность, с<input id="proBannerDuration" type="number" min=".1" value="10"></label></div><label class="field">Повтор через, с<input id="proBannerInterval" type="number" min=".1" value="30"></label><label class="field">Только части (пусто — все)<input id="proBannerClips" placeholder="1, 3, 5"></label>
    <label class="range-field">Прозрачность <output id="proOpacityValue">100%</output><input id="proOpacity" type="range" min="0" max="1" step=".01" value="1"></label><div class="field-pair"><label>Появление, с<input id="proFadeIn" type="number" min="0" max="10" step=".1" value="0"></label><label>Исчезновение, с<input id="proFadeOut" type="number" min="0" max="10" step=".1" value="0"></label></div>
    <label class="toggle-row">Сохранять пропорции<input id="proAspect" type="checkbox"></label><label class="toggle-row">Заблокировать положение и размер<input id="proLocked" type="checkbox"></label><div class="action-pair"><button id="proLayerUp" class="button secondary">Слой выше</button><button id="proLayerDown" class="button secondary">Слой ниже</button></div><label class="field">Ключевые кадры: время; X; Y; масштаб; прозрачность<textarea id="proMotion" rows="4" placeholder="0; 50; 100; 1; 0&#10;1; 100; 100; 1; 1"></textarea></label><button id="proApplyMotion" class="button secondary">Сохранить движение</button><p class="muted">Время отсчитывается от появления баннера. Масштаб 1 = текущий размер. Между точками — плавное движение.</p>`;
  $('multiBannerControls').append(advanced);
  function listNumbers(value) { if (!value.trim()) return []; const list = value.split(/[,\s]+/).map(Number); if (list.some(n => !Number.isInteger(n) || n < 1 || n > 1000)) throw Error('Укажите номера частей от 1 до 1000 через запятую.'); return [...new Set(list)]; }
  for (const id of ['proBannerMode','proBannerStart','proBannerDuration','proBannerInterval','proBannerClips','proOpacity','proFadeIn','proFadeOut','proAspect','proLocked']) $(id).onchange = handle(() => {
    const b = editor.getBanner(); if (!b) return;
    const schedule = {mode: $('proBannerMode').value, start: finite($('proBannerStart').value, 0, 86400), duration: finite($('proBannerDuration').value, .1, 86400), interval: finite($('proBannerInterval').value, .1, 86400), clips: listNumbers($('proBannerClips').value)};
    if (schedule.mode === 'interval' && schedule.interval < schedule.duration) throw Error('Интервал повторения должен быть не короче показа.');
    checkpoint(); Object.assign(b, {schedule, opacity: Number($('proOpacity').value), fade_in: Number($('proFadeIn').value), fade_out: Number($('proFadeOut').value), lock_aspect: $('proAspect').checked, locked: $('proLocked').checked}); changed(); sync();
  });
  $('proApplyMotion').onclick = handle(() => {
    const b = editor.getBanner(); if (!b) return;
    const points = $('proMotion').value.split('\n').filter(line => line.trim()).map(line => {
      const p = line.split(';').map(n => Number(n.trim().replace(',', '.')));
      if (p.length !== 5 || p.some(n => !Number.isFinite(n)) || p[0] < 0 || p[3] < .1 || p[3] > 4 || p[4] < 0 || p[4] > 1) throw Error('Строка движения: время; X; Y; масштаб 0,1–4; прозрачность 0–1.');
      return {time: p[0], x: p[1], y: p[2], scale: p[3], opacity: p[4]};
    }).sort((a, b) => a.time - b.time);
    if (new Set(points.map(p => p.time)).size !== points.length || points.length > 100) throw Error('До 100 точек с разными временами.');
    checkpoint(); b.motion = points; changed();
  });
  for (const [id, delta] of [['proLayerUp', 1], ['proLayerDown', -1]]) $(id).onclick = () => { const b = editor.getBanner(), index = state.banners.indexOf(b), at = index + delta; if (at < 0 || at >= state.banners.length) return; checkpoint(); [state.banners[index], state.banners[at]] = [state.banners[at], state.banners[index]]; editor.renderBannerList(); changed(); };

  const manual = node('section', 'pro-manual editor-tool');
  manual.innerHTML = `<h2>Профиль и настройки частей</h2><div class="field-pair"><label>Профиль заказчика<select id="proProfile"><option value="">Текущие настройки</option></select></label><label>Название<input id="proProfileName" placeholder="Сервис — Shorts"></label></div><div class="action-pair"><button id="proApplyProfile" class="button secondary">Применить профиль</button><button id="proSaveProfile" class="button secondary">Сохранить профиль</button><button id="proDeleteProfile" class="text-button">Удалить профиль</button></div>
    <details class="settings-details"><summary>Настройки отдельных частей</summary><p class="muted">Эти поля меняют только указанные клипы. Общий макет сохраняется.</p><label class="field">Номера частей<input id="proClipTargets" value="1"></label><div class="field-pair"><label>X видео<input id="proClipX" type="number"></label><label>Y видео<input id="proClipY" type="number"></label></div><div class="field-pair"><label>Ширина<input id="proClipW" type="number" min="16"></label><label>Высота<input id="proClipH" type="number" min="16"></label></div><label class="field">Заполнение<select id="proClipFit"><option value="contain">Вписать</option><option value="cover">Обрезать</option><option value="stretch">Растянуть</option></select></label><div class="field-pair"><label>Размер субтитров<input id="proClipSize" type="number" min="16" max="160"></label><label>Высота субтитров, %<input id="proClipYText" type="number" min="0" max="100"></label></div><label class="field">Цвет<input id="proClipColor" type="color"></label><label class="toggle-row">Скопировать текущие баннеры в эти части<input id="proClipBanners" type="checkbox"></label><div class="action-pair"><button id="proLoadClip" class="button secondary">Загрузить поля выбранной части</button><button id="proSaveClip" class="button primary">Применить к указанным</button><button id="proClearClip" class="text-button">Вернуть общие настройки</button></div><p id="proClipInfo" class="muted"></p></details>`;
  $('editorPanedesign').append(manual);
  function loadClip() { const index = Number($('proClipTargets').value.split(',')[0]) || owner().index, o = ensure().clip_overrides[index] || {}, g = o.video || state.geometry.video, sub = {...state.subtitles, ...o.subtitles}; for (const [id, value] of [['proClipX',g.x],['proClipY',g.y],['proClipW',g.w],['proClipH',g.h],['proClipFit',g.fit],['proClipSize',sub.size],['proClipYText',sub.y],['proClipColor',sub.color]]) $(id).value = value; }
  $('proLoadClip').onclick = loadClip;
  $('proSaveClip').onclick = handle(() => { const targets = listNumbers($('proClipTargets').value); if (!targets.length || targets.some(n => n > cuts().length)) throw Error('Выберите существующие части.'); const override = {video: {x: finite($('proClipX').value,-15360,15360), y: finite($('proClipY').value,-15360,15360), w: finite($('proClipW').value,16,state.width*4), h: finite($('proClipH').value,16,state.height*4), fit: $('proClipFit').value}, subtitles: {size: finite($('proClipSize').value,16,160,true), y: finite($('proClipYText').value,0,100), color: $('proClipColor').value}}; if ($('proClipBanners').checked) override.banners = structuredClone(state.banners); checkpoint(); for (const index of targets) ensure().clip_overrides[index] = {...ensure().clip_overrides[index],...structuredClone(override)}; changed(); sync(); });
  $('proClearClip').onclick = handle(() => { checkpoint(); for (const index of listNumbers($('proClipTargets').value)) delete ensure().clip_overrides[index]; changed(); loadClip(); sync(); });
  const clipBannerTools=node('div');
  clipBannerTools.innerHTML=`<p class="muted">Выберите баннер в списке выше. Эти координаты сохранятся только в указанных частях.</p><div class="field-pair"><label>X баннера<input id="proClipBannerX" type="number" value="0"></label><label>Y баннера<input id="proClipBannerY" type="number" value="0"></label></div><div class="field-pair"><label>Ширина баннера<input id="proClipBannerW" type="number" min="16" value="200"></label><label>Высота баннера<input id="proClipBannerH" type="number" min="16" value="100"></label></div><div class="action-pair"><button id="proReadClipBanner" class="button secondary">Загрузить положение баннера</button><button id="proSaveClipBanner" class="button secondary">Баннер только в этих частях</button></div>`;
  $('proClipInfo').before(clipBannerTools);
  $('proReadClipBanner').onclick=handle(()=>{const base=editor.getBanner();if(!base)throw Error('Выберите баннер.');const index=listNumbers($('proClipTargets').value)[0]||1,b=ensure().clip_overrides[index]?.banners?.find(b=>b.id===base.id)||base;for(const [id,key]of [['proClipBannerX','x'],['proClipBannerY','y'],['proClipBannerW','w'],['proClipBannerH','h']])$(id).value=b.geometry[key];});
  $('proSaveClipBanner').onclick=handle(()=>{const base=editor.getBanner(),targets=listNumbers($('proClipTargets').value);if(!base)throw Error('Выберите баннер.');if(!targets.length||targets.some(n=>n>cuts().length))throw Error('Выберите существующие части.');const geometry={x:finite($('proClipBannerX').value,-15360,15360),y:finite($('proClipBannerY').value,-15360,15360),w:finite($('proClipBannerW').value,16,state.width*4),h:finite($('proClipBannerH').value,16,state.height*4)};const updates=targets.map(index=>{const banners=structuredClone(ensure().clip_overrides[index]?.banners||state.banners),b=banners.find(b=>b.id===base.id);if(!b)throw Error('В этой части нет выбранного баннера. Скопируйте текущие баннеры в часть.');Object.assign(b.geometry,geometry);b.motion=[];return {index,banners};});checkpoint();for(const {index,banners}of updates){const override=ensure().clip_overrides[index]||={};override.banners=banners;}changed();sync();});

  const framingTools = node('details', 'settings-details');
  framingTools.innerHTML = `<summary>Вертикальное кадрирование</summary><label class="field">Режим<select id="proFramingMode"><option value="manual">Ручной макет</option><option value="center">Заполнить кадр по центру</option><option value="subject">Следовать за объектом с ИИ</option></select></label><div class="field-pair"><label>Центр X, %<input id="proFocusX" type="number" min="0" max="100" value="50"></label><label>Центр Y, %<input id="proFocusY" type="number" min="0" max="100" value="50"></label></div><label class="field">Объект<input id="proSubject" value="Главный говорящий персонаж"></label><label class="field">Между точками<select id="proFocusInterpolation"><option value="linear">Плавно</option><option value="hold">Сохранять до следующей точки</option></select></label><button id="proFindFocus" class="button secondary">Подобрать кадрирование с ИИ</button><p id="proFocusInfo" class="muted">Результат можно поправить вручную. ИИ анализирует выборочные кадры.</p>`;
  manual.append(framingTools);
  const focusEditor = node('div');
  focusEditor.innerHTML = `<label class="field">Точки кадрирования: время исходника; X%; Y%<textarea id="proFocusPoints" rows="4" placeholder="0; 50; 50&#10;10; 70; 50"></textarea></label><div class="action-pair"><button id="proSaveFocus" class="button secondary">Сохранить точки</button><button id="proClearFocus" class="text-button">Убрать точки</button></div>`;
  framingTools.append(focusEditor);
  $('proSaveFocus').onclick = handle(() => {
    const points = $('proFocusPoints').value.split('\n').filter(line => line.trim()).map(line => {
      const p = line.split(';').map(n => Number(n.trim().replace(',', '.')));
      if (p.length !== 3 || p.some(n => !Number.isFinite(n)) || p[0] < 0 || p[0] > state.duration || p[1] < 0 || p[1] > 100 || p[2] < 0 || p[2] > 100) throw Error('Точка: время исходника; X от 0 до 100; Y от 0 до 100.');
      return {time:p[0],x:p[1]/100,y:p[2]/100};
    }).sort((a,b)=>a.time-b.time);
    if (points.length > 1000 || new Set(points.map(p=>p.time)).size !== points.length) throw Error('До 1000 точек с разными временами.');
    checkpoint(); Object.assign(ensure().framing,{points,mode:'subject'}); Object.assign(state.geometry.video,{x:0,y:0,w:state.width,h:state.height,fit:'cover'}); changed(); sync();
  });
  $('proClearFocus').onclick = () => { checkpoint(); ensure().framing.points=[]; changed(); sync(); };
  for (const id of ['proFramingMode','proFocusX','proFocusY','proSubject','proFocusInterpolation']) $(id).onchange = handle(() => { checkpoint(); Object.assign(ensure().framing, {mode: $('proFramingMode').value, x: finite($('proFocusX').value,0,100)/100, y: finite($('proFocusY').value,0,100)/100, subject: $('proSubject').value, interpolation: $('proFocusInterpolation').value}); if (ensure().framing.mode !== 'manual') Object.assign(state.geometry.video, {x:0,y:0,w:state.width,h:state.height,fit:'cover'}); changed(); updateInspector(); });
  $('proFindFocus').onclick = handle(async () => { if (!state.sourceId) throw Error('Добавьте видео.'); const sourceId = state.sourceId, snapshot = JSON.stringify(baseConfig()); const job = await startJob('/api/pro/framing', {source_id: sourceId, framing: {...ensure().framing, mode:'subject'}}, 'framing'); if (job.status !== 'done') throw Error(job.error || 'Кадрирование остановлено.'); if (state.sourceId !== sourceId || JSON.stringify(baseConfig()) !== snapshot) { $('proFocusInfo').textContent = 'Проект изменился. Результат сохранён в истории; повторите подбор.'; return; } checkpoint(); ensure().framing = job.framing; Object.assign(state.geometry.video,{x:0,y:0,w:state.width,h:state.height,fit:'cover'}); $('proFocusInfo').textContent = `Готово: ${job.framing.points.length} точек. ` + (job.framing_issues || []).map(i=>i.message).join(' '); changed(); sync(); });

  const textTools = node('details', 'settings-details');
  textTools.innerHTML = `<summary>Текст, промокод и призыв к действию</summary><label class="field">Слой<select id="proTextSelect"><option value="">Новый текст</option></select></label><label class="field">Текст<textarea id="proText" rows="3" placeholder="Промокод CLIP"></textarea></label><div class="field-pair"><label>Появление, с<input id="proTextStart" type="number" min="0" value="0"></label><label>Исчезновение, с<input id="proTextEnd" type="number" min=".1" value="10"></label></div><div class="field-pair"><label>X центра, %<input id="proTextX" type="number" min="0" max="100" value="50"></label><label>Y центра, %<input id="proTextY" type="number" min="0" max="100" value="15"></label></div><div class="field-pair"><label>Размер<input id="proTextSize" type="number" min="16" max="160" value="48"></label><label>Цвет<input id="proTextColor" type="color" value="#ffffff"></label></div><div class="action-pair"><button id="proSaveText" class="button secondary">Сохранить текст</button><button id="proRemoveText" class="text-button">Удалить текст</button></div><p class="muted">Время итоговой части: один текст повторяется в каждом клипе. Переносы задаются Enter.</p>`;
  manual.append(textTools);
  $('proSaveText').onclick = handle(() => { const text = $('proText').value.trim(); if (!text) throw Error('Введите текст.'); const t = {id:$('proTextSelect').value || ClipBrowser.randomUUID(),text,enabled:true,timebase:'output',start:finite($('proTextStart').value,0,86400),end:finite($('proTextEnd').value,.1,86400),x:finite($('proTextX').value,0,100),y:finite($('proTextY').value,0,100),size:finite($('proTextSize').value,16,160,true),color:$('proTextColor').value,font:'Arial',bold:true,background:false}; if (t.end <= t.start) throw Error('Конец должен быть позже начала.'); checkpoint(); const list = ensure().text_layers, at = list.findIndex(row=>row.id===t.id); if(at<0)list.push(t);else list[at]=t; changed(); sync(); $('proTextSelect').value=t.id; });
  $('proRemoveText').onclick = () => { checkpoint(); ensure().text_layers = ensure().text_layers.filter(t=>t.id!==$('proTextSelect').value); changed(); sync(); };
  $('proTextSelect').onchange = () => { const t=ensure().text_layers.find(t=>t.id===$('proTextSelect').value); if(!t)return; for(const [id,key]of [['proText','text'],['proTextStart','start'],['proTextEnd','end'],['proTextX','x'],['proTextY','y'],['proTextSize','size'],['proTextColor','color']])$(id).value=t[key]; };

  const audioTools = node('details', 'settings-details');
  audioTools.innerHTML = `<summary>Звук экспорта</summary><div class="field-pair"><label>Громкость исходника, %<input id="proSourceVolume" type="number" min="0" max="300" value="100"></label><label>Громкость вставки, %<input id="proAdvertVolume" type="number" min="0" max="300" value="100"></label></div><label class="toggle-row">Делать основное видео тише при появлении баннера<input id="proDucking" type="checkbox"></label><label class="field">Когда делать тише<select id="proDuckTrigger"><option value="visible">При любом баннере, даже без звука</option><option value="sounding">Только при баннере со включённым звуком</option></select></label><label class="range-field">Громкость видео во время баннера <output id="proDuckVolumeValue">25%</output><input id="proDuckVolume" type="range" min="0" max="100" step="1" value="25"></label><p id="proDuckHint" class="muted">25% — четверть обычной громкости. Между показами звук возвращается. 0% — выключить звук видео; 100% — оставить как есть. Настройка действует для всех баннеров проекта.</p><label class="field">Плавное начало и конец, с<input id="proAudioFade" type="number" min="0" max="5" step=".1" value="0"></label><label class="toggle-row">Выравнивать громкость<input id="proNormalize" type="checkbox"></label><label class="toggle-row">Ограничить перегрузку<input id="proLimiter" type="checkbox" checked></label><p class="muted">Эти параметры применяются к готовому MP4. Громкость проигрывателя управляет прослушиванием.</p>`;
  $('editorPaneexport').prepend(audioTools);
  for (const id of ['proSourceVolume','proAdvertVolume','proDucking','proDuckTrigger','proDuckVolume','proAudioFade','proNormalize','proLimiter']) $(id).onchange = handle(() => { checkpoint(); Object.assign(ensure().audio,{source_volume:finite($('proSourceVolume').value,0,300)/100,advert_volume:finite($('proAdvertVolume').value,0,300)/100,ducking:$('proDucking').checked,duck_trigger:$('proDuckTrigger').value,duck_volume:finite($('proDuckVolume').value,0,100)/100,fade:finite($('proAudioFade').value,0,5),normalize:$('proNormalize').checked,limiter:$('proLimiter').checked}); changed(); });
  $('proDuckVolume').onpointerdown = checkpoint; $('proDuckVolume').onkeydown = checkpoint;
  $('proDuckVolume').oninput = () => {ensure().audio.duck_volume = finite($('proDuckVolume').value,0,100)/100;$('proDuckVolumeValue').textContent = $('proDuckVolume').value+'%';changed();};
  $('proDuckVolume').onchange = () => sync();

  const deliveryTools = node('details', 'settings-details');
  deliveryTools.innerHTML = `<summary>Требования заказчика и платформы</summary><label class="field">Платформа<select id="proPlatform"><option value="custom">Свои требования</option><option value="tiktok">TikTok</option><option value="shorts">YouTube Shorts</option></select></label><div class="field-pair"><label>Максимум готовой части, с (0 — без лимита)<input id="proMaxDuration" type="number" min="0" value="0"></label><label>Максимум файла, МБ (0 — без лимита)<input id="proMaxSize" type="number" min="0" value="0"></label></div><label class="toggle-row">Баннер обязателен<input id="proRequireBanner" type="checkbox"></label><label class="toggle-row">Субтитры обязательны<input id="proRequireSubtitles" type="checkbox"></label><div class="field-pair"><label>Минимум ширины баннера, px<input id="proMinW" type="number" min="0" value="0"></label><label>Минимум высоты, px<input id="proMinH" type="number" min="0" value="0"></label></div><label class="field">Минимум показа, с<input id="proMinSeconds" type="number" min="0" value="0"></label><label class="toggle-row">Проверять перекрытие интерфейсом<input id="proCheckSafe" type="checkbox"></label><div class="field-pair"><label>Занято сверху, %<input id="proSafeTop" type="number" min="0" max="45" value="12"></label><label>Занято снизу, %<input id="proSafeBottom" type="number" min="0" max="45" value="20"></label></div><label class="field">Занято справа, %<input id="proSafeRight" type="number" min="0" max="45" value="17"></label><p class="muted">Поля интерфейса — настраиваемый ориентир. Проверьте их на своём телефоне и для своей подписи.</p>`;
  $('editorPaneexport').prepend(deliveryTools);
  for (const id of ['proPlatform','proMaxDuration','proMaxSize','proRequireBanner','proRequireSubtitles','proMinW','proMinH','proMinSeconds','proCheckSafe','proSafeTop','proSafeBottom','proSafeRight']) $(id).onchange = handle(() => { checkpoint(); if(id==='proPlatform'&&$('proPlatform').value==='shorts')$('proMaxDuration').value=180; Object.assign(ensure().delivery,{platform:$('proPlatform').value,max_duration:finite($('proMaxDuration').value,0,86400),max_size_mb:finite($('proMaxSize').value,0,10000),require_banner:$('proRequireBanner').checked,require_subtitles:$('proRequireSubtitles').checked,min_banner_width:finite($('proMinW').value,0,3840),min_banner_height:finite($('proMinH').value,0,3840),min_banner_seconds:finite($('proMinSeconds').value,0,86400),check_safe_zones:$('proCheckSafe').checked,safe_top:finite($('proSafeTop').value,0,45)/100,safe_bottom:finite($('proSafeBottom').value,0,45)/100,safe_right:finite($('proSafeRight').value,0,45)/100}); changed(); syncSafe(); });
  function syncSafe() { const d=ensure().delivery; document.querySelector('.safe-top').style.height=d.safe_top*100+'%';document.querySelector('.safe-bottom').style.height=d.safe_bottom*100+'%';document.querySelector('.safe-right').style.width=d.safe_right*100+'%'; }
  const phone = button('Вид платформы',()=>{ $('showSafeZones').checked=!$('showSafeZones').checked;$('safeZones').hidden=!$('showSafeZones').checked; $('stage').classList.toggle('platform-preview',$('showSafeZones').checked); });
  document.querySelector('.preview-tools').append(phone);

  const splitTools = node('section', 'editor-tool pro-split');
  splitTools.innerHTML = `<h3>Целиком или по длине готового клипа</h3><div class="action-pair"><button id="proWhole" class="button secondary">Весь ролик одним файлом</button><label>Готовая часть, с<input id="proFinalSeconds" type="number" min=".2" value="60"></label><button id="proSplitFinal" class="button secondary">Нарезать с учётом рекламы</button></div><p class="muted">Границы стараются сохранить целые реплики. Смена нарезки сбрасывает настройки отдельных частей.</p>`;
  $('editorPaneedit').append(splitTools);
  $('proWhole').onclick = handle(()=>{if(!state.sourceId)throw Error('Добавьте видео.');checkpoint();state.splitMode='ranges';state.ranges=[{start:0,end:state.duration}];ensure().clip_overrides={};renderParts();previewPart(0);changed();});
  $('proSplitFinal').onclick=handle(async()=>{const config=baseConfig(), seconds=finite($('proFinalSeconds').value,.2,86400); config.delivery={...config.delivery,max_duration:0,platform:'custom'};const result=await post('/api/pro/parts',{config,seconds,sentence_boundaries:true});checkpoint();state.splitMode='ranges';state.ranges=result.parts;ensure().clip_overrides={};renderParts();previewPart(0);changed();});

  async function loadProfiles() { const result=await api('/api/delivery-profiles');profiles=result.profiles;for(const id of ['proProfile','batchProfile']) { const select=$(id);if(!select)continue;const value=select.value;select.replaceChildren(new Option('Текущие настройки', ''));for(const p of profiles)select.add(new Option(p.name,p.id));select.value=profiles.some(p=>p.id===value)?value:''; } renderVariants(); }
  function applyStyle(config) { checkpoint(); state.width=config.canvas.width;state.height=config.canvas.height;state.geometry.video=structuredClone(config.video);state.background=structuredClone(config.background);state.banners=structuredClone(config.banners);state.advertising={...state.advertising,...config.advertising,chroma:config.advertising.chromakey?.enabled||false};state.advertId=config.advertising.asset_id||null;const cues=state.subtitles.cues;state.subtitles={...config.subtitles,cues};state.pro=Object.fromEntries(Object.keys(defaults()).map(key=>[key,structuredClone(config[key]??defaults()[key])]));state.quality=config.quality;state.encoder=config.encoder;state.fps=config.fps;for(const b of state.banners)editor.loadBannerImage(b).catch(e=>toast(e.message));if(state.advertId)api('/api/assets/'+state.advertId).then(a=>activateAdvert(a,true)).catch(e=>toast(e.message));syncControls();renderSubtitles();renderParts();resizeStage();editor.renderBannerList();loadClip();changed();sync(); }
  $('proApplyProfile').onclick=handle(()=>{const p=profiles.find(p=>p.id===$('proProfile').value);if(!p)throw Error('Выберите профиль.');applyStyle(p.config);});
  $('proSaveProfile').onclick=handle(async()=>{const name=$('proProfileName').value.trim();if(!name)throw Error('Назовите профиль.');await post('/api/delivery-profiles',{name,config:baseConfig(),options:batchOptions()});await loadProfiles();toast('Профиль сохранён на движке.');});
  $('proDeleteProfile').onclick=handle(async()=>{if(!$('proProfile').value)throw Error('Выберите профиль.');await api('/api/delivery-profiles/'+$('proProfile').value,{method:'DELETE'});await loadProfiles();});

  function sync() {
    if(changing)return;changing=true;
    try {
      const p=ensure(), b=editor.getBanner();
      if(b){const s={mode:'source',start:5,duration:10,interval:30,clips:[],...b.schedule};for(const [id,value]of [['proBannerMode',s.mode],['proBannerStart',s.start],['proBannerDuration',s.duration],['proBannerInterval',s.interval],['proBannerClips',s.clips.join(', ')],['proOpacity',b.opacity??1],['proFadeIn',b.fade_in||0],['proFadeOut',b.fade_out||0]])if(document.activeElement!==$(id))$(id).value=value;$('proAspect').checked=b.lock_aspect===true;$('proLocked').checked=b.locked===true;$('proOpacityValue').value=Math.round((b.opacity??1)*100)+'%';}
      for(const [id,value]of [['proDuckTrigger',p.audio.duck_trigger],['proSourceVolume',p.audio.source_volume*100],['proAdvertVolume',p.audio.advert_volume*100],['proDuckVolume',p.audio.duck_volume*100],['proAudioFade',p.audio.fade],['proFramingMode',p.framing.mode],['proFocusX',p.framing.x*100],['proFocusY',p.framing.y*100],['proSubject',p.framing.subject],['proFocusInterpolation',p.framing.interpolation],['proPlatform',p.delivery.platform],['proMaxDuration',p.delivery.max_duration],['proMaxSize',p.delivery.max_size_mb],['proMinW',p.delivery.min_banner_width],['proMinH',p.delivery.min_banner_height],['proMinSeconds',p.delivery.min_banner_seconds],['proSafeTop',p.delivery.safe_top*100],['proSafeBottom',p.delivery.safe_bottom*100],['proSafeRight',p.delivery.safe_right*100]])if(document.activeElement!==$(id))$(id).value=value;
      for(const [id,value]of [['proDucking',p.audio.ducking],['proNormalize',p.audio.normalize],['proLimiter',p.audio.limiter],['proRequireBanner',p.delivery.require_banner],['proRequireSubtitles',p.delivery.require_subtitles],['proCheckSafe',p.delivery.check_safe_zones]])$(id).checked=value;
      $('proDuckTrigger').disabled = $('proDuckVolume').disabled = !p.audio.ducking;
      $('proDuckVolumeValue').textContent = Math.round(p.audio.duck_volume*100)+'%';
      $('proClipInfo').textContent='Отдельные настройки: '+(Object.keys(p.clip_overrides).join(', ')||'нет');
      const signature=JSON.stringify(p.framing.points);if(lastFocus!==signature&&document.activeElement!==$('proFocusPoints')){$('proFocusPoints').value=p.framing.points.map(point=>[point.time,point.x*100,point.y*100].join('; ')).join('\n');lastFocus=signature;}
      if(b){const signature=b.id+JSON.stringify(b.motion||[]);if(lastBanner!==signature&&document.activeElement!==$('proMotion')){$('proMotion').value=(b.motion||[]).map(point=>[point.time,point.x??b.geometry.x,point.y??b.geometry.y,point.scale,point.opacity].join('; ')).join('\n');lastBanner=signature;}}
      const select=$('proTextSelect'),selected=select.value;select.replaceChildren(new Option('Новый текст',''));for(const t of p.text_layers)select.add(new Option(t.text.slice(0,50),t.id));select.value=selected;syncSafe();
    }finally{changing=false;}
  }
  let syncTimer;
  const priorBannerSelect=editor.selectBanner;editor.selectBanner=function(id){priorBannerSelect(id);sync();};
  const priorInspector=updateInspector;
  updateInspector=function(){priorInspector();const locked=state.layer==='editorBanner'&&editor.getBanner()?.locked;for(const id of ['layerX','layerY','layerW','layerH','layerFit','layerScale','fullWidth','centerLayer','bannerRotation'])if($(id))$(id).disabled=!!locked;};
  $('selection').addEventListener('keydown',event=>{if(state.layer==='editorBanner'&&editor.getBanner()?.locked&&event.key.startsWith('Arrow')){event.preventDefault();event.stopImmediatePropagation();}},true);
  const previousSource=activateSource;
  activateSource=async function(asset,keep=false,url=null){if(!keep){ensure().clip_overrides={};ensure().framing.points=[];lastFocus='';}return previousSource(asset,keep,url);};
  window.ClipPro={previewBanners,previewDuckGain,previewAudio,previewContext:owner,outputClock,drawBanner,changed(){clearTimeout(syncTimer);syncTimer=setTimeout(sync,150);},refreshButtons(){if($('proFindFocus'))$('proFindFocus').disabled=!connected||busy()||!state.sourceId;},projectRestored(){ensure();lastBanner='';lastFocus='';loadClip();sync();},connected(){loadProfiles().catch(()=>{});loadBatchLibraries().catch(()=>{});},baseConfig};
  // Batch UI and campaigns are initialized below, using the same style schema.
  initBatch(); ensure(); loadClip(); sync(); if(connected)window.ClipPro.connected();

  let batchFiles=[], uploaded=[], batchId='', batchWorker='', polling=false, uploading=false, uploadController=null, campaigns=[], editingPublication='';
  function initBatch() {
    const mode=node('select','work-mode');mode.id='workMode';mode.setAttribute('aria-label','Режим работы');mode.add(new Option('Ручной монтаж','manual'));mode.add(new Option('Автоматический пакет','automatic'));document.querySelector('.top-actions').prepend(mode);
    const panel=node('section','batch-panel panel');panel.id='batchPanel';panel.hidden=true;
    panel.innerHTML=`<div class="panel-heading"><div><h2>Обработка папки</h2><p class="muted">Серии → общий профиль → баннеры и субтитры → готовая папка. Закрытие браузера не останавливает движок.</p></div><a class="button secondary" href="manual.html" target="_blank">Руководство</a></div><div class="batch-grid"><section class="batch-input"><h3>1. Материалы</h3><label class="upload-zone" id="batchDrop"><input id="batchFolder" type="file" webkitdirectory multiple hidden><span class="upload-symbol">＋</span><strong>Выбрать или перетащить папку</strong><small>Видео и одноимённые SRT / VTT / ASS. Подпапки сохраняются в результатах.</small></label><p id="batchFileSummary" class="muted">Папка не выбрана.</p><details class="settings-details"><summary>Папка уже на компьютере с движком</summary><label class="field">Абсолютный путь<input id="batchInputDirectory" placeholder="D:\\Видео\\Сериал"></label><p class="muted">Ввод пути использует файлы этого ПК. Исходники копируются в материалы.</p></details><label class="field">Название пакета<input id="batchName" value="Сериал"></label><label class="field">Профиль заказчика<select id="batchProfile"><option value="">Текущие настройки редактора</option></select></label><button id="batchRefreshLibraries" class="text-button">Обновить профили и баннеры</button><div id="batchVariants" class="batch-variants"></div><h3>2. Баннер</h3><label class="field">Готовый баннер<select id="batchBanner"><option value="">Баннеры текущего профиля / редактора</option><option value="none">Без баннера</option></select></label><label class="button secondary full">Загрузить новый баннер<input id="batchBannerFile" type="file" accept="image/*,video/*,.gif,.mkv,.webm" hidden></label><label class="field">Показ в каждой готовой части<select id="batchBannerMode"><option value="full">Весь клип</option><option value="each">Заданный интервал</option><option value="start">В начале</option><option value="end">В конце</option><option value="interval">Периодически</option><option value="source">Сохранить расписание профиля</option></select></label><div class="field-pair"><label>Начало / отступ, с<input id="batchBannerStart" type="number" min="0" value="5"></label><label>Длительность, с<input id="batchBannerDuration" type="number" min=".1" value="10"></label></div><label class="field">Повтор через, с<input id="batchBannerInterval" type="number" min=".1" value="30"></label><label class="toggle-row">Убрать подтверждённый однотонный фон<input id="batchRemoveBackground" type="checkbox" checked></label><label class="toggle-row">Подобрать положение с ИИ<input id="batchAdaptBanners" type="checkbox"></label><p class="muted">Размер, звук и требования берутся из профиля. ИИ-размещение требует подключённой модели.</p></section>
      <section class="batch-options"><h3>3. Обработка</h3><label class="field">Нарезка<select id="batchSplit"><option value="whole">Каждая серия целиком</option><option value="duration">По итоговой длительности</option><option value="equal">Заданное число частей</option></select></label><div class="field-pair"><label>Длина готовой части, с<input id="batchSeconds" type="number" min=".2" value="60"></label><label>Частей в серии<input id="batchCount" type="number" min="1" max="1000" value="1"></label></div><label class="toggle-row">Заканчивать на границе реплики<input id="batchSentences" type="checkbox" checked></label><label class="field">Субтитры<select id="batchSubtitles"><option value="none">Не добавлять</option><option value="auto">Добавить, если найдены / распознаны</option><option value="required">Требуются: предупреждение, если не созданы</option></select></label><div class="field-pair"><label>Модель речи<select id="batchSpeechModel"><option value="small">small</option><option value="turbo">turbo</option><option value="large-v3">large-v3</option><option value="base">base</option><option value="tiny">tiny</option><option value="medium">medium</option></select></label><label>Язык<select id="batchLanguage"><option value="auto">Автоматически</option><option value="ru">Русский</option><option value="en">Английский</option><option value="uk">Украинский</option><option value="es">Испанский</option><option value="de">Немецкий</option><option value="fr">Французский</option></select></label></div><label class="toggle-row">Корректура субтитров с ИИ<input id="batchCorrect" type="checkbox"></label><label class="toggle-row">Удалять подтверждённое интро<input id="batchIntro" type="checkbox"></label><label class="toggle-row">Удалять подтверждённое аутро<input id="batchOutro" type="checkbox"></label><h3>4. Проверка и выдача</h3><label class="toggle-row">Сначала подготовить проекты; экспорт после принятия<input id="batchReviewFirst" type="checkbox"></label><label class="toggle-row">Проверять MP4 на декодирование, чёрные кадры и тишину<input id="batchCheck" type="checkbox" checked></label><label class="toggle-row">Создать ZIP всей папки<input id="batchZip" type="checkbox" checked></label><details class="settings-details"><summary>Пакет для публикации</summary><label class="field">Название: {episode}, {part}, {variant}<input id="batchTitle" value="{episode} — часть {part}"></label><label class="field">Описание<textarea id="batchDescription" rows="3"></textarea></label><label class="field">Теги<input id="batchTags"></label><label class="field">Кампания<select id="batchCampaign"><option value="">Без кампании</option></select></label></details><p class="notice">Настройки применяются ко всем файлам. Обязательные ошибки и замечания отмечаются в отчёте; исходники сохраняются.</p><div class="action-pair"><button id="batchStart" class="button primary">Обработать папку</button><button id="batchStop" class="button secondary" disabled>Остановить</button><button id="batchResume" class="button secondary" disabled>Продолжить</button></div><p id="batchStatus" role="status" class="muted">Выберите материалы и настройки.</p><progress id="batchProgress" value="0" max="100"></progress></section></div>
      <section class="batch-results"><div class="panel-heading"><h3>Результаты и принятие</h3><button id="batchRefresh" class="text-button">Обновить</button></div><label class="field">История пакетов<select id="batchHistory"><option value="">Нет пакетов</option></select></label><p id="batchOutputPath" class="muted"></p><div id="batchDownloads" class="download-list"></div><div class="action-pair"><button id="batchSelectAll" class="button secondary">Выбрать готовые</button><button id="batchAccept" class="button primary">Принять / экспортировать выбранные</button><button id="batchReject" class="button secondary">Отклонить выбранные</button><button id="batchRetryItems" class="button secondary">Повторить выбранные ошибки</button></div><div class="batch-table-wrap"><table class="batch-table"><thead><tr><th>Выбор</th><th>Файл</th><th>Статус</th><th>Проверка</th><th>Результат</th></tr></thead><tbody id="batchRows"></tbody></table></div></section>
      <details class="settings-details campaign-tools"><summary>Кампании, публикации и результаты</summary><div class="field-pair"><label>Кампания<input id="campaignName" placeholder="Название"></label><label>Заказчик<input id="campaignAdvertiser"></label></div><button id="saveCampaign" class="button secondary">Создать кампанию</button><h3>Добавить публикацию</h3><label class="field">Название<input id="publicationTitle"></label><label class="field">Ссылка<input id="publicationUrl" type="url"></label><label class="field">Платформа<select id="publicationPlatform"><option value="tiktok">TikTok</option><option value="shorts">Shorts</option><option value="other">Другая</option></select></label><div class="field-pair"><label>Просмотры<input id="publicationViews" type="number" min="0" value="0"></label><label>Переходы<input id="publicationClicks" type="number" min="0" value="0"></label></div><div class="field-pair"><label>Доход<input id="publicationRevenue" type="number" min="0" step=".01" value="0"></label><label>Валюта<input id="publicationCurrency" value="RUB" maxlength="3"></label></div><button id="savePublication" class="button secondary">Сохранить публикацию</button><div id="campaignResults" class="history-list"></div></details>`;
    document.querySelector('.workspace-heading').after(panel);
    mode.onchange=()=>{document.body.dataset.workMode=mode.value;panel.hidden=mode.value!=='automatic';if(mode.value==='automatic')loadBatchLibraries().catch(e=>toast(e.message));};
    $('batchFolder').onchange=()=>selectBatchFiles([...$('batchFolder').files].map(file=>({file,path:file.webkitRelativePath||file.name})));
    $('batchDrop').ondragover=e=>{e.preventDefault();$('batchDrop').classList.add('drag-over');};$('batchDrop').ondragleave=()=> $('batchDrop').classList.remove('drag-over');
    $('batchDrop').ondrop=handle(async e=>{e.preventDefault();$('batchDrop').classList.remove('drag-over');const files=[];async function walk(entry){if(entry.isFile){const file=await new Promise((resolve,reject)=>entry.file(resolve,reject));files.push({file,path:entry.fullPath.replace(/^\//,'')});}else if(entry.isDirectory){const reader=entry.createReader();for(;;){const entries=await new Promise((resolve,reject)=>reader.readEntries(resolve,reject));if(!entries.length)break;for(const child of entries)await walk(child);}}}const entries=[...e.dataTransfer.items].map(item=>item.webkitGetAsEntry?.()).filter(Boolean);if(entries.length){for(const entry of entries)await walk(entry);}else for(const file of e.dataTransfer.files)files.push({file,path:file.name});selectBatchFiles(files);});
    $('batchBannerFile').onchange=handle(async()=>{const file=$('batchBannerFile').files[0];if(!file)return;const form=new FormData();form.append('file',file);const asset=await api('/api/uploads?kind=banner',{method:'POST',body:form});const template={id:'uploaded-'+asset.id,name:file.name,asset_id:asset.id,width:state.width,height:state.height,geometry:{x:0,y:0,w:state.width,h:state.height*.25,fit:'contain'},chromakey:{enabled:false,color:'#00ff00',similarity:.15,blend:.05},duration:10,loop:true,offset:0,volume:0};bannerTemplates.push(template);renderBatchBanners();$('batchBanner').value=template.id;$('batchBannerFile').value='';});
    $('batchStart').onclick=handle(startBatch);
    $('batchStop').onclick=handle(async()=>{if(uploading){uploadController?.abort();return;}if(batchWorker||batchId)await post('/api/jobs/'+(batchWorker||batchId)+'/cancel',{});});
    $('batchResume').onclick=handle(async()=>{if(!batchId)return;await post('/api/jobs/'+batchId+'/retry',{});pollBatch(batchId);});
    $('batchRefresh').onclick=handle(async()=>{await loadBatchHistory();if(batchId)renderBatch(await api('/api/batches/'+batchId));});
    $('batchRefreshLibraries').onclick=handle(async()=>{await loadProfiles();await loadBatchLibraries();});
    $('batchHistory').onchange=handle(async()=>{batchId=$('batchHistory').value;if(batchId){localStorage.setItem('clipcanvas-last-batch',batchId);const job=await api('/api/batches/'+batchId);renderBatch(job);if(['running','queued'].includes(job.status)||job.approval_job||job.archive_job)pollBatch(batchId);}});
    $('batchProfile').onchange=()=>{const p=profiles.find(p=>p.id===$('batchProfile').value);if(p){setBatchOptions(p.options);const s=p.config.banners[0]?.schedule;if(s){$('batchBannerMode').value=s.mode;for(const [id,key]of [['batchBannerStart','start'],['batchBannerDuration','duration'],['batchBannerInterval','interval']])$(id).value=s[key]??$(id).value;}}};
    const selected=()=>[...document.querySelectorAll('#batchRows input:checked')].map(n=>Number(n.value));
    for(const [id,action]of [['batchAccept','accept'],['batchReject','reject'],['batchRetryItems','retry']])$(id).onclick=handle(async()=>{const indices=selected();if(!batchId||!indices.length)throw Error('Выберите элементы таблицы.');if(action==='accept')await saveProject(false);const result=await post('/api/batches/'+batchId+'/decision',{indices,action});if(result.id)await pollBatch(batchId,result.id);else renderBatch(await api('/api/batches/'+batchId));});
    const archiveButton=button('Обновить ZIP после отбора',async()=>{if(!batchId)throw Error('Выберите пакет.');const result=await post('/api/batches/'+batchId+'/archive',{});await pollBatch(batchId,result.id);});archiveButton.id='batchArchive';$('batchDownloads').after(archiveButton);
    const archiveInfo=node('p','muted');archiveInfo.id='batchArchiveInfo';archiveButton.after(archiveInfo);
    $('batchSelectAll').onclick=()=>document.querySelectorAll('#batchRows input').forEach(input=>{input.checked=['ready','review','prepared'].includes(input.dataset.status);});
    $('saveCampaign').onclick=handle(async()=>{await post('/api/campaigns',{name:$('campaignName').value,advertiser:$('campaignAdvertiser').value});await loadCampaigns();});
    $('savePublication').after(button('Новая запись',()=>{editingPublication='';$('savePublication').textContent='Сохранить публикацию';for(const id of ['publicationTitle','publicationUrl'])$(id).value='';for(const id of ['publicationViews','publicationClicks','publicationRevenue'])$(id).value=0;},'text-button'));
    $('savePublication').onclick=handle(async()=>{const campaign_id=$('batchCampaign').value;if(!campaign_id)throw Error('Выберите кампанию в пакете для публикации.');await post('/api/publications',{id:editingPublication||undefined,campaign_id,title:$('publicationTitle').value,url:$('publicationUrl').value,platform:$('publicationPlatform').value,views:Number($('publicationViews').value),clicks:Number($('publicationClicks').value),revenue:Number($('publicationRevenue').value),currency:$('publicationCurrency').value.toUpperCase()});await loadCampaigns();});
  }
  function selectBatchFiles(files){batchFiles=files;uploaded=[];const videos=files.filter(row=>/\.(mp4|mkv|mov|avi|webm|m4v|ts|mts)$/i.test(row.path));$('batchFileSummary').textContent=`Видео: ${videos.length}; субтитров: ${files.filter(row=>/\.(srt|vtt|ass|ssa)$/i.test(row.path)).length}. Объём видео: ${(videos.reduce((sum,row)=>sum+row.file.size,0)/1024**3).toFixed(2)} ГБ.`;$('batchInputDirectory').value='';}
  function batchOptions(){return {split:$('batchSplit').value,seconds:Number($('batchSeconds').value),count:Number($('batchCount').value),sentence_boundaries:$('batchSentences').checked,subtitles:$('batchSubtitles').value,correct_subtitles:$('batchCorrect').checked,language:$('batchLanguage').value,speech_model:$('batchSpeechModel').value,remove_intro:$('batchIntro').checked,remove_outro:$('batchOutro').checked,adapt_banners:$('batchAdaptBanners').checked,remove_background:$('batchRemoveBackground').checked,review_before_export:$('batchReviewFirst').checked,check_outputs:$('batchCheck').checked,zip:$('batchZip').checked,title:$('batchTitle').value,description:$('batchDescription').value,tags:$('batchTags').value,campaign_id:$('batchCampaign').value||null};}
  function setBatchOptions(options){for(const [id,key]of [['batchSplit','split'],['batchSeconds','seconds'],['batchCount','count'],['batchSubtitles','subtitles'],['batchLanguage','language'],['batchSpeechModel','speech_model'],['batchTitle','title'],['batchDescription','description'],['batchTags','tags']])if(options[key]!==undefined)$(id).value=options[key];for(const [id,key]of [['batchSentences','sentence_boundaries'],['batchCorrect','correct_subtitles'],['batchIntro','remove_intro'],['batchOutro','remove_outro'],['batchAdaptBanners','adapt_banners'],['batchRemoveBackground','remove_background'],['batchReviewFirst','review_before_export'],['batchCheck','check_outputs'],['batchZip','zip']])if(options[key]!==undefined)$(id).checked=options[key];}
  function batchConfig(){const p=profiles.find(p=>p.id===$('batchProfile').value),config=structuredClone(p?.config||baseConfig()),template=bannerTemplates.find(t=>t.id===$('batchBanner').value);if($('batchBanner').value==='none'){config.banners=[];config.banner={enabled:false};config.delivery.require_banner=false;}else if(template){const rx=config.canvas.width/template.width,ry=config.canvas.height/template.height;config.banner={enabled:false};config.banners=[{id:ClipBrowser.randomUUID(),name:template.name,enabled:true,asset_id:template.asset_id,timebase:'output',start:0,end:template.duration||10,geometry:{...template.geometry,x:template.geometry.x*rx,y:template.geometry.y*ry,w:template.geometry.w*rx,h:template.geometry.h*ry},chromakey:template.chromakey,loop:template.loop!==false,offset:template.offset||0,volume:template.volume||0,speed:template.speed||1,instructions:template.instructions||'',layout_rules:template.layout_rules||{},opacity:template.opacity??1,fade_in:template.fade_in||0,fade_out:template.fade_out||0,locked:template.locked||false,lock_aspect:template.lock_aspect!==false,motion:(template.motion||[]).map(point=>({...point,x:point.x==null?null:point.x*rx,y:point.y==null?null:point.y*ry}))}];config.delivery.require_banner=true;}const mode=$('batchBannerMode').value;if(mode!=='source')for(const b of [config.banner,...config.banners].filter(b=>b?.enabled))b.schedule={mode,start:Number($('batchBannerStart').value),duration:Number($('batchBannerDuration').value),interval:Number($('batchBannerInterval').value),clips:[]};return config;}
  function renderVariants(){const list=$('batchVariants');if(!list)return;const checked=[...list.querySelectorAll('input:checked')].map(n=>n.value);list.replaceChildren();if(profiles.length){list.append(node('p','muted','Дополнительные варианты: выберите профили (до 10).'));for(const profile of profiles){const label=node('label','toggle-row',profile.name),input=node('input');input.type='checkbox';input.value=profile.id;input.checked=checked.includes(profile.id);label.append(input);list.append(label);}}}
  function renderBatchBanners(){const select=$('batchBanner'),value=select.value;select.replaceChildren(new Option('Баннеры текущего профиля / редактора',''),new Option('Без баннера','none'));for(const t of bannerTemplates)select.add(new Option(t.name,t.id));select.value=[...select.options].some(o=>o.value===value)?value:'';}
  async function loadBatchLibraries(){const result=await api('/api/banner-templates');bannerTemplates=[...result.templates,...bannerTemplates.filter(t=>t.id.startsWith('uploaded-'))];renderBatchBanners();await loadBatchHistory();await loadCampaigns();}
  async function startBatch() {
    if(uploading||polling)throw Error('Текущий пакет ещё обрабатывается.');
    const input_directory=$('batchInputDirectory').value.trim(),options=batchOptions();let config=batchConfig();
    const videos=batchFiles.filter(row=>/\.(mp4|mkv|mov|avi|webm|m4v|ts|mts)$/i.test(row.path));
    if(!input_directory&&!videos.length)throw Error('Выберите папку с видео.');
    if(videos.length>2000)throw Error('До 2000 видео в одном пакете.');
    let variants=[...$('batchVariants').querySelectorAll('input:checked')].map(input=>{const p=profiles.find(p=>p.id===input.value);return {name:p.name,config:p.config};});
    if(variants.length>10)throw Error('До 10 вариантов в одном пакете.');
    const reviewed=await ClipBrief.confirm([{name:'Основной вариант',config},...variants], 'Подтвердите обработку папки', `${input_directory || videos.length+' видео'} · Субтитры: ${{none:'выключены',auto:'готовые или распознавание',required:'обязательны'}[options.subtitles]} · После подтверждения весь пакет обрабатывается по этим настройкам.`);
    if(!reviewed)return;config=reviewed[0].config;variants=reviewed.slice(1);
    uploading=true;uploadController=new AbortController();$('batchStart').disabled=true;$('batchStop').disabled=false;
    try {
      if(!input_directory)for(let i=0;i<videos.length;i++) {
        const row=videos[i];let item=uploaded.find(item=>item.relative_path===row.path);
        if(item?.error || item?.captions_done)continue;
        if(uploadController.signal.aborted)throw Error('Загрузка остановлена.');
        $('batchStatus').textContent=`Загрузка ${i+1}/${videos.length}: ${row.path}`;$('batchProgress').value=100*i/videos.length;
        if(!item) {
          const form=new FormData();form.append('file',row.file);
          try { const asset=await api('/api/uploads?kind=video',{method:'POST',body:form,signal:uploadController.signal});item={source_id:asset.id,relative_path:row.path,cues:[]};uploaded.push(item); }
          catch(error) { if(uploadController.signal.aborted||!error.status||error.status>=500)throw error;uploaded.push({relative_path:row.path,cues:[],error:error.message});continue; }
        }
        const stem=row.path.replace(/\.[^.]+$/,''),sidecar=batchFiles.find(other=>other.path.replace(/\.[^.]+$/,'').toLowerCase()===stem.toLowerCase()&&/\.(srt|vtt|ass|ssa)$/i.test(other.path));
        if(sidecar) {
          const captions=new FormData();captions.append('file',sidecar.file);
          try { item.cues=(await api('/api/subtitles/import?source_id='+item.source_id,{method:'POST',body:captions,signal:uploadController.signal})).cues; }
          catch(error) { if(uploadController.signal.aborted||!error.status||error.status>=500)throw error;item.subtitle_error=error.message; }
        }
        item.captions_done=true;
      }
      const items=uploaded.map(({captions_done,...item})=>item);
      const result=await post('/api/batch',{name:$('batchName').value.trim()||'Пакет',config,options,items:input_directory?[]:items,input_directory:input_directory||null,variants});
      batchId=result.id;localStorage.setItem('clipcanvas-last-batch',batchId);pollBatch(batchId);await loadBatchHistory();
    } catch(error) { if(uploadController.signal.aborted){$('batchStatus').textContent='Загрузка остановлена. «Обработать папку» продолжит с уже загруженных файлов.';return;}throw error; }
    finally { uploading=false;uploadController=null;$('batchStart').disabled=polling;$('batchStop').disabled=!polling; }
  }
  async function pollBatch(id, worker='') {
    if(polling)return;polling=true;batchWorker=worker||id;$('batchStart').disabled=true;
    try { for(;;) {
      if(batchId!==id)break;
      const batch=await api('/api/batches/'+id);
      if(!worker)for(const key of ['approval_job','archive_job'])if(batch[key]){const child=await api('/api/jobs/'+batch[key]);if(['queued','running'].includes(child.status)){worker=child.id;batchWorker=worker;}}
      const job=worker?await api('/api/jobs/'+worker):batch;
      renderBatch(batch);$('batchStop').disabled=!['queued','running'].includes(job.status);
      if(worker)$('batchStatus').textContent=job.message||job.status;
      if(!['queued','running'].includes(job.status)){if(job.status==='error')throw Error(job.error||'Ошибка обработки.');break;}
      await new Promise(resolve=>setTimeout(resolve,1200));
    }} catch(error) { $('batchStatus').textContent=error.message+' Состояние сохранено; нажмите «Обновить».'; }
    finally { polling=false;batchWorker='';$('batchStart').disabled=uploading;$('batchStop').disabled=true;try{if(batchId===id)renderBatch(await api('/api/batches/'+id));}catch{}if(batchId&&batchId!==id)pollBatch(batchId); }
  }
  const statuses={pending:'В очереди',prepared:'Ожидает принятия',ready:'Готово',review:'Нужен просмотр',accepted:'Принято',rejected:'Отклонено',error:'Ошибка'};
  function renderBatch(job) {
    renderBatchRows(job);
    $('batchArchiveInfo').textContent=job.archive_stale?'Отбор изменён. Обновите ZIP: отклонённые ролики будут исключены.':'ZIP содержит результаты на момент последней сборки. Принятые файлы находятся в accepted, отклонённые — в rejected.';
    $('batchArchive').disabled=['queued','running'].includes(job.status)||!!batchWorker;
    for(const id of ['batchAccept','batchReject','batchRetryItems'])$(id).disabled=!!batchWorker;
    if(batchWorker)$('batchResume').disabled=true;
  }
  function renderBatchRows(job){$('batchStatus').textContent=job.message||job.status;$('batchProgress').value=job.progress||0;$('batchStop').disabled=!['running','queued'].includes(job.status);$('batchResume').disabled=!['error','cancelled'].includes(job.status);$('batchOutputPath').textContent=job.output_directory?'Папка на движке: '+job.output_directory:'';const downloads=$('batchDownloads');downloads.replaceChildren();for(const file of job.files||[]){const a=node('a','button secondary',file.name);a.href=engine+file.url;a.download=file.name;downloads.append(a);}const selected=new Set([...document.querySelectorAll('#batchRows input:checked')].map(n=>n.value)),body=$('batchRows');body.replaceChildren();for(const row of job.batch_items||[]){const tr=node('tr'),choose=node('td'),input=node('input');input.type='checkbox';input.value=row.index;input.dataset.status=row.status;input.checked=selected.has(String(row.index));input.setAttribute('aria-label','Выбрать '+row.relative_path);choose.append(input);const name=node('td','',row.relative_path+(row.variant?' · вариант '+row.variant:'')),status=node('td','batch-state '+row.status,statuses[row.status]||row.status),issues=node('td');if(row.error)issues.append(node('p','notice',row.error));if(row.issues?.length){const details=node('details');details.append(node('summary','',row.issues.length+' замечаний'));for(const issue of row.issues)details.append(node('p','muted',issue.message));issues.append(details);}else if(!row.error)issues.textContent='—';const output=node('td');const mp4=(row.artifacts||[]).filter(p=>p.endsWith('.mp4'));for(const path of mp4){const a=node('a','text-button',path.split('/').at(-1));a.href=engine+'/api/batches/'+job.id+'/files/'+path.split('/').map(encodeURIComponent).join('/');a.target='_blank';output.append(a);}if(row.project_id)output.append(button('Открыть проект',async()=>{await applyProject(await api('/api/projects/'+row.project_id));$('workMode').value='manual';$('workMode').onchange();editor.openRoute('design');},'text-button'));tr.append(choose,name,status,issues,output);body.append(tr);}}
  async function loadBatchHistory(){const result=await api('/api/batches'),select=$('batchHistory');select.replaceChildren(new Option('Выберите пакет',''));for(const job of result.batches)select.add(new Option((job.batch_name||job.id.slice(0,8))+' · '+job.status,job.id));if(!batchId)batchId=localStorage.getItem('clipcanvas-last-batch')||'';if(!result.batches.some(job=>job.id===batchId)){batchId='';localStorage.removeItem('clipcanvas-last-batch');}select.value=batchId;if(batchId&&!polling){const job=await api('/api/batches/'+batchId);renderBatch(job);if(['running','queued'].includes(job.status)||job.approval_job||job.archive_job)pollBatch(batchId);}}
  window.ClipPro.historyCleared=async function(){const jobs=await api('/api/batches');if(!jobs.batches.some(job=>job.id===batchId)){batchId='';batchWorker='';for(const id of ['batchRows','batchDownloads'])$(id).replaceChildren();$('batchOutputPath').textContent='';$('batchStatus').textContent='История пакетов очищена.';$('batchResume').disabled=true;}await loadBatchHistory();};
  async function loadCampaigns(){const result=await api('/api/campaigns');campaigns=result.campaigns;const select=$('batchCampaign'),value=select.value;select.replaceChildren(new Option('Без кампании',''));for(const c of campaigns)select.add(new Option(c.name,c.id));select.value=value;const results=$('campaignResults');results.replaceChildren();for(const p of result.publications){const row=node('div','history-item');row.append(node('strong','',p.title),node('span','',`${p.views} просмотров · ${p.clicks} переходов · ${p.revenue} ${p.currency} · CTR ${p.views?(p.clicks/p.views*100).toFixed(2):'0'}%`));if(p.url){const link=node('a','',p.url);link.href=p.url;link.target='_blank';link.rel='noopener noreferrer';row.append(link);}row.append(button('Изменить',()=>{editingPublication=p.id;$('batchCampaign').value=p.campaign_id;for(const [id,key]of [['publicationTitle','title'],['publicationUrl','url'],['publicationPlatform','platform'],['publicationViews','views'],['publicationClicks','clicks'],['publicationRevenue','revenue'],['publicationCurrency','currency']])$(id).value=p[key];$('savePublication').textContent='Обновить публикацию';$('publicationTitle').focus();},'text-button'));results.append(row);}}
})();
