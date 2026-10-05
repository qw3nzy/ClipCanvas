'use strict';

// The existing editor owns media, project persistence and rendering. This layer
// adds source-time editing and reviews while keeping those paths shared.
(() => {
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const button = (label, action, className = 'button secondary') => {
    const node = el('button', className, label);
    node.type = 'button';
    node.onclick = handle(action);
    return node;
  };
  const empty = (node, text) => node.replaceChildren(el('p', 'empty-note', text));
  let overview = null, overviewKey = '', selected = -1, pointer = null, drawing = false;
  let lastSource = null, searchResults = [], lastReview = null, images = new Map();
  let undoStack = [], redoStack = [], undoTimer, applyingUndo = false, lastSnapshot = null;
  let cueBoundaries = [], cachedParts = [], zoom = 1, markIn = 0, markOut = 0;
  let aiAvailable = false, searchJob = null, outputFile = null;
  const mediaPanel = document.querySelector('.media-panel');
  const library = el('div', 'studio-library');
  library.id = 'studioMaterials';
  while (mediaPanel.firstChild) library.append(mediaPanel.firstChild);
  const navigation = el('nav', 'studio-tabs');
  navigation.setAttribute('aria-label', 'Инструменты студии');
  const tabs = [['materials', 'Материалы'], ['find', 'Поиск сцен'], ['templates', 'Шаблоны'], ['checks', 'Проверка']];
  const panels = {materials: library};
  for (const [key, label] of tabs) {
    const tab = button(label, () => openTab(key), 'studio-tab');
    tab.id = 'studioTab'+key;
    tab.setAttribute('aria-controls', 'studioPanel'+key);
    navigation.append(tab);
    if (key !== 'materials') panels[key] = el('section', 'studio-tool-panel');
    panels[key].id = 'studioPanel'+key;
  }
  mediaPanel.append(navigation, ...Object.values(panels));
  panels.find.innerHTML = `
    <div class="tool-heading"><span class="eyebrow">ЛОКАЛЬНЫЙ ИИ</span><h2>Найдём сильные сцены</h2></div>
    <p class="muted">Диалоги и кадры помогают выбрать момент. Вы решаете, что попадёт в монтаж.</p>
    <div class="ai-state"><span id="aiStatus" role="status">Проверяем модель…</span><button id="refreshAI" class="text-button">Проверить</button></div>
    <label class="field">Что искать<select id="searchMode"><option value="any">Лучшие моменты</option><option value="funny">Юмор</option><option value="action">Экшен</option><option value="emotion">Эмоции и повороты</option><option value="dialogue">Диалоги и цитаты</option></select></label>
    <details class="search-options"><summary>Диапазон и длина клипов</summary><div class="field-pair"><label>От<input id="searchStart" value="00:00:00" aria-label="Начало поиска"></label><label>До<input id="searchEnd" placeholder="Конец видео" aria-label="Конец поиска"></label></div>
    <button id="searchSelection" class="text-button">Искать в отмеченном диапазоне I–O</button>
    <div class="field-pair"><label>Мин. длина, с<input id="searchMin" type="number" value="20" min="2" max="180"></label><label>Макс. длина, с<input id="searchMax" type="number" value="60" min="3" max="180"></label></div>
    <label class="field">Количество предложений<select id="searchCount"><option value="4">До 4</option><option value="8" selected>До 8</option><option value="15">До 15</option></select></label>
    <label class="toggle-row"><span>Распознать речь, если нет субтитров</span><input type="checkbox" id="searchSpeech" checked></label>
    <label class="field">Язык речи<select id="searchLanguage"><option value="auto">Определить автоматически</option><option value="ru">Русский</option><option value="en">Английский</option><option value="uk">Украинский</option><option value="de">Немецкий</option><option value="fr">Французский</option><option value="es">Испанский</option></select></label>
    <label class="field">Распознавание<select id="searchASRModel"><option value="small">Small · точнее</option><option value="base">Base · быстрее</option></select></label></details>
    <button id="findHighlights" class="button primary full">Найти фрагменты</button>
    <p class="muted">Для первого запуска попробуйте 2–5 минут. Полный фильм займёт больше времени. Модель работает на этом компьютере.</p>
    <div class="results-heading"><h3>Предложения <span id="highlightCount"></span></h3><button id="addHighlights" class="text-button" disabled>Добавить отмеченные</button></div>
    <div id="highlightResults" class="studio-cards" aria-live="polite"></div>`;
  panels.templates.innerHTML = `
    <div class="tool-heading"><span class="eyebrow">ОФОРМЛЕНИЕ</span><h2>Готовый стиль за один клик</h2></div>
    <p class="muted">Шаблон меняет холст и оформление. Ваши клипы, реплики и материалы сохраняются.</p>
    <div id="templateList" class="studio-cards"></div>
    <label class="field">Название своего шаблона<input id="templateName" type="text" maxlength="70" placeholder="Мой стиль для сериала"></label>
    <button id="saveTemplate" class="button secondary full">Сохранить текущий стиль</button>`;
  panels.checks.innerHTML = `
    <div class="tool-heading"><span class="eyebrow">КОНТРОЛЬ РЕЗУЛЬТАТА</span><h2>Перед публикацией</h2></div>
    <p class="muted">Проверьте границы реплик, читаемость и готовый файл.</p>
    <button id="runPreflight" class="button secondary full">Проверить монтаж</button>
    <label class="field">Готовый MP4<select id="checkFile"><option value="">Выберите результат</option></select></label>
    <button id="runOutputCheck" class="button secondary full">Проверить файл</button>
    <div id="checkResults" class="studio-cards" aria-live="polite"></div>
    <p class="muted">Чёрный экран и тишина могут быть частью фильма. Проверка отмечает их для просмотра.</p>`;

  function openTab(key) {
    mediaPanel.scrollTop = 0;
    for (const [name] of tabs) {
      panels[name].hidden = name !== key;
      $('studioTab'+name).classList.toggle('active', name === key);
      $('studioTab'+name).setAttribute('aria-pressed', String(name === key));
    }
    if (key === 'templates') loadTemplates().catch(error => toast(error.message));
    if (key === 'checks') loadOutputs().catch(error => toast(error.message));
  }
  openTab('materials');

  const timeline = el('section', 'timeline-panel panel');
  timeline.id = 'timelinePanel';
  timeline.innerHTML = `
    <div class="timeline-heading"><div><span class="eyebrow">МОНТАЖ</span><h2>Шкала исходника <span id="timelineDuration" class="muted"></span></h2></div>
      <div class="timeline-zoom"><button id="undoEdit" class="icon-button" title="Отменить · Ctrl+Z" aria-label="Отменить">↶</button><button id="redoEdit" class="icon-button" title="Вернуть · Ctrl+Shift+Z" aria-label="Вернуть">↷</button>
      <button id="timelineZoomOut" class="icon-button" aria-label="Уменьшить масштаб шкалы">−</button><span id="timelineZoomValue">1×</span><button id="timelineZoomIn" class="icon-button" aria-label="Увеличить масштаб шкалы">＋</button><button id="timelineFit" class="text-button">Весь фильм</button></div></div>
    <div class="timeline-toolbar"><button id="buildTimeline" class="button secondary">Кадры и звуковая волна</button><button id="markIn" class="button secondary" title="I — начало">I · Начало</button><button id="markOut" class="button secondary" title="O — конец">O · Конец</button><span id="markedRange" class="timecode">00:00 — 00:00</span><button id="addMarkedRange" class="button primary">＋ В монтаж</button><button id="removeClip" class="text-button" disabled>Удалить клип</button></div>
    <div class="timeline-body"><div class="track-labels"><span>Исходник</span><span>Звук</span><span>Клипы</span><span>Текст</span></div>
      <div id="timelineScroll" class="timeline-scroll"><div id="timelineSpace" class="timeline-space"><canvas id="timelineCanvas" tabindex="0" role="application" aria-label="Монтажная шкала: стрелки — перемотка, I и O — границы, Enter — добавить клип"></canvas></div></div></div>
    <div class="timeline-footer"><span id="timelineHint">Загрузите видео, чтобы выбрать фрагмент.</span><span>Пробел — просмотр · I / O — границы · Alt — без привязки</span></div>
    <div class="clip-trim" id="clipTrim" hidden><strong id="clipTrimTitle"></strong><label>Начало<input id="trimStart" aria-label="Начало выбранного клипа"></label><label>Конец<input id="trimEnd" aria-label="Конец выбранного клипа"></label><button id="applyTrim" class="button secondary">Применить</button><button id="previewClip" class="button secondary">Смотреть клип</button></div>`;
  const bottomPanel = document.querySelector('.bottom-panel');
  bottomPanel.before(timeline);
  // Keep manual/equal splitting available without making it the main editor.
  const exportRow = bottomPanel.querySelector('.export-row');
  const splitDetails = el('details', 'split-details');
  splitDetails.append(el('summary', '', 'Автонарезка и список диапазонов'));
  while (bottomPanel.firstChild && bottomPanel.firstChild !== exportRow) splitDetails.append(bottomPanel.firstChild);
  bottomPanel.prepend(splitDetails);
  const historyPanel = document.querySelector('.history-panel');
  const historyDetails = el('details', 'history-details panel');
  historyDetails.append(el('summary', '', 'Проекты и история обработки'));
  historyPanel.before(historyDetails);
  historyDetails.append(historyPanel);
  historyPanel.classList.remove('panel');

  const smart = el('section', 'smart-subtitles');
  smart.innerHTML = `<h3>Умные субтитры</h3><p class="muted">Короткие строки, исправления и словарь имён.</p>
    <label class="field">Имена и термины<input id="captionGlossary" type="text" maxlength="2000" placeholder="Гермиона, Хогвартс…"></label>
    <div class="action-pair"><button id="formatCaptions" class="button secondary">Разбить строки</button><button id="correctCaptions" class="button secondary">Исправить с ИИ</button></div>
    <p id="smartHint" class="muted">Правки появятся для просмотра. Тайм-коды модели не доверяются.</p>`;
  $('subtitleSettings').prepend(smart);
  const searchSubtitles = el('input');
  searchSubtitles.id = 'subtitleSearch';
  searchSubtitles.type = 'search';
  searchSubtitles.placeholder = 'Найти реплику…';
  searchSubtitles.setAttribute('aria-label', 'Найти реплику в субтитрах');
  const foundSubtitles = el('div', 'subtitle-search-results');
  foundSubtitles.id = 'subtitleSearchResults';
  $('subtitleList').before(searchSubtitles, foundSubtitles);
  let subtitleSearchTimer;
  searchSubtitles.oninput = () => {
    clearTimeout(subtitleSearchTimer);
    subtitleSearchTimer = setTimeout(() => {
      foundSubtitles.replaceChildren();
      const query = searchSubtitles.value.trim().toLocaleLowerCase();
      if (!query) return;
      const matches = state.subtitles.cues.map((cue, index) => ({cue, index})).filter(({cue}) => cue.text.toLocaleLowerCase().includes(query));
      foundSubtitles.append(el('small', 'muted', `Найдено ${matches.length}, показано до 30`));
      for (const {cue, index} of matches.slice(0, 30)) foundSubtitles.append(button(shortTime(cue.start)+' · '+cue.text, () => focusCue(index), 'subtitle-match'));
    }, 180);
  };

  const reviewDialog = el('dialog', 'review-dialog');
  reviewDialog.id = 'captionReviewDialog';
  reviewDialog.innerHTML = `<div class="dialog-heading"><div><span class="eyebrow">ПРОВЕРКА ТЕКСТА</span><h2>Предложенные изменения</h2></div><button id="closeCaptionReview" class="icon-button" aria-label="Закрыть">×</button></div><p id="captionReviewSummary" class="muted"></p><div id="captionReviewRows"></div><div class="dialog-actions"><button id="applyCaptionReview" class="button primary">Применить отмеченные</button><button id="cancelCaptionReview" class="button secondary">Оставить как есть</button></div>`;
  document.body.append(reviewDialog);
  $('closeCaptionReview').onclick = $('cancelCaptionReview').onclick = () => reviewDialog.close();
  const outputDialog = el('dialog', 'output-dialog');
  outputDialog.id = 'outputDialog';
  outputDialog.innerHTML = `<div class="dialog-heading"><h2>Проверка результата</h2><button id="closeOutput" class="icon-button" aria-label="Закрыть">×</button></div><video id="outputVideo" controls playsinline></video><p class="muted">Время этого проигрывателя относится к готовому клипу.</p>`;
  document.body.append(outputDialog);
  $('closeOutput').onclick = () => outputDialog.close();
  outputDialog.addEventListener('close', () => $('outputVideo').pause());

  function studioState() {
    if (!state.studio || state.studio.sourceId !== state.sourceId) state.studio = {sourceId: state.sourceId, zoom: 1};
    return state.studio;
  }
  function cueSignature(cues) {
    return JSON.stringify(cues.map(c => [c.start, c.end, c.text, (c.words || []).map(w => [w.start, w.end, w.text])]));
  }
  function snapshot() {
    const keys = ['width', 'height', 'geometry', 'background', 'banner', 'advertising', 'subtitles', 'splitMode', 'partSeconds', 'mergeTail', 'partCount', 'ranges', 'quality', 'encoder', 'fps', 'zip', 'exportClean'];
    return JSON.stringify(Object.fromEntries(keys.map(key => [key, state[key]])));
  }
  function recordUndo() {
    if (restoring || applyingUndo || !state.sourceId) return;
    const next = snapshot();
    if (next !== lastSnapshot) {
      if (lastSnapshot !== null) undoStack.push(lastSnapshot);
      while (undoStack.length > 40 || undoStack.reduce((sum, value) => sum+value.length*2, 0) > 24*1024*1024) undoStack.shift();
      redoStack = [];
      lastSnapshot = next;
    }
    $('undoEdit').disabled = !undoStack.length;
    $('redoEdit').disabled = !redoStack.length;
  }
  function undo(redo = false) {
    clearTimeout(undoTimer);
    recordUndo();
    const from = redo ? redoStack : undoStack, to = redo ? undoStack : redoStack;
    if (!from.length) return;
    to.push(snapshot());
    lastSnapshot = from.pop();
    applyingUndo = true;
    Object.assign(state, JSON.parse(lastSnapshot));
    sourcePreview(); syncControls(); renderParts(); renderSubtitles(); resizeStage(); changed();
    applyingUndo = false;
    $('undoEdit').disabled = !undoStack.length;
    $('redoEdit').disabled = !redoStack.length;
  }
  function checkpoint() { clearTimeout(undoTimer); recordUndo(); }
  function seekSource(t) {
    candidateEnd = null;
    sourcePreview();
    video.currentTime = Math.max(0, Math.min(state.duration, t));
    requestDraw(); drawTimeline();
  }
  function selectClip(index, seek = false) {
    selected = index;
    const part = cuts()[selected];
    $('clipTrim').hidden = !part;
    $('removeClip').disabled = !part;
    if (part) {
      $('clipTrimTitle').textContent = 'Клип '+(index+1);
      $('trimStart').value = time(part.start, true);
      $('trimEnd').value = time(part.end, true);
      if (seek) seekSource(part.start);
    }
    drawTimeline();
  }
  function manualParts() {
    if (state.splitMode !== 'ranges') {
      state.ranges = cuts().map(({start, end}) => ({start, end}));
      state.splitMode = 'ranges';
    }
    return state.ranges;
  }
  function addParts(parts) {
    if (!state.duration) throw Error('Сначала загрузите видео.');
    parts.forEach(validateRange);
    checkpoint();
    // Automatic splitting is only a suggestion until the first manually chosen clip.
    if (state.splitMode !== 'ranges') state.ranges = [];
    state.splitMode = 'ranges';
    let added = 0;
    for (const part of parts) {
      if (state.ranges.length >= 1000) break;
      if (!state.ranges.some(p => Math.abs(p.start-part.start) < .05 && Math.abs(p.end-part.end) < .05)) {
        state.ranges.push({start: part.start, end: part.end}); added++;
      }
    }
    sourcePreview(); syncControls(); renderParts(); changed();
    selectClip(state.ranges.length-1);
    toast(added ? `Добавлено клипов: ${added}. Границы можно перетащить на шкале.` : 'Эти фрагменты уже в монтаже.');
  }
  function mark(kind) {
    if (!state.duration) return;
    const current = video.currentTime || 0;
    if (kind === 'in') markIn = current; else markOut = current;
    updateMarks(); drawTimeline();
  }
  function updateMarks() {
    $('markedRange').textContent = shortTime(markIn)+' — '+shortTime(markOut);
    $('addMarkedRange').disabled = !state.duration || markOut-markIn < .1;
  }

  const scroll = $('timelineScroll'), trackCanvas = $('timelineCanvas'), paint = trackCanvas.getContext('2d');
  const trackHeight = 212;
  function pxPerSecond() { return scroll.clientWidth*zoom/Math.max(.1, state.duration); }
  function secondsAt(event) {
    return Math.max(0, Math.min(state.duration, (event.clientX-trackCanvas.getBoundingClientRect().left+scroll.scrollLeft)/pxPerSecond()));
  }
  function drawTimeline() {
    if (drawing) return;
    drawing = true;
    requestAnimationFrame(() => { drawing = false; paintTimeline(); });
  }
  function paintTimeline() {
    const width = Math.max(1, scroll.clientWidth), ratio = Math.min(2, devicePixelRatio || 1);
    $('timelineSpace').style.width = Math.round(width*zoom)+'px';
    if (trackCanvas.width !== Math.round(width*ratio) || trackCanvas.height !== trackHeight*ratio) {
      trackCanvas.width = Math.round(width*ratio); trackCanvas.height = trackHeight*ratio;
    }
    trackCanvas.style.width = width+'px';
    paint.setTransform(ratio, 0, 0, ratio, 0, 0);
    paint.fillStyle = '#f8fafc'; paint.fillRect(0, 0, width, trackHeight);
    const pps = pxPerSecond(), left = scroll.scrollLeft, x = sec => sec*pps-left;
    const visibleStart = left/pps, visibleEnd = (left+width)/pps;
    const target = 85/pps;
    const step = [1/30, .1, .25, .5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600].find(v => v >= target) || 7200;
    paint.font = '11px Segoe UI'; paint.textBaseline = 'middle';
    for (let t = Math.ceil(visibleStart/step)*step; t <= Math.min(state.duration, visibleEnd); t += step) {
      paint.fillStyle = '#cdd6e4'; paint.fillRect(x(t), 22, 1, trackHeight-22);
      paint.fillStyle = '#617087'; paint.fillText(step < 1 ? t.toFixed(2) : shortTime(t), x(t)+5, 12);
    }
    paint.fillStyle = '#e3eaf3'; paint.fillRect(0, 30, width, 52);
    if (!state.duration) {
      paint.fillStyle = '#7b8ca6'; paint.fillText('Здесь появятся кадры, звук, клипы и субтитры', 18, 57); return;
    }
    for (const thumb of overview?.thumbnails || []) {
      const image = images.get(thumb.url), tx = x(thumb.time);
      if (image?.complete && image.naturalWidth && tx >= -95 && tx <= width+95) paint.drawImage(image, tx-44, 31, 88, 50);
    }
    const waveform = overview?.waveform || [], max = Math.max(.015, ...waveform);
    paint.fillStyle = '#72a8aa';
    const stride = Math.max(1, Math.floor(waveform.length/Math.max(1, width*zoom)));
    for (let i = Math.max(0, Math.floor(visibleStart/state.duration*waveform.length)); i < waveform.length; i += stride) {
      const tx = x(i/waveform.length*state.duration);
      if (tx > width) break;
      const amplitude = Math.max(1, Math.sqrt(waveform[i]/max)*13);
      paint.fillRect(tx, 103-amplitude, Math.max(1, state.duration/waveform.length*pps*stride-1), amplitude*2);
    }
    if (!waveform.length) { paint.fillStyle = '#94a3b8'; paint.fillText('Подготовьте звуковую волну кнопкой выше', 10, 103); }
    for (const scene of overview?.scenes || []) if (scene >= visibleStart && scene <= visibleEnd) {
      paint.fillStyle = '#ffffffb3'; paint.fillRect(x(scene), 32, 1, 48);
    }
    for (let i = 0; i < cachedParts.length; i++) {
      const part = cachedParts[i];
      if (part.end < visibleStart || part.start > visibleEnd) continue;
      const startX = Math.max(-2, x(part.start)), endX = Math.min(width+2, x(part.end)), active = i === selected;
      paint.fillStyle = active ? '#2563eb' : '#dce8fc'; paint.fillRect(startX, 129, Math.max(2, endX-startX-2), 34);
      paint.strokeStyle = active ? '#194ebf' : '#aac5f5'; paint.strokeRect(startX+.5, 129.5, Math.max(1, endX-startX-3), 33);
      paint.fillStyle = active ? '#ffffff' : '#335783';
      if (endX-startX > 28) paint.fillText(''+(i+1), startX+10, 146);
      if (active) { paint.fillRect(x(part.start)+3, 138, 2, 16); paint.fillRect(x(part.end)-7, 138, 2, 16); }
      if (state.advertising.enabled && !state.exportClean && advertAsset) {
        try { for (const segment of adSchedule(part)) if (segment.kind === 'source' && segment.start > 0) {
          const tx = x(segment.sourceStart); paint.fillStyle = '#dc8a24'; paint.fillRect(tx-2, 126, 4, 40);
        }} catch { /* The inspector explains invalid ad settings. */ }
      }
    }
    paint.fillStyle = '#ae98dd';
    // Sorted captions: only draw entries visible in this viewport.
    const cues = state.subtitles.cues;
    let lo = 0, hi = cues.length;
    while (lo < hi) { const mid = (lo+hi)>>1; if (cues[mid].end < visibleStart) lo = mid+1; else hi = mid; }
    for (let i = lo; i < cues.length && cues[i].start <= visibleEnd; i++) {
      const a = Math.max(0, x(cues[i].start)), b = Math.min(width, x(cues[i].end));
      paint.fillStyle = '#e8def9'; paint.fillRect(a, 175, Math.max(2, b-a-1), 26);
      if (b-a > 45) { paint.save(); paint.beginPath(); paint.rect(a+3, 175, b-a-6, 26); paint.clip(); paint.fillStyle = '#6a4894'; paint.fillText(cues[i].text.replaceAll('\n', ' '), a+6, 188); paint.restore(); }
    }
    if (markOut > markIn) { paint.fillStyle = '#2563eb13'; paint.fillRect(x(markIn), 26, (markOut-markIn)*pps, trackHeight-26); }
    for (const [t, label] of [[markIn, 'I'], [markOut, 'O']]) {
      const tx = x(t); if (tx < -20 || tx > width+20) continue;
      paint.fillStyle = '#2563eb'; paint.fillRect(tx, 23, 1, 183); paint.fillRect(tx-6, 21, 13, 15); paint.fillStyle = '#fff'; paint.fillText(label, tx-2, 29);
    }
    const cursor = x(video.currentTime || 0);
    paint.fillStyle = '#ef685e'; paint.fillRect(cursor, 0, 2, trackHeight);
    paint.beginPath(); paint.moveTo(cursor-5, 0); paint.lineTo(cursor+7, 0); paint.lineTo(cursor+1, 9); paint.fill();
  }
  function setZoom(value) {
    const center = (scroll.scrollLeft+scroll.clientWidth/2)/pxPerSecond();
    zoom = Math.max(1, Math.min(256, value));
    studioState().zoom = zoom;
    $('timelineZoomValue').textContent = zoom+'×';
    $('timelineSpace').style.width = Math.round(scroll.clientWidth*zoom)+'px';
    scroll.scrollLeft = center*pxPerSecond()-scroll.clientWidth/2;
    changed(); drawTimeline();
  }
  function snap(value, event) {
    if (event.altKey) return value;
    const tolerance = Math.min(.5, 9/pxPerSecond());
    let best = value, distance = tolerance;
    for (const boundary of cueBoundaries) {
      const delta = Math.abs(boundary-value);
      if (delta < distance) { distance = delta; best = boundary; }
    }
    return best;
  }
  trackCanvas.addEventListener('pointerdown', event => {
    if (!state.duration || event.button !== 0) return;
    event.preventDefault(); trackCanvas.focus();
    const t = secondsAt(event), y = event.clientY-trackCanvas.getBoundingClientRect().top;
    if (y >= 127 && y <= 166) {
      const candidates = cachedParts.map((part, index) => ({part, index}));
      candidates.sort((a, b) => (b.index === selected)-(a.index === selected));
      const found = candidates.find(({part}) => t >= part.start-8/pxPerSecond() && t <= part.end+8/pxPerSecond());
      if (found) {
        checkpoint();
        const {part, index} = found;
        selectClip(index);
        const edge = Math.abs(t-part.start)*pxPerSecond() < 10 ? 'start' : Math.abs(t-part.end)*pxPerSecond() < 10 ? 'end' : 'move';
        pointer = {index, edge, t, part: {...part}, x: event.clientX, started: false};
        trackCanvas.setPointerCapture(event.pointerId);
        return;
      }
    }
    if (y >= 173) {
      const index = state.subtitles.cues.findIndex(c => c.start <= t && c.end >= t);
      if (index >= 0) { focusCue(index); return; }
    }
    seekSource(t);
    pointer = {edge: 'seek'};
    trackCanvas.setPointerCapture(event.pointerId);
  });
  trackCanvas.addEventListener('pointermove', event => {
    if (!pointer) return;
    const t = snap(secondsAt(event), event);
    if (pointer.edge === 'seek') { seekSource(t); return; }
    if (!pointer.started && Math.abs(event.clientX-pointer.x) < 3) return;
    pointer.started = true;
    const parts = manualParts(), original = pointer.part, part = parts[pointer.index];
    if (pointer.edge === 'start') part.start = Math.max(0, Math.min(part.end-.1, t));
    else if (pointer.edge === 'end') part.end = Math.min(state.duration, Math.max(part.start+.1, t));
    else {
      const length = original.end-original.start;
      part.start = Math.max(0, Math.min(state.duration-length, snap(original.start+t-pointer.t, event)));
      part.end = part.start+length;
    }
    cachedParts = cuts(); selectClip(pointer.index);
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) trackCanvas.addEventListener(name, () => {
    if (pointer?.started) { syncControls(); renderParts(); changed(); }
    pointer = null;
  });
  scroll.addEventListener('scroll', drawTimeline, {passive: true});
  new ResizeObserver(drawTimeline).observe(scroll);
  video.addEventListener('timeupdate', drawTimeline);
  video.addEventListener('seeked', drawTimeline);

  function focusCue(index) {
    showTab(true); subtitlePage = Math.floor(index/30); renderSubtitles();
    seekSource(state.subtitles.cues[index].start);
    const row = $('subtitleList').children[index%30];
    row?.scrollIntoView({block: 'nearest'}); row?.querySelector('textarea')?.focus({preventScroll: true});
  }
  $('timelineZoomIn').onclick = () => setZoom(zoom*2);
  $('timelineZoomOut').onclick = () => setZoom(zoom/2);
  $('timelineFit').onclick = () => setZoom(1);
  $('markIn').onclick = () => mark('in'); $('markOut').onclick = () => mark('out');
  $('addMarkedRange').onclick = handle(() => addParts([{start: markIn, end: markOut}]));
  $('removeClip').onclick = handle(() => {
    if (selected < 0) return;
    checkpoint(); manualParts().splice(selected, 1); sourcePreview(); syncControls(); changed(); selectClip(-1);
  });
  $('applyTrim').onclick = handle(() => {
    const part = {start: parseTime($('trimStart').value), end: parseTime($('trimEnd').value)};
    validateRange(part); checkpoint(); manualParts()[selected] = part; sourcePreview(); syncControls(); changed(); selectClip(selected);
  });
  $('previewClip').onclick = handle(() => previewPart(selected));
  $('undoEdit').onclick = () => undo(); $('redoEdit').onclick = () => undo(true);
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.target.closest('input, textarea, select, [contenteditable=true]') || document.querySelector('dialog[open]')) return;
    if ((event.ctrlKey || event.metaKey) && event.code === 'KeyZ') { event.preventDefault(); undo(event.shiftKey); return; }
    if (event.ctrlKey || event.metaKey || event.altKey || !state.duration) return;
    if (event.code === 'Space') { event.preventDefault(); $('playButton').click(); }
    if (event.code === 'KeyI') mark('in');
    if (event.code === 'KeyO') mark('out');
    if (event.target === trackCanvas) {
      if (event.code === 'Enter') { event.preventDefault(); $('addMarkedRange').click(); }
      if (event.code === 'Delete') { event.preventDefault(); $('removeClip').click(); }
      if (['ArrowLeft', 'ArrowRight'].includes(event.code)) { event.preventDefault(); seekSource(video.currentTime+(event.code === 'ArrowLeft' ? -1 : 1)*(event.shiftKey ? 5 : 1/state.fps)); }
    }
  });

  function loadOverview(value) {
    if (!value || value.source_id !== state.sourceId) return;
    const selectedAudio = state.audioIndex ?? sourceAsset?.recommended_audio_stream_index ?? null;
    if (value.audio_stream_index !== selectedAudio) {
      $('timelineHint').textContent = 'Звуковая дорожка изменена. Подготовьте обзор заново.';
      return;
    }
    overview = value; overviewKey = value.source_id+':'+value.audio_stream_index;
    images = new Map();
    for (const thumb of value.thumbnails || []) {
      const image = new Image(); image.onload = drawTimeline; image.src = engine+thumb.url; images.set(thumb.url, image);
    }
    $('timelineHint').textContent = `Смен сцен: ${value.scenes.length} · перетаскивайте края клипов; фиолетовые блоки открывают реплики`;
    refreshTimelineData();
  }
  $('buildTimeline').onclick = handle(async () => {
    const job = await startJob('/api/studio/overview', {source_id: state.sourceId, audio_stream_index: state.audioIndex}, 'overview');
    await applyStudioJob(job, 'overview');
  });
  async function checkAI(start = false) {
    const result = start ? await post('/api/ai/start', {}) : await api('/api/ai/status');
    aiAvailable = result.available;
    $('aiStatus').textContent = result.available ? 'Qwen3.5‑4B · готов' : result.running ? 'Модель не скачана · Setup-AI.cmd' : result.installed ? 'Ollama установлен · нажмите «Запустить»' : 'Установите модель · Setup-AI.cmd';
    $('refreshAI').textContent = result.running ? 'Проверить' : 'Запустить';
    $('refreshAI').dataset.start = String(!result.running);
    $('aiStatus').classList.toggle('ready', result.available);
    refreshStudioButtons();
  }
  $('refreshAI').onclick = handle(() => checkAI($('refreshAI').dataset.start === 'true'));
  $('searchSelection').onclick = () => { $('searchStart').value = time(markIn, true); $('searchEnd').value = time(markOut, true); };
  $('findHighlights').onclick = handle(async () => {
    const payload = {source_id: state.sourceId, audio_stream_index: state.audioIndex,
      start: parseTime($('searchStart').value), end: $('searchEnd').value.trim() ? parseTime($('searchEnd').value) : null,
      min_duration: finite($('searchMin').value, 2, 180), max_duration: finite($('searchMax').value, 3, 180),
      count: Number($('searchCount').value), mode: $('searchMode').value,
      transcribe: $('searchSpeech').checked, language: $('searchLanguage').value, speech_model: $('searchASRModel').value, cues: state.subtitles.cues};
    if (payload.end !== null && payload.end <= payload.start) throw Error('Конец поиска должен быть позже начала.');
    studioState().search = {...payload, cues: undefined}; changed();
    const job = await startJob('/api/ai/highlights', payload, 'highlights');
    await applyStudioJob(job, 'highlights');
  });
  function renderHighlights(job) {
    searchJob = job; searchResults = job.clips || [];
    const target = $('highlightResults'); target.replaceChildren();
    $('highlightCount').textContent = searchResults.length ? String(searchResults.length) : '';
    $('addHighlights').disabled = !searchResults.length;
    if (!searchResults.length) { empty(target, 'Подходящих моментов не найдено. Попробуйте другой диапазон или уменьшите минимальную длину.'); return; }
    if (job.status === 'cancelled') target.append(el('p', 'notice', 'Анализ остановлен. Ниже результаты только обработанных окон.'));
    if (job.evidence === 'sampled_frames') target.append(el('p', 'notice', 'Анализ только по отдельным кадрам: диалоги не распознаны.'));
    for (const [index, clip] of searchResults.entries()) {
      const card = el('article', 'highlight-card'), header = el('label', 'highlight-select');
      const check = el('input'); check.type = 'checkbox'; check.checked = true; check.dataset.clipIndex = index;
      header.append(check, el('strong', '', clip.title));
      const score = el('span', 'score', `${clip.score}/100`); score.title = 'Редакторская оценка модели, не вероятность успеха';
      const meta = el('div', 'card-meta'); meta.append(el('span', 'timecode', shortTime(clip.start)+' — '+shortTime(clip.end)), score);
      const actions = el('div', 'action-pair');
      actions.append(button('Смотреть', () => previewCandidate(clip)), button('＋ В монтаж', () => addParts([clip])));
      card.append(header, meta, el('p', 'muted', clip.reason));
      if (clip.evidence?.length) {
        const evidence = el('details', 'clip-evidence'); evidence.append(el('summary', '', 'Реплики сцены'));
        for (const cue of clip.evidence) evidence.append(el('p', 'muted', shortTime(cue.start)+' · '+cue.text));
        card.append(evidence);
      }
      card.append(actions); target.append(card);
    }
    if (job.cues?.length && !state.subtitles.cues.length) target.append(button('Использовать распознанные субтитры', () => { checkpoint(); acceptCues(job.cues); toast('Субтитры добавлены. Проверьте имена и тайм-коды.'); }, 'button secondary full'));
  }
  let candidateEnd = null;
  function previewCandidate(clip) { seekSource(clip.start); candidateEnd = clip.end; video.play().catch(error => toast(error.message)); }
  video.addEventListener('timeupdate', () => { if (candidateEnd !== null && video.currentTime >= candidateEnd) { stopPlayback(); candidateEnd = null; } });
  video.addEventListener('seeking', () => { if (candidateEnd !== null && video.currentTime > candidateEnd+.1) candidateEnd = null; });
  $('addHighlights').onclick = handle(() => addParts([...$('highlightResults').querySelectorAll('input[data-clip-index]:checked')].map(input => searchResults[Number(input.dataset.clipIndex)])));

  function showReview(job) {
    const originals = job.request?.cues || [];
    lastReview = {job, signature: cueSignature(originals), sourceId: job.request?.source_id};
    const formatting = job.request?.mode === 'format', rows = $('captionReviewRows'); rows.replaceChildren();
    $('applyCaptionReview').textContent = formatting ? 'Применить разбивку' : 'Применить отмеченные';
    const changes = job.changes || [];
    $('captionReviewSummary').textContent = formatting ? `${originals.length} → ${job.reviewed_cues?.length || 0} строк. Фразы без меток слов сохраняют свои границы.` : `${changes.length} предложений. Проверьте имена и смысл; ненужные изменения можно снять.`;
    if (formatting) {
      for (const cue of (job.reviewed_cues || []).slice(0, 40)) {
        const row = el('div', 'review-format'); row.append(el('span', 'timecode', shortTime(cue.start)), el('p', '', cue.text)); rows.append(row);
      }
      if (job.reviewed_cues?.length > 40) rows.append(el('p', 'muted', 'Показаны первые 40 строк. Разбивка применяется ко всему тексту.'));
    } else for (const [i, change] of changes.entries()) {
      const row = el('label', 'review-row'), check = el('input'); check.type = 'checkbox'; check.checked = true; check.dataset.changeIndex = i;
      const text = el('div'); text.append(el('small', 'muted', 'Строка '+(change.index+1)), el('del', '', change.before), el('ins', '', change.after));
      row.append(check, text); rows.append(row);
    }
    $('applyCaptionReview').disabled = !formatting && !changes.length;
    reviewDialog.showModal();
  }
  for (const [id, mode] of [['formatCaptions', 'format'], ['correctCaptions', 'correct']]) $(id).onclick = handle(async () => {
    const job = await startJob('/api/ai/subtitles', {source_id: state.sourceId, audio_stream_index: state.audioIndex,
      cues: state.subtitles.cues, mode, line_length: 32, glossary: $('captionGlossary').value}, 'subtitle-review');
    await applyStudioJob(job, 'subtitle-review');
  });
  $('applyCaptionReview').onclick = handle(() => {
    if (!lastReview || lastReview.sourceId !== state.sourceId || lastReview.signature !== cueSignature(state.subtitles.cues)) throw Error('Субтитры изменились после запуска проверки. Повторите проверку для текущей версии.');
    checkpoint();
    if (lastReview.job.request.mode === 'format') acceptCues(lastReview.job.reviewed_cues);
    else {
      const cues = structuredClone(state.subtitles.cues);
      for (const input of $('captionReviewRows').querySelectorAll('input:checked')) {
        const change = lastReview.job.changes[Number(input.dataset.changeIndex)]; cues[change.index] = change.cue;
      }
      acceptCues(cues);
    }
    reviewDialog.close(); toast('Правки применены. Ctrl+Z отменяет изменение.');
  });

  async function loadTemplates() {
    if (!connected) return;
    const result = await api('/api/templates'), target = $('templateList'); target.replaceChildren();
    for (const template of result.templates) {
      const card = el('article', 'template-card');
      card.append(el('strong', '', template.name), el('p', 'muted', template.description || 'Ваше сохранённое оформление'));
      const actions = el('div', 'action-pair');
      actions.append(button('Применить', () => {
        checkpoint(); const settings = template.settings;
        if (settings.width && settings.height) setCanvas(settings.width, settings.height);
        for (const key of ['background', 'geometry']) if (settings[key]) Object.assign(state[key], structuredClone(settings[key]));
        if (!settings.geometry) autoVideo();
        if (settings.subtitles) Object.assign(state.subtitles, structuredClone(settings.subtitles));
        for (const key of ['quality', 'encoder', 'fps']) if (settings[key] !== undefined) state[key] = settings[key];
        syncControls(); resizeStage(); changed(); toast('Применён шаблон «'+template.name+'».');
      }));
      if (!template.builtin) actions.append(button('Удалить', async () => { await api('/api/templates/'+template.id, {method: 'DELETE'}); await loadTemplates(); }, 'text-button'));
      card.append(actions); target.append(card);
    }
  }
  $('saveTemplate').onclick = handle(async () => {
    const name = $('templateName').value.trim(); if (!name) throw Error('Введите название шаблона.');
    const settings = {width: state.width, height: state.height, geometry: state.geometry, background: state.background,
      subtitles: Object.fromEntries(Object.entries(state.subtitles).filter(([key]) => !['cues', 'enabled'].includes(key))),
      quality: state.quality, encoder: state.encoder, fps: state.fps};
    await post('/api/templates', {name, settings}); $('templateName').value = ''; await loadTemplates(); toast('Шаблон сохранён.');
  });

  function renderChecks(issues, file = null) {
    const target = $('checkResults'); target.replaceChildren(); outputFile = file;
    if (!issues?.length) { empty(target, 'Автоматическая проверка не нашла замечаний. Просмотрите клип перед публикацией.'); return; }
    for (const issue of issues) {
      const row = el('article', 'check-card '+issue.severity);
      row.append(el('span', 'check-label', issue.severity === 'error' ? 'Исправить' : 'Проверить'), el('p', '', issue.message));
      if (Number.isFinite(issue.time)) row.append(button('▶ '+shortTime(issue.time), async () => {
        if (file) {
          const media = $('outputVideo'); outputDialog.showModal();
          if (media.dataset.url !== file.url) { await waitMetadata(media, engine+file.url); media.dataset.url = file.url; }
          media.currentTime = issue.time; await media.play();
        } else seekSource(issue.time);
      }, 'text-button'));
      target.append(row);
    }
  }
  $('runPreflight').onclick = handle(async () => { const result = await post('/api/studio/preflight', renderConfig()); renderChecks(result.issues); });
  let outputChoices = [];
  async function loadOutputs() {
    if (!connected) return;
    const data = await api('/api/jobs?summary=true'); outputChoices = [];
    for (const job of data.jobs) if (['render', 'sample'].includes(job.type)) for (const file of job.files || []) if (file.name.endsWith('.mp4')) outputChoices.push({job_id: job.id, file});
    const old = $('checkFile').value; $('checkFile').replaceChildren(new Option('Выберите результат', ''));
    outputChoices.slice(0, 100).forEach((item, i) => $('checkFile').add(new Option(item.job_id.slice(0, 6)+' · '+item.file.name, i)));
    $('checkFile').value = old;
  }
  $('runOutputCheck').onclick = handle(async () => {
    if ($('checkFile').value === '') throw Error('Выберите готовый MP4.');
    const selected = outputChoices[Number($('checkFile').value)];
    const job = await startJob('/api/studio/check-output', {job_id: selected.job_id, filename: selected.file.name}, 'output-check');
    await applyStudioJob(job, 'output-check');
  });
  async function applyStudioJob(job, type) {
    if (!['done', 'cancelled'].includes(job.status)) return;
    if (type !== 'output-check' && job.request?.source_id !== state.sourceId) { toast('Результат относится к другому исходнику. Откройте нужный проект.'); return; }
    if (type === 'overview' && job.overview) { studioState().overviewJobId = job.id; loadOverview(job.overview); changed(); }
    if (type === 'highlights') {
      studioState().highlightsJobId = job.id; renderHighlights(job); openTab('find'); changed();
      mediaPanel.scrollTop += Math.max(0, $('highlightResults').getBoundingClientRect().top-mediaPanel.getBoundingClientRect().top-navigation.offsetHeight-44);
    }
    if (type === 'subtitle-review' && job.status === 'done') showReview(job);
    if (type === 'output-check' && job.status === 'done') { openTab('checks'); renderChecks(job.issues, job.checked_file); }
  }
  function refreshStudioButtons() {
    const blocked = !connected || busy() || !state.sourceId;
    $('jobPanel').classList.toggle('is-active', Boolean(state.activeJob));
    for (const id of ['buildTimeline', 'findHighlights', 'formatCaptions', 'correctCaptions', 'runPreflight', 'runOutputCheck']) {
      $(id).disabled = blocked || (['formatCaptions', 'correctCaptions'].includes(id) && !state.subtitles.cues.length);
    }
    $('findHighlights').title = aiAvailable ? '' : 'Локальный движок попытается запустить установленную модель';
  }
  function refreshTimelineData() {
    try { cachedParts = cuts(); } catch { cachedParts = []; }
    cueBoundaries = [0, state.duration, ...(overview?.scenes || []), ...state.subtitles.cues.flatMap(c => [c.start, c.end])].sort((a, b) => a-b);
    $('timelineDuration').textContent = state.duration ? '· '+time(state.duration) : '';
    drawTimeline(); refreshStudioButtons();
  }
  let restoringStudio = false, restoreKey = '';
  async function restoreStudio() {
    if (restoringStudio || !state.sourceId || !connected) return;
    const saved = studioState(), key = [state.sourceId, saved.overviewJobId, saved.highlightsJobId].join(':');
    if (key === restoreKey) return;
    restoreKey = key; restoringStudio = true;
    try {
      zoom = Math.max(1, Math.min(256, saved.zoom || 1)); $('timelineZoomValue').textContent = zoom+'×';
      const sourceId = state.sourceId;
      if (saved.search) {
        const search = saved.search;
        $('searchStart').value = time(search.start || 0, true); $('searchEnd').value = search.end ? time(search.end, true) : '';
        for (const [id, key] of [['searchMin','min_duration'],['searchMax','max_duration'],['searchCount','count'],['searchMode','mode'],['searchLanguage','language'],['searchASRModel','speech_model']]) if (search[key] !== undefined) $(id).value = search[key];
        $('searchSpeech').checked = search.transcribe !== false;
      }
      if (saved.overviewJobId) {
        const job = await api('/api/jobs/'+saved.overviewJobId);
        if (sourceId === state.sourceId) loadOverview(job.overview);
      }
      if (saved.highlightsJobId) {
        const job = await api('/api/jobs/'+saved.highlightsJobId);
        if (sourceId === state.sourceId) renderHighlights(job);
      }
    } catch { /* A deleted history item simply needs another analysis. */ }
    finally { restoringStudio = false; drawTimeline(); }
  }
  function onChange() {
    if (restoring) return;
    if (state.sourceId !== lastSource) {
      lastSource = state.sourceId; overview = null; images = new Map(); searchResults = []; restoreKey = '';
      undoStack = []; redoStack = []; lastSnapshot = snapshot(); selected = -1;
      markIn = 0; markOut = Math.min(state.duration, 30); updateMarks();
      $('searchEnd').value = ''; $('searchStart').value = '00:00:00';
      empty($('highlightResults'), 'Запустите поиск — найденные сцены появятся здесь.');
      $('timelineHint').textContent = 'Нажмите на шкалу для перемотки. Отметьте начало I и конец O.';
    }
    if (overview && overviewKey !== state.sourceId+':'+(state.audioIndex ?? sourceAsset?.recommended_audio_stream_index ?? null)) {
      overview = null; $('timelineHint').textContent = 'Звуковая дорожка изменена. Подготовьте обзор заново.';
    }
    refreshTimelineData();
    clearTimeout(undoTimer); if (!applyingUndo) undoTimer = setTimeout(recordUndo, 350);
    restoreStudio();
  }
  window.ClipStudio = {changed: onChange, refreshButtons: refreshStudioButtons, applyJobResult: applyStudioJob,
    connected() { checkAI().catch(error => { $('aiStatus').textContent = error.message; }); restoreStudio(); },
    openTab, addParts, selectClip, refreshTimelineData, projectRestored() {
      clearTimeout(undoTimer); undoStack = []; redoStack = []; lastSnapshot = snapshot(); restoreKey = '';
      overview = null; images = new Map(); selected = -1; selectClip(-1); onChange();
    }};
  // Deferred scripts run before the async project restore finishes.
  onChange();
  setTimeout(() => { checkAI().catch(() => { $('aiStatus').textContent = 'Подключите движок'; }); refreshStudioButtons(); }, 800);
})();
