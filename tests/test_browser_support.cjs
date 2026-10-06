'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createHash, randomBytes, webcrypto} = require('node:crypto');
const {randomUUID, sha256} = require('../web/browser-support.js');

test('HTTP fallback produces version 4 UUIDs from getRandomValues', () => {
  const ids = new Set();
  const httpCrypto = {getRandomValues: array => webcrypto.getRandomValues(array)};
  for (let i = 0; i < 1000; i++) {
    const id = randomUUID(httpCrypto);
    assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    ids.add(id);
  }
  assert.equal(ids.size, 1000);
});

test('UUID fallback sets version and variant bits and delegates native API', () => {
  assert.equal(randomUUID({getRandomValues: array => array.fill(255)}), 'ffffffff-ffff-4fff-bfff-ffffffffffff');
  const native = {randomUUID() { assert.equal(this, native); return 'native-id'; }};
  assert.equal(randomUUID(native), 'native-id');
  assert.throws(() => randomUUID({}), /Обновите браузер/);
});

test('HTTP SHA-256 matches native hashes for Unicode, padding boundaries and large snapshots', async () => {
  const values = ['', 'abc', 'Реклама @bubabot 🎬', ...[55, 56, 63, 64, 65, 127, 128, 129, 4096].map(n => 'a'.repeat(n)), 'x'.repeat(1000000)];
  for (let i = 0; i < 20; i++) values.push(randomBytes(i * 71).toString('hex'));
  for (const text of values) assert.equal(await sha256(text, {}), createHash('sha256').update(text).digest('hex'));
  assert.notEqual(await sha256('{"width":1080}', {}), await sha256('{"width":720}', {}));
});

test('HTTPS native and HTTP fallback fingerprints remain interchangeable', async () => {
  const text = JSON.stringify({source_id:'source', banners:[{name:'Баннер'}], cues:[{text:'Привет 👋'}]});
  assert.equal(await sha256(text, webcrypto), await sha256(text, {}));
});
