'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {activeVideoBlurs,videoBlurSigma}=require('../web/video-blur.js');
test('Source intervals include start, exclude end, and support an open end',()=>{
  const regions=[{id:'a',enabled:true,start:1,end:3},{id:'b',enabled:false,start:0,end:null},{id:'c',start:2,end:null}];
  assert.deepEqual(activeVideoBlurs(regions,.9),[]);
  assert.deepEqual(activeVideoBlurs(regions,1).map(r=>r.id),['a']);
  assert.deepEqual(activeVideoBlurs(regions,2).map(r=>r.id),['a','c']);
  assert.deepEqual(activeVideoBlurs(regions,3).map(r=>r.id),['c']);
  assert.deepEqual(activeVideoBlurs([],1),[]);
});
test('Blur strength scales with decoded source resolution',()=>{
  assert.equal(videoBlurSigma(50,320,180),9);
  assert.equal(videoBlurSigma(50,640,360),18);
  assert.equal(videoBlurSigma(0,320,180),9);
  assert.ok(videoBlurSigma(1,16,16)>=.1);
});
