'use strict';

// One user action owns both jobs. Results replace captions only at the end, so
// cancellation and source changes cannot leave half-applied AI edits behind.
(() => {
  function sourceFingerprint(value) {
    return JSON.stringify([value.sourceId, value.audioIndex ?? null, value.duration, value.subtitles.cues]);
  }

  function mergeCorrections(cues, changes) {
    const result = structuredClone(cues), seen = new Set();
    const lexical = text => text.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
    for (const change of changes || []) {
      const i = change.index;
      if (!Number.isInteger(i) || i < 0 || i >= result.length || seen.has(i)) continue;
      const cue = result[i], text = typeof change.after === 'string' ? change.after.trim() : '';
      if (change.before !== cue.text || !text || text.length > 2000 || text === cue.text) continue;
      seen.add(i);
      // Only the text is accepted from AI; phrase and word times are ours.
      const tokens = text.match(/\S+/gu) || [], words = cue.words || [];
      if (words.length && words.length === tokens.length && words.every((word, j) => lexical(word.text) === lexical(tokens[j]))) {
        words.forEach((word, j) => { word.text = tokens[j]; });
      } else {
        cue.words = [];
        cue.timing_quality = 'phrase';
      }
      cue.text = text;
      delete cue.render_lines;
    }
    return result;
  }

  if (typeof document === 'undefined') {
    if (typeof module !== 'undefined') module.exports = {sourceFingerprint, mergeCorrections};
    return;
  }

  const panel = document.createElement('section');
  panel.className = 'editor-tool subtitle-workflow';
  panel.innerHTML = `<h3>Субтитры в один шаг</h3>
    <p class="muted">Распознаём речь и исправляем текст с ИИ. Границы реплик сохраняются.</p>
    <label class="field">Режим распознавания<select id="subtitleQuality">
      <option value="large-v3">Качество · Large v3</option>
      <option value="turbo">Баланс · Large v3 Turbo</option>
      <option value="small">Меньше ресурсов · Small</option>
    </select></label>
    <p id="subtitleAudioSummary" class="muted"></p>
    <button id="createAISubtitles" type="button" class="button primary full">Создать субтитры с ИИ</button>
    <button id="cancelAISubtitles" type="button" class="button secondary full" hidden>Остановить создание</button>
    <p id="subtitleWorkflowStatus" class="muted" role="status" aria-live="polite"></p>
    <small class="muted">Первый запуск выбранной модели скачивает её. Large v3 требует больше памяти и времени. ИИ использует модель из «Настроек».</small>`;
  $('subtitleSettings').prepend(panel);
  $('subtitleAudioSummary').before($('speechLanguage').closest('label'));
  const manual = $('transcribeButton').closest('details');
  if (manual) manual.querySelector('summary').textContent = 'Только распознавание · другие модели';
  const smart = document.querySelector('.smart-subtitles');
  if (smart) {
    const details = document.createElement('details'), summary = document.createElement('summary');
    details.className = 'settings-details';
    summary.textContent = 'Исправить готовый текст · словарь имён';
    smart.before(details); details.append(summary, smart);
  }

  let active = null, lastSource = state.sourceId;
  const status = text => { $('subtitleWorkflowStatus').textContent = text; };
  const preferences = () => state.editor?.subtitleWorkflow || {};
  const modelChoice = () => ['large-v3', 'turbo', 'small'].includes(preferences().model) ? preferences().model : 'large-v3';

  function refresh() {
    if (lastSource !== state.sourceId) {
      lastSource = state.sourceId;
      if (!active) status('');
    }
    const blocked = Boolean(active) || busy();
    $('createAISubtitles').disabled = blocked || !connected || !state.sourceId || !sourceAsset?.audio || capabilities.asr_available === false;
    $('createAISubtitles').textContent = state.subtitles.cues.length ? 'Пересоздать субтитры с ИИ' : 'Создать субтитры с ИИ';
    $('createAISubtitles').title = state.subtitles.cues.length ? 'Заменит текущие реплики после завершения. Ctrl+Z вернёт предыдущие.' : '';
    $('cancelAISubtitles').hidden = !active;
    $('cancelAISubtitles').disabled = Boolean(active?.cancelled);
    $('subtitleQuality').disabled = blocked;
    $('speechLanguage').disabled = blocked;
    if (!active) $('subtitleQuality').value = modelChoice();
    const audio = $('audioTrack').selectedOptions?.[0]?.textContent || 'Автоматически';
    $('subtitleAudioSummary').textContent = sourceAsset?.audio ? 'Звуковая дорожка: '+audio : 'Загрузите видео со звуком.';
  }

  async function cancelActiveJob(token) {
    const job = state.activeJob;
    if (!token.cancelled || !job || job.type !== token.phase || job.sourceId !== token.sourceId || token.cancelledJobs.has(job.id)) return;
    token.cancelledJobs.add(job.id);
    try { await post('/api/jobs/'+job.id+'/cancel', {}); }
    catch (error) { status('Остановка не подтверждена: '+error.message+' Результат не будет применён.'); }
  }

  async function cancel() {
    if (!active) return;
    active.cancelled = true;
    status('Останавливаем создание. Текущие субтитры сохраняются.');
    refresh();
    await cancelActiveJob(active);
  }

  async function run(options = {}) {
    if (active || busy() || jobStarting) throw Error('Дождитесь текущей задачи или остановите её.');
    if (!connected || !state.sourceId) throw Error('Подключите движок и загрузите видео.');
    if (!sourceAsset?.audio || capabilities.asr_available === false) throw Error('Для распознавания нужны звуковая дорожка и установленный Whisper.');
    const model = options.model || modelChoice(), language = options.language || state.speechLanguage;
    if (!['tiny', 'base', 'small', 'medium', 'large-v3', 'turbo'].includes(model)) throw Error('Неизвестная модель распознавания.');
    const token = {sourceId: state.sourceId, audioIndex: state.audioIndex, signature: sourceFingerprint(state), cancelled: false, cancelledJobs: new Set(), phase: 'transcribe'};
    const glossary = options.glossary ?? $('captionGlossary').value;
    active = token;
    state.importBusy = true;
    state.speechModel = model;
    $('speechModel').value = model;
    state.editor ||= {};
    state.editor.subtitleWorkflow = {model};
    changed(); refreshButtons(); refresh();
    // Covers a click on Stop while the POST creating a job is still in flight.
    const cancellationWatch = setInterval(() => { if (token.cancelled) cancelActiveJob(token); }, 150);
    const isCurrent = () => sourceFingerprint(state) === token.signature;
    try {
      status('1/2 · Распознаём выбранную звуковую дорожку…');
      const transcript = await startJob('/api/transcribe', {source_id: token.sourceId, audio_stream_index: token.audioIndex, language, model}, 'transcribe');
      if (token.cancelled || transcript.status === 'cancelled') { status('Создание остановлено. Текущие субтитры сохранены.'); return {status: 'cancelled', applied: false}; }
      if (transcript.status !== 'done') throw Error('Распознавание не завершено.');
      if (!isCurrent()) { status('Исходник, звук или текст изменились. Результат сохранён в истории и не применён.'); return {status: 'stale', applied: false}; }
      let cues = structuredClone(transcript.cues || []), correctionError = null, corrections = 0;
      if (!cues.length) { status('Речь не найдена. Текущие субтитры сохранены.'); return {status: 'empty', applied: false}; }
      if (options.correct !== false) {
        token.phase = 'subtitle-review';
        status('2/2 · ИИ проверяет пунктуацию и ошибки распознавания…');
        try {
          const review = await startJob('/api/ai/subtitles', {source_id: token.sourceId, audio_stream_index: token.audioIndex, cues, mode: 'correct', line_length: 32, glossary}, 'subtitle-review');
          if (token.cancelled || review.status === 'cancelled') { status('Создание остановлено. Текущие субтитры сохранены.'); return {status: 'cancelled', applied: false}; }
          if (review.status !== 'done') throw Error('Редактура ИИ не завершена.');
          const corrected = mergeCorrections(cues, review.changes);
          corrections = corrected.filter((cue, i) => cue.text !== cues[i].text).length;
          cues = corrected;
        } catch (error) { correctionError = error.message; }
      }
      if (token.cancelled) { status('Создание остановлено. Текущие субтитры сохранены.'); return {status: 'cancelled', applied: false}; }
      if (!isCurrent()) { status('Исходник, звук или текст изменились. Результат сохранён в истории и не применён.'); return {status: 'stale', applied: false}; }
      cues.forEach(validateCue);
      window.ClipStudio?.checkpoint();
      acceptCues(cues);
      if (correctionError) status('Речь распознана: '+cues.length+' реплик. Правка ИИ не выполнена: '+correctionError+' Используйте «Исправить готовый текст». Ctrl+Z вернёт прежние субтитры.');
      else status('Готово: '+cues.length+' реплик'+(options.correct === false ? '' : ', ИИ исправил '+corrections)+'. Проверьте имена на слух. Субтитры включены в экспорт; Ctrl+Z отменит создание.');
      return {status: correctionError ? 'partial' : 'done', applied: true, cues, corrections, correctionError};
    } catch (error) {
      status(token.cancelled ? 'Создание остановлено. Текущие субтитры сохранены.' : 'Субтитры не изменены. '+error.message);
      if (token.cancelled) return {status: 'cancelled', applied: false};
      throw error;
    } finally {
      clearInterval(cancellationWatch);
      active = null;
      state.importBusy = false;
      refreshButtons(); refresh();
    }
  }

  $('subtitleQuality').onchange = () => {
    state.editor ||= {};
    state.editor.subtitleWorkflow = {model: $('subtitleQuality').value};
    state.speechModel = $('subtitleQuality').value;
    $('speechModel').value = state.speechModel;
    changed();
  };
  $('createAISubtitles').onclick = handle(() => run());
  $('cancelAISubtitles').onclick = handle(cancel);
  $('cancelJob').addEventListener('click', () => { if (active) { active.cancelled = true; refresh(); } }, {capture: true});
  window.ClipSubtitles = {run, cancel, refresh, sourceFingerprint, mergeCorrections, get running() { return Boolean(active); }};
  for (const key of ['refreshButtons', 'changed', 'projectRestored']) {
    const previous = window.ClipEditor[key];
    window.ClipEditor[key] = function (...args) { const result = previous?.apply(this, args); refresh(); return result; };
  }
  refresh();
})();
