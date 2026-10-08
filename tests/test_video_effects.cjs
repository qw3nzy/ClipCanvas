'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {videoEffectsSettings,videoEffectsFilter}=require('../web/video-effects.js');
test('Legacy video starts neutral; zero, mirror, and reset are preserved',()=>{
  assert.deepEqual(videoEffectsSettings(),{mirror:false,brightness:1,contrast:1,saturation:1});
  assert.deepEqual(videoEffectsSettings({mirror:true,brightness:0,contrast:2,saturation:0}),{mirror:true,brightness:0,contrast:2,saturation:0});
  assert.equal(videoEffectsFilter({mirror:true}),'');
  assert.equal(videoEffectsFilter({brightness:1.2,contrast:.8,saturation:0}),'brightness(1.2) contrast(0.8) saturate(0)');
});
