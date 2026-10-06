'use strict';

// Short paths for daily editing, sharing the existing project and undo history.
(() => {
  const editor = window.ClipEditor;
  const el = (tag, cls, text) => { const n = document.createElement(tag); n.className = cls || ''; if (text !== undefined) n.textContent = text; return n; };
  const action = (text, fn, cls = 'button secondary') => { const b = el('button', cls, text); b.type = 'button'; b.onclick = handle(fn); return b; };
  const checkpoint = () => window.ClipStudio?.checkpoint();
  const wrap = (node, title, open = false) => { const d = el('details', 'settings-details'); d.open = open; node.before(d); d.append(el('summary', '', title), node); return d; };
  const normalizeAngle = value => ((Number(value) + 180) % 360 + 360) % 360 - 180;

  // The viewing scope is independent of entering or leaving fullscreen.
  const scopeLabel = el('label', 'preview-scope', 'Смотреть '), scope = el('select');
  scope.id = 'previewScope'; scope.setAttribute('aria-label', 'Что смотреть');
  [['source', 'Весь исходник'], ['all', 'Весь монтаж'], ['clip', 'Выбранный клип']].forEach(([v, t]) => scope.add(new Option(t, v)));
  scopeLabel.append(scope); document.querySelector('.preview-panel .panel-heading').append(scopeLabel);
  const syncScope = () => { scope.value = previewIndex === null ? 'source' : previewIndex === -1 ? 'all' : 'clip'; };
  function previewWhole() {
    if (!state.sourceId) throw Error('Сначала добавьте видео.');
    stopPlayback(); segments = []; let offset = 0;
    for (const part of cuts()) { const schedule = adSchedule(part); for (const segment of schedule) segments.push({...segment, start: segment.start + offset, end: segment.end + offset}); offset += schedule.at(-1)?.end || 0; }
    previewIndex = -1; playSegment = 0;
    const duration = segments.at(-1)?.end || 0;
    $('seek').max = duration; $('duration').textContent = shortTime(duration);
    $('previewMode').textContent = 'Весь монтаж · ' + shortTime(duration);
    if (segments.length) switchSegment(0); else video.currentTime = 0;
    $('playButton').disabled = !segments.length; $('seek').disabled = !segments.length;
    renderPartChips(cuts()); syncScope(); editor.sync(); resizeStage(); requestDraw();
  }
  const priorPart = previewPart;
  previewPart = function(index) { if (index === -1) return previewWhole(); priorPart(index); syncScope(); resizeStage(); };
  const priorSource = sourcePreview;
  sourcePreview = function() { priorSource(); syncScope(); refreshButtons(); resizeStage(); };
  scope.onchange = handle(() => { if (scope.value === 'source') sourcePreview(); else if (scope.value === 'all') previewWhole(); else previewPart(Number($('partSelect').value) || 0); });
  $('sourcePreview').onclick = () => sourcePreview(); $('sourcePreview').textContent = 'Весь исходник';
  // A full-width 16:9 source retains its aspect ratio inside the chosen canvas.
  // Fullscreen fills the viewport; fitting never silently changes the project.
  const oldResize = resizeStage;
  resizeStage = function() {
    if (!document.fullscreenElement) { $('resolutionLabel').textContent = state.width + ' × ' + state.height; return oldResize(); }
    const w = previewIndex === null ? (video.videoWidth || state.width) : state.width, h = previewIndex === null ? (video.videoHeight || state.height) : state.height;
    const area = $('previewArea'), ratio = Math.max(.01, Math.min((area.clientWidth - 8) / w, (area.clientHeight - 8) / h));
    $('stage').style.width = w * ratio + 'px'; $('stage').style.height = h * ratio + 'px';
    const backing = Math.min(3840, Math.max(240, Math.round(w * ratio * Math.min(2, devicePixelRatio || 1))));
    canvas.width = backing; canvas.height = Math.round(backing * h / w); $('resolutionLabel').textContent = (previewIndex === null ? 'Исходник ' : 'Холст ') + w + ' × ' + h; updateSelection(); requestDraw();
  };
  new ResizeObserver(() => resizeStage()).observe($('previewArea'));
  const composedDraw = draw;
  draw = function() {
    if (!document.fullscreenElement || previewIndex !== null) return composedDraw();
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.fillStyle = '#000'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (video.readyState >= 2) ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  };

  // Keep common tools visible and disclose detailed controls when needed.
  document.querySelector('[data-route="projects"]').textContent = '1 · Видео';
  document.querySelector('[data-route="edit"]').textContent = '2 · Монтаж';
  document.querySelector('[data-route="design"]').textContent = '3 · Баннеры и вид';
  document.querySelector('[data-route="subtitles"]').textContent = '4 · Субтитры';
  document.querySelector('[data-route="export"]').textContent = '5 · Экспорт';
  $('projectSummary').textContent = 'Добавьте видео → смонтируйте → оформите → экспортируйте.';
  wrap($('layoutSettings'), 'Размер видео, фон и точные координаты');
  wrap(document.querySelector('.caption-tools'), 'Точное оформление субтитров');
  const bannerAI = $('bannerInstructions').closest('section'); wrap(bannerAI, 'Размещение с ИИ и требования заказчика');
  $('adProfile').closest('details').open = false;
  $('audioTrack').closest('details').open = false;
  const designPane = $('editorPanedesign'), bannerTools = document.querySelector('.banner-tools');
  designPane.prepend(bannerTools);
  $('previewHint').textContent = 'Перетащите баннер на холсте. Тяните углы для размера, круглый маркер — для поворота. Режим просмотра выбирается над видео.';

  // Rotation uses the exact same centered transform as the export compositor.
  const originalFitDraw = fitDraw;
  fitDraw = function(image, geometry) {
    const angle = Number(geometry.rotation) || 0;
    if (!angle) return originalFitDraw(image, geometry);
    ctx.save(); ctx.translate(geometry.x + geometry.w / 2, geometry.y + geometry.h / 2); ctx.rotate(angle * Math.PI / 180);
    originalFitDraw(image, {...geometry, x: -geometry.w / 2, y: -geometry.h / 2}); ctx.restore();
  };
  const rotationControls = el('div', 'banner-rotation');
  rotationControls.innerHTML = '<label class="range-field">Поворот <output id="bannerRotationValue">0°</output><input id="bannerRotation" type="range" min="-180" max="180" step="1" value="0"></label><div class="action-pair"><button id="bannerRotateLeft" class="button secondary">↶ 90°</button><button id="bannerRotateReset" class="text-button">Выровнять</button><button id="bannerRotateRight" class="button secondary">↷ 90°</button></div>';
  $('editBannerPosition').parentElement.after(rotationControls);
  function setRotation(value) { const b = editor.getBanner(); if (!b) return; b.geometry.rotation = normalizeAngle(value); updateInspector(); changed(); refresh(); }
  $('bannerRotation').onpointerdown = checkpoint; $('bannerRotation').onkeydown = checkpoint;
  $('bannerRotation').oninput = () => setRotation($('bannerRotation').value);
  $('bannerRotateLeft').onclick = () => { checkpoint(); setRotation((editor.getBanner()?.geometry.rotation || 0) - 90); };
  $('bannerRotateRight').onclick = () => { checkpoint(); setRotation((editor.getBanner()?.geometry.rotation || 0) + 90); };
  $('bannerRotateReset').onclick = () => { checkpoint(); setRotation(0); };
  const rotationHandle = el('button', 'rotation-handle', '↻'); rotationHandle.type = 'button'; rotationHandle.dataset.handle = 'rotate'; rotationHandle.setAttribute('aria-label', 'Повернуть баннер'); $('selection').append(rotationHandle);
  const originalSelection = updateSelection;
  updateSelection = function() { originalSelection(); const g = state.geometry[state.layer]; $('selection').style.transform = 'rotate(' + (Number(g?.rotation) || 0) + 'deg)'; rotationHandle.hidden = state.layer !== 'editorBanner'; };
  let gesture = null;
  const selection = $('selection');
  selection.addEventListener('pointerdown', e => {
    if (state.layer !== 'editorBanner') return;
    if (editor.getBanner()?.locked) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    e.preventDefault(); e.stopImmediatePropagation(); const g = state.geometry.editorBanner; if (!g) return;
    checkpoint(); const box = $('stage').getBoundingClientRect(), scale = state.width / box.width;
    gesture = {g: {...g}, x: e.clientX, y: e.clientY, scale, handle: e.target.dataset.handle, cx: box.left + (g.x + g.w / 2) / scale, cy: box.top + (g.y + g.h / 2) / scale};
    gesture.angle = Math.atan2(e.clientY - gesture.cy, e.clientX - gesture.cx); selection.setPointerCapture(e.pointerId);
  }, true);
  selection.addEventListener('pointermove', e => {
    if (!gesture) return; e.preventDefault(); e.stopImmediatePropagation();
    const a = gesture.g, g = state.geometry.editorBanner, dx = (e.clientX - gesture.x) * gesture.scale, dy = (e.clientY - gesture.y) * gesture.scale;
    if (gesture.handle === 'rotate') {
      let angle = (a.rotation || 0) + (Math.atan2(e.clientY - gesture.cy, e.clientX - gesture.cx) - gesture.angle) * 180 / Math.PI;
      if (e.shiftKey) angle = Math.round(angle / 15) * 15; g.rotation = normalizeAngle(angle);
    } else if (!gesture.handle) { g.x = a.x + dx; g.y = a.y + dy; }
    else {
      const angle = (a.rotation || 0) * Math.PI / 180, c = Math.cos(angle), s = Math.sin(angle), sx = gesture.handle.includes('w') ? -1 : 1, sy = gesture.handle.includes('n') ? -1 : 1;
      g.w = Math.max(16, Math.min(state.width * 4, a.w + sx * (dx * c + dy * s)));
      g.h = Math.max(16, Math.min(state.height * 4, a.h + sy * (-dx * s + dy * c)));
      if (editor.getBanner()?.lock_aspect || e.shiftKey) { const ratio = a.w / a.h; if (Math.abs(g.w - a.w) / a.w >= Math.abs(g.h - a.h) / a.h) g.h = g.w / ratio; else g.w = g.h * ratio; }
      const localX = sx * (g.w - a.w) / 2, localY = sy * (g.h - a.h) / 2;
      g.x = a.x + a.w / 2 + localX * c - localY * s - g.w / 2; g.y = a.y + a.h / 2 + localX * s + localY * c - g.h / 2;
    }
    updateInspector(); changed(); refresh();
  }, true);
  for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) selection.addEventListener(event, () => { gesture = null; }, true);
  $('editBannerPosition').onclick = () => { editor.selectBanner(editor.getBanner()?.id); const details = $('layoutSettings').parentElement; details.open = true; updateInspector(); };

  // Saved templates keep a real engine asset, not only empty placeholder geometry.
  let templates = [], templatesLoaded = false;
  const library = el('details', 'banner-library');
  library.innerHTML = '<summary>Мои готовые баннеры <span id="bannerTemplateCount"></span></summary><p class="muted">Добавьте видеобаннер или изображение, уберите фон и сохраните. Потом перетаскивайте готовую карточку на видео.</p><div id="bannerTemplateList" class="banner-template-list"></div><p id="bannerTemplateStatus" class="muted" role="status"></p><button id="refreshBannerTemplates" class="text-button">Обновить библиотеку</button>';
  bannerTools.querySelector('h2').after(library);
  const saveTemplate = action('Сохранить как готовый баннер', async () => {
    const b = editor.getBanner(); if (!b) throw Error('Сначала добавьте баннер.');
    await post('/api/banner-templates', {name: (b.name || 'Мой баннер').slice(0, 70), asset_id: b.asset_id, width: state.width, height: state.height, geometry: b.geometry, chromakey: b.chromakey || {}, duration: Math.max(.1, b.end - b.start), loop: b.loop !== false, offset: Number(b.offset) || 0, volume: Number(b.volume) || 0, instructions: b.instructions || '', layout_rules: b.layout_rules || {}, schedule: b.schedule || {}, opacity: b.opacity ?? 1, fade_in: b.fade_in || 0, fade_out: b.fade_out || 0, motion: b.motion || [], locked: b.locked || false, lock_aspect: b.lock_aspect !== false});
    await loadTemplates(); toast('Баннер сохранён в библиотеке движка.');
  }); saveTemplate.id = 'saveBannerTemplate'; rotationControls.after(saveTemplate);
  async function loadTemplates() {
    if (!connected) return;
    const result = await api('/api/banner-templates'); templates = result.templates || []; templatesLoaded = true;
    const list = $('bannerTemplateList'); list.replaceChildren();
    $('bannerTemplateCount').textContent = templates.length ? '· ' + templates.length : '';
    const picker = $('autoBannerTemplate');
    if (picker) { const selected = picker.value; picker.replaceChildren(new Option('Баннеры, уже добавленные в проект', '')); for (const t of templates) picker.add(new Option(t.name, t.id)); picker.value = templates.some(t => t.id === selected) ? selected : ''; }
    $('bannerTemplateStatus').textContent = templates.length ? 'Нажмите «Добавить» или перетащите карточку на холст.' : 'Пока пусто. Загрузите видео или изображение и нажмите «Сохранить как готовый баннер».';
    for (const template of templates) {
      const card = el('article', 'banner-template-card'); card.draggable = true; card.dataset.templateId = template.id;
      const thumb = el('img'); if (template.media_type !== 'video') thumb.src = mediaUrl(template.asset_id); thumb.alt = template.name;
      thumb.draggable = false; card.append(thumb, el('strong', '', template.name));
      if (template.media_type === 'video') card.append(el('small', 'banner-media-badge', '▶ Видео · ' + shortTime(template.media_duration || 0)));
      if (template.media_type === 'video' || template.chromakey?.enabled) post('/api/banners/preview', {asset_id: template.asset_id, chromakey: template.chromakey || {}, time: Number(template.offset) || 0}).then(result => { if (thumb.isConnected) thumb.src = engine + result.url; }).catch(() => {});
      card.addEventListener('dragstart', e => { e.dataTransfer.setData('application/x-clipcanvas-banner', template.id); e.dataTransfer.effectAllowed = 'copy'; });
      const actions = el('div', 'action-pair'); actions.append(action('Добавить', () => insertTemplate(template)), action('Убрать из библиотеки', async () => { await api('/api/banner-templates/' + template.id, {method: 'DELETE'}); await loadTemplates(); }, 'text-button'));
      card.append(actions); list.append(card);
    }
  }
  async function insertTemplate(template, dropPoint) {
    if (!state.sourceId) throw Error('Сначала добавьте видео.'); if (busy()) throw Error('Дождитесь текущей задачи.');
    const part = editor.selectedPart(), rx = state.width / template.width, ry = state.height / template.height;
    const g = {...template.geometry, x: template.geometry.x * rx, y: template.geometry.y * ry, w: template.geometry.w * rx, h: template.geometry.h * ry};
    if (dropPoint) { g.x = dropPoint.x - g.w / 2; g.y = dropPoint.y - g.h / 2; }
    const start = Math.max(part.start, Math.min(video.currentTime || part.start, part.end - .05)), end = Math.min(part.end, start + template.duration);
    if (end <= start) throw Error('Выберите участок с видео.');
    const b = {id: ClipBrowser.randomUUID(), name: template.name, asset_id: template.asset_id, enabled: true, timebase: 'source', start, end, loop: template.loop !== false, offset: Number(template.offset) || 0, volume: Number(template.volume) || 0, geometry: g, chromakey: structuredClone(template.chromakey), instructions: template.instructions || '', layout_rules: structuredClone(template.layout_rules || {}), schedule: structuredClone(template.schedule || {}), opacity: template.opacity ?? 1, fade_in: template.fade_in || 0, fade_out: template.fade_out || 0, motion: (template.motion || []).map(p => ({...p, x: p.x === null ? null : p.x * rx, y: p.y === null ? null : p.y * ry})), locked: template.locked || false, lock_aspect: template.lock_aspect !== false};
    const sourceId = state.sourceId, projectId = state.projectId;
    await editor.loadBannerImage(b);
    if (sourceId !== state.sourceId || projectId !== state.projectId || busy()) throw Error('Проект изменился. Добавьте баннер ещё раз.');
    checkpoint(); state.banners.push(b); editor.selectBanner(b.id); editor.renderBannerList(); changed();
  }
  $('refreshBannerTemplates').onclick = handle(loadTemplates);
  const stage = $('stage');
  stage.addEventListener('dragover', e => { if ([...e.dataTransfer.types].includes('application/x-clipcanvas-banner')) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; stage.classList.add('banner-drop-target'); } });
  stage.addEventListener('dragleave', () => stage.classList.remove('banner-drop-target'));
  stage.addEventListener('drop', handle(async e => {
    stage.classList.remove('banner-drop-target'); const id = e.dataTransfer.getData('application/x-clipcanvas-banner'); if (!id) return; e.preventDefault(); e.stopPropagation();
    const template = templates.find(t => t.id === id); if (!template) throw Error('Обновите библиотеку баннеров.');
    const box = stage.getBoundingClientRect(); await insertTemplate(template, {x: (e.clientX - box.left) / box.width * state.width, y: (e.clientY - box.top) / box.height * state.height});
  }));
  // Clicking a visible banner selects it directly, without hunting for its layer.
  stage.addEventListener('pointerdown', e => {
    if (e.target.closest('.caption-selection') || e.target.dataset.handle || document.fullscreenElement) return;
    const box = canvas.getBoundingClientRect(), x = (e.clientX - box.left) / box.width * state.width, y = (e.clientY - box.top) / box.height * state.height;
    const inAd = previewIndex !== null && segments[playSegment]?.kind === 'advert';
    for (const b of [...state.banners].reverse()) {
      const t = b.timebase === 'output' ? (previewIndex === null ? editor.sourceToOutput(video.currentTime) : timelineOffset()) : video.currentTime;
      if (!b.enabled || t < b.start || t >= b.end || (inAd && b.timebase !== 'output')) continue;
      const g = b.geometry, a = -(g.rotation || 0) * Math.PI / 180, dx = x - g.x - g.w / 2, dy = y - g.y - g.h / 2;
      if (Math.abs(dx * Math.cos(a) - dy * Math.sin(a)) <= g.w / 2 && Math.abs(dx * Math.sin(a) + dy * Math.cos(a)) <= g.h / 2) { editor.openRoute('design'); editor.selectBanner(b.id); break; }
    }
  }, true);
  $('subtitleList').addEventListener('click', e => { if (e.target.closest('button')?.textContent === 'Оформление') document.querySelector('.caption-tools').parentElement.open = true; });

  const automatic = el('section', 'automatic-tool');
  automatic.innerHTML = '<div class="automatic-heading"><span class="automatic-icon">✦</span><div><h2>Автоматический монтаж</h2><p class="muted">Выберите, что поручить ИИ, и запустите один раз.</p></div></div><label class="field">Готовый баннер<select id="autoBannerTemplate"><option value="">Баннеры, уже добавленные в проект</option></select></label><label class="toggle-row"><span>Разместить добавленные баннеры</span><input id="autoBanners" type="checkbox" checked></label><label class="toggle-row"><span>Убрать однотонный фон баннеров</span><input id="autoBackground" type="checkbox" checked></label><label class="toggle-row"><span>Удалить интро</span><input id="autoIntro" type="checkbox" checked></label><label class="toggle-row"><span>Удалить аутро</span><input id="autoOutro" type="checkbox" checked></label><label class="toggle-row"><span>Создать субтитры с ИИ</span><input id="autoSubtitles" type="checkbox"></label><p class="muted">Можно взять готовый баннер из библиотеки или использовать баннеры проекта. Субтитры: Whisper large-v3 + корректура подключённой ИИ-моделью. Результат можно отменить.</p><button id="runAutomatic" class="button primary full">✦ Собрать автоматически</button><button id="stopAutomatic" class="text-button" hidden>Остановить</button><p id="automaticStatus" role="status" class="muted"></p><ul id="automaticReport" class="automatic-report"></ul>';
  $('editorPaneedit').prepend(automatic);
  const openAuto = action('✦ Автомонтаж', () => { editor.openRoute('edit'); automatic.scrollIntoView({block: 'nearest'}); }, 'button primary');
  $('editorPaneprojects').prepend(openAuto);
  function automaticSnapshot() {
    return JSON.stringify({source_id: state.sourceId, width: state.width, height: state.height, audio: state.audioIndex, video: state.geometry.video, advertising: state.advertising, advertId: state.advertId, banners: state.banners, cuts: state.cuts, subtitles: state.subtitles, ranges: state.ranges, splitMode: state.splitMode, partSeconds: state.partSeconds, partCount: state.partCount, mergeTail: state.mergeTail, background: state.background, exportClean: state.exportClean});
  }
  // Store only a digest in a job; exact project data remains the render request.
  async function snapshotDigest() { return ClipBrowser.sha256(automaticSnapshot()); }
  let autoStarting = false;
  $('runAutomatic').onclick = handle(async () => {
    if (busy() || autoStarting || window.ClipSubtitles?.running) throw Error('Дождитесь текущей задачи.');
    autoStarting = true; refresh();
    try {
      const project = renderConfig(false), options = {banners: $('autoBanners').checked, remove_background: $('autoBackground').checked, remove_intro: $('autoIntro').checked, remove_outro: $('autoOutro').checked, subtitles: $('autoSubtitles').checked, correct_subtitles: true, speech_model: 'large-v3', language: state.speechLanguage || 'auto'};
      const templateId = $('autoBannerTemplate').value;
      if (templateId) {
        const t = templates.find(item => item.id === templateId); if (!t) throw Error('Обновите библиотеку баннеров.');
        const part = project.parts[0], g = t.geometry, rx = state.width / t.width, ry = state.height / t.height;
        project.banners.push({id: ClipBrowser.randomUUID(), name: t.name, asset_id: t.asset_id, enabled: true, timebase: 'source', start: part.start, end: Math.min(part.end, part.start + t.duration), loop: t.loop !== false, offset: Number(t.offset) || 0, volume: Number(t.volume) || 0, geometry: {...g, x: g.x * rx, y: g.y * ry, w: g.w * rx, h: g.h * ry}, chromakey: structuredClone(t.chromakey), instructions: t.instructions || '', layout_rules: structuredClone(t.layout_rules || {})});
      }
      if (!Object.entries(options).some(([k, v]) => ['banners', 'remove_background', 'remove_intro', 'remove_outro', 'subtitles'].includes(k) && v)) throw Error('Выберите хотя бы одно действие.');
      const beforeDigest = automaticSnapshot(), snapshot = await snapshotDigest();
      if (beforeDigest !== automaticSnapshot()) throw Error('Проект изменился. Запустите обработку ещё раз.');
      stopPlayback(); $('automaticReport').replaceChildren(); $('automaticStatus').textContent = 'Подготовка. Этапы обработки появятся в панели задачи.';
      const job = await startJob('/api/ai/automatic', {project, options, client_snapshot: snapshot}, 'automatic');
      await applyAutomatic(job);
    } finally { autoStarting = false; refresh(); }
  });
  $('stopAutomatic').onclick = () => $('cancelJob').click();
  async function applyAutomatic(job) {
    if (job.status !== 'done') { $('automaticStatus').textContent = 'Обработка остановлена. Проект сохранён без изменений.'; return; }
    if (state.editor?.automaticJobId === job.id) return;
    const signature = job.client_snapshot || job.request?.client_snapshot;
    const beforeDigest = automaticSnapshot(), currentDigest = await snapshotDigest();
    if (state.editor?.automaticJobId === job.id) return;
    if (beforeDigest !== automaticSnapshot() || job.source_id !== state.sourceId || !signature || signature !== currentDigest) { $('automaticStatus').textContent = 'Проект изменился после запуска. Результат сохранён в истории, текущий монтаж не заменён. Запустите обработку заново.'; toast($('automaticStatus').textContent); return; }
    const result = job.project; if (!result) throw Error('Движок не вернул результат монтажа.');
    checkpoint(); state.cuts = structuredClone(result.cuts || []); state.banners = structuredClone(result.banners || []);
    if (job.automatic_report?.some(item => item.step === 'subtitles' && item.status === 'applied') && result.subtitles?.cues) state.subtitles = {...state.subtitles, ...structuredClone(result.subtitles)};
    state.editor ||= {}; state.editor.automaticJobId = job.id; state.editor.automaticReport = job.automatic_report || [];
    editor.projectRestored(); syncControls(); renderSubtitles(); renderParts(); previewWhole(); changed(); renderAutomaticReport();
    $('automaticStatus').textContent = 'Готово. Включён просмотр всего монтажа. Ctrl+Z отменяет изменения.';
  }
  function renderAutomaticReport() {
    $('automaticReport').replaceChildren();
    for (const item of state.editor?.automaticReport || []) $('automaticReport').append(el('li', item.status === 'applied' ? 'applied' : '', item.reason || item.message || item.step));
  }
  function refresh() {
    syncScope(); const b = editor.getBanner();
    if (b && document.activeElement !== $('bannerRotation')) $('bannerRotation').value = Number(b.geometry.rotation) || 0;
    $('bannerRotationValue').textContent = Math.round(Number(b?.geometry.rotation) || 0) + '°';
    $('runAutomatic').disabled = !connected || !state.sourceId || busy() || autoStarting;
    $('stopAutomatic').hidden = state.activeJob?.type !== 'automatic'; saveTemplate.disabled = !connected || !b || busy();
  }
  const previousChanged = editor.changed; editor.changed = function() { previousChanged(); refresh(); };
  const previousRefresh = editor.refreshButtons; editor.refreshButtons = function() { previousRefresh(); refresh(); };
  const previousRestored = editor.projectRestored; editor.projectRestored = function() { previousRestored(); refresh(); renderAutomaticReport(); };
  const previousOpenRoute = editor.openRoute; editor.openRoute = function(key) { previousOpenRoute(key); if (key === 'edit' && !templatesLoaded) loadTemplates().catch(() => {}); };
  const previousConnected = editor.connected; editor.connected = function() { previousConnected(); loadTemplates().catch(e => { $('bannerTemplateStatus').textContent = e.message; }); };
  document.querySelector('[data-route="design"]').addEventListener('click', () => { if (!templatesLoaded) loadTemplates().catch(e => { $('bannerTemplateStatus').textContent = e.message; }); });
  window.ClipWorkflow = {previewWhole, applyAutomatic, automaticSnapshot, snapshotDigest, loadTemplates, insertTemplate, refresh};
  document.querySelector('[data-route="edit"]').addEventListener('click', () => { if (!templatesLoaded) loadTemplates().catch(() => {}); });
  if (connected) loadTemplates().catch(e => { $('bannerTemplateStatus').textContent = e.message; });
  refresh(); renderAutomaticReport();
})();
