'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const {sourceFingerprint, mergeCorrections} = require('../web/subtitle-workflow.js');
const script = fs.readFileSync(path.join(__dirname, '../web/subtitle-workflow.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const initial = [{start: 1, end: 2, text: 'Прежний текст', manual: false}];
const transcript = [{start: 2, end: 4, text: 'привет мир', words: [{start: 2, end: 2.5, text: 'привет'}, {start: 3, end: 4, text: 'мир'}]}];
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };

function harness(job) {
  const nodes = new Map();
  class Node {
    constructor() { this.value = ''; this.textContent = ''; this.listeners = {}; }
    set innerHTML(html) { for (const match of html.matchAll(/id="([^"]+)"/g)) nodes.set(match[1], new Node()); }
    prepend() {} append() {} before() {}
    closest() { return new Node(); }
    querySelector() { return new Node(); }
    addEventListener(name, action) { this.listeners[name] = action; }
  }
  for (const id of ['subtitleSettings', 'speechLanguage', 'transcribeButton', 'audioTrack', 'speechModel', 'captionGlossary', 'cancelJob']) nodes.set(id, new Node());
  nodes.get('audioTrack').selectedOptions = [{textContent: 'Русская озвучка'}];
  const state = {sourceId: 'source-a', audioIndex: 2, duration: 10, subtitles: {enabled: false, cues: clone(initial)}, editor: {}, speechModel: 'base', speechLanguage: 'ru', importBusy: false, activeJob: null};
  let checkpoints = 0;
  const calls = [], cancellations = [];
  const context = {
    document: {createElement: () => new Node(), getElementById: id => nodes.get(id), querySelector: () => null},
    $: id => nodes.get(id), window: {ClipEditor: {}, ClipStudio: {checkpoint: () => { checkpoints++; }}},
    state, sourceAsset: {audio: true}, connected: true, capabilities: {}, jobStarting: false,
    structuredClone, setInterval, clearInterval,
    busy: () => state.importBusy || Boolean(state.activeJob),
    changed: () => context.window.ClipEditor.changed?.(),
    refreshButtons: () => context.window.ClipEditor.refreshButtons?.(),
    handle: action => action,
    validateCue: cue => { assert.ok(cue.start < cue.end); },
    acceptCues: cues => { state.subtitles.cues = cues; state.subtitles.enabled = true; },
    post: async endpoint => { cancellations.push(endpoint); return {}; },
    startJob: async (endpoint, payload, type) => {
      assert.equal(state.importBusy, true, 'whole workflow holds the UI lock');
      calls.push({endpoint, payload: clone(payload), type});
      state.activeJob = {id: 'job-'+calls.length, type, sourceId: state.sourceId};
      try { return await job(endpoint, payload, type, context); }
      finally { state.activeJob = null; }
    }
  };
  vm.runInNewContext(script, context);
  return {context, state, nodes, calls, cancellations, api: context.window.ClipSubtitles, checkpoints: () => checkpoints};
}

test('AI text keeps phrase/word times and appearance; lexical replacements remove stale word highlights', () => {
  const cues = [{...clone(transcript[0]), appearance: {color: '#abcdef'}, render_lines: ['old']}];
  const merged = mergeCorrections(cues, [{index: 0, before: cues[0].text, after: 'Привет, мир!', cue: {start: 100, end: 101}}]);
  assert.deepEqual([merged[0].start, merged[0].end], [2, 4]);
  assert.deepEqual(merged[0].words.map(word => [word.start, word.end]), [[2, 2.5], [3, 4]]);
  assert.equal(merged[0].words[0].text, 'Привет,');
  assert.deepEqual(merged[0].appearance, {color: '#abcdef'});
  assert.equal(merged[0].render_lines, undefined);
  assert.equal(cues[0].text, 'привет мир');
  const replaced = mergeCorrections(cues, [{index: 0, before: cues[0].text, after: 'Привет, друг!'}]);
  assert.deepEqual(replaced[0].words, []);
  assert.equal(replaced[0].timing_quality, 'phrase');
  assert.deepEqual(mergeCorrections(cues, [{index: -1, after: 'x'}, {index: 0, before: 'stale', after: 'x'}]), cues);
});

test('fingerprint includes source, audio selection and full caption edits', () => {
  const state = {sourceId: 'a', audioIndex: 0, duration: 10, subtitles: {cues: clone(initial)}};
  const before = sourceFingerprint(state);
  state.audioIndex = 1; assert.notEqual(sourceFingerprint(state), before);
  state.audioIndex = 0; state.subtitles.cues[0].text = 'changed'; assert.notEqual(sourceFingerprint(state), before);
});

test('one click runs large-v3 on selected audio and atomically applies corrected captions with one undo checkpoint', async () => {
  const review = deferred(), reachedReview = deferred();
  const h = harness(async endpoint => {
    if (endpoint === '/api/transcribe') return {status: 'done', cues: clone(transcript)};
    reachedReview.resolve(); return review.promise;
  });
  assert.equal(h.calls.length, 0, 'opening UI never starts model download');
  const pending = h.api.run();
  await reachedReview.promise;
  assert.deepEqual(h.state.subtitles.cues, initial);
  assert.equal(h.checkpoints(), 0);
  assert.equal(h.calls[0].payload.model, 'large-v3');
  assert.equal(h.calls[0].payload.audio_stream_index, 2);
  await assert.rejects(h.api.run(), /Дождитесь/);
  review.resolve({status: 'done', changes: [{index: 0, before: 'привет мир', after: 'Привет, мир!'}]});
  const result = await pending;
  assert.equal(result.status, 'done');
  assert.equal(h.checkpoints(), 1);
  assert.equal(h.state.subtitles.cues[0].text, 'Привет, мир!');
  assert.equal(h.state.subtitles.enabled, true);
  assert.equal(h.state.importBusy, false);
});

test('changed source/audio or captions discard a completed review', async () => {
  for (const mutate of [state => { state.sourceId = 'source-b'; }, state => { state.audioIndex = 3; }, state => { state.subtitles.cues[0].text = 'Правка пользователя'; }]) {
    const review = deferred(), reachedReview = deferred();
    const h = harness(async endpoint => {
      if (endpoint === '/api/transcribe') return {status: 'done', cues: clone(transcript)};
      reachedReview.resolve(); return review.promise;
    });
    const pending = h.api.run(); await reachedReview.promise;
    mutate(h.state); const edited = clone(h.state.subtitles.cues);
    review.resolve({status: 'done', changes: []});
    assert.equal((await pending).status, 'stale');
    assert.deepEqual(h.state.subtitles.cues, edited);
    assert.equal(h.checkpoints(), 0);
  }
});

test('cancel during either job preserves existing subtitles even if server finishes successfully', async () => {
  for (const phase of ['transcribe', 'subtitle-review']) {
    const work = deferred(), reached = deferred();
    const h = harness(async (endpoint, payload, type) => {
      if (type === phase) { reached.resolve(); return work.promise; }
      return {status: 'done', cues: clone(transcript)};
    });
    const pending = h.api.run(); await reached.promise;
    await h.api.cancel(); work.resolve({status: 'done', cues: clone(transcript), changes: []});
    assert.equal((await pending).status, 'cancelled');
    assert.deepEqual(h.state.subtitles.cues, initial);
    assert.equal(h.checkpoints(), 0);
    assert.equal(h.cancellations.length, 1);
    assert.equal(h.state.importBusy, false);
  }
});

test('unavailable AI retains usable transcription and reports partial completion', async () => {
  const h = harness(async endpoint => {
    if (endpoint === '/api/transcribe') return {status: 'done', cues: clone(transcript)};
    throw Error('Модель ИИ недоступна');
  });
  const result = await h.api.run();
  assert.equal(result.status, 'partial');
  assert.equal(h.state.subtitles.cues[0].text, 'привет мир');
  assert.match(h.nodes.get('subtitleWorkflowStatus').textContent, /Правка ИИ не выполнена/);
  assert.equal(h.checkpoints(), 1);
});

test('cancel requested before job creation is acknowledged reaches the newly created job', async () => {
  const creation = deferred(), completion = deferred(), cancelled = deferred();
  const h = harness(async () => { throw Error('unused'); });
  h.context.startJob = async () => {
    await creation.promise;
    h.state.activeJob = {id: 'delayed-job', type: 'transcribe', sourceId: h.state.sourceId};
    try { return await completion.promise; }
    finally { h.state.activeJob = null; }
  };
  h.context.post = async endpoint => { assert.equal(endpoint, '/api/jobs/delayed-job/cancel'); cancelled.resolve(); return {}; };
  const pending = h.api.run();
  assert.equal(h.api.running, true);
  await h.api.cancel();
  creation.resolve();
  await cancelled.promise;
  completion.resolve({status: 'cancelled'});
  assert.equal((await pending).status, 'cancelled');
  assert.deepEqual(h.state.subtitles.cues, initial);
  assert.equal(h.api.running, false);
});

test('empty recognition or failed transcription does not replace current captions', async () => {
  const empty = harness(async () => ({status: 'done', cues: []}));
  assert.equal((await empty.api.run()).status, 'empty');
  assert.deepEqual(empty.state.subtitles.cues, initial);
  const failed = harness(async () => { throw Error('Whisper failed'); });
  await assert.rejects(failed.api.run(), /Whisper failed/);
  assert.deepEqual(failed.state.subtitles.cues, initial);
  assert.equal(failed.state.importBusy, false);
});
