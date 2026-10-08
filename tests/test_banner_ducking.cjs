'use strict';
const assert = require('node:assert/strict'), test = require('node:test');
const {proAudioSettings,proDuckGain} = require('../web/pro.js');

test('new ducking uses banner appearance, old enabled ducking keeps sounding trigger', () => {
  assert.equal(proAudioSettings().duck_trigger,'visible');
  assert.equal(proAudioSettings({ducking:true}).duck_trigger,'sounding');
  assert.equal(proAudioSettings({ducking:true,duck_trigger:'visible',duck_volume:0}).duck_volume,0);
});
test('silent and overlapping visible banners reduce source once, gaps restore it', () => {
  const audio = {ducking:true,duck_trigger:'visible',duck_volume:.25}, silent = {enabled:true,volume:0};
  assert.equal(proDuckGain(audio,[silent],()=>false),.25);
  assert.equal(proDuckGain(audio,[silent,silent],()=>false),.25);
  assert.equal(proDuckGain(audio,[]),1);
  assert.equal(proDuckGain(audio,[{...silent,enabled:false}]),1);
  assert.equal(proDuckGain({...audio,duck_volume:0},[silent]),0);
  assert.equal(proDuckGain({...audio,ducking:false},[silent]),1);
});
test('sounding trigger needs a real audio track and enabled banner sound', () => {
  const audio = {ducking:true,duck_trigger:'sounding',duck_volume:.2};
  assert.equal(proDuckGain(audio,[{enabled:true,volume:0}]),1);
  assert.equal(proDuckGain(audio,[{enabled:true,volume:1}],()=>false),1);
  assert.equal(proDuckGain(audio,[{enabled:true,volume:1}],()=>true),.2);
});
