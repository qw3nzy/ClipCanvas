'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {soundtrackSettings,soundtrackTiming}=require('../web/soundtrack.js');
const plan=[{kind:'source',start:0,end:2},{kind:'advert',start:2,end:3},{kind:'source',start:3,end:5}];
test('Older projects have no enabled soundtrack',()=>{
  assert.equal(soundtrackSettings().enabled,false);
  assert.equal(soundtrackTiming(soundtrackSettings(),3,plan,1).visible,false);
});
test('Start and file offset are independent; short file ends or loops',()=>{
  const settings=soundtrackSettings({enabled:true,start:.5,offset:1});
  assert.deepEqual(soundtrackTiming(settings,3,plan,.25),{time:1,visible:false});
  assert.deepEqual(soundtrackTiming(settings,3,plan,1),{time:1.5,visible:true});
  assert.equal(soundtrackTiming(settings,3,plan,4.5).visible,false);
  assert.deepEqual(soundtrackTiming({...settings,loop:true},3,plan,4.5),{time:1,visible:true});
});
test('Advertising pauses the song and resumes from the same point',()=>{
  const settings=soundtrackSettings({enabled:true,start:.5});
  assert.deepEqual(soundtrackTiming(settings,10,plan,2.5,true),{time:1.5,visible:false});
  assert.deepEqual(soundtrackTiming(settings,10,plan,3),{time:1.5,visible:true});
  assert.deepEqual(soundtrackTiming({...settings,start:2.5},10,plan,3),{time:0,visible:true});
  assert.deepEqual(soundtrackTiming({...settings,pause_for_ads:false},10,plan,2.5,true),{time:2,visible:true});
});
