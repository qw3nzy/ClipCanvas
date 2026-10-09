'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {videoCropSettings,videoCropRect,videoCropAspect}=require('../web/video-crop.js');
test('Disabled and legacy crops retain every source pixel',()=>{
  assert.deepEqual(videoCropRect(320,180),{x:0,y:0,w:320,h:180});
  assert.deepEqual(videoCropRect(320,180,{enabled:false,x:.5,w:.5}),{x:0,y:0,w:320,h:180});
});
test('Crop excludes borders with exact bounded pixel coordinates',()=>{
  assert.deepEqual(videoCropRect(320,180,{enabled:true,x:.5,y:.25,w:.5,h:.5}),{x:160,y:45,w:160,h:90});
  const tiny=videoCropRect(101,55,{enabled:true,x:.99,y:.99,w:.01,h:.01});
  assert.equal(tiny.x+tiny.w,101);assert.equal(tiny.y+tiny.h,55);assert.equal(tiny.w,2);
  assert.deepEqual(videoCropSettings({x:NaN,w:Infinity}),{enabled:false,x:0,y:0,w:1,h:1});
});
test('Aspect presets preserve center, stay inside source, and match visible dimensions',()=>{
  for(const aspect of [16/9,9/16,1,4/5]){
    const crop=videoCropAspect({x:.5,y:.25,w:.5,h:.5},aspect,16/9);
    assert.ok(Math.abs(crop.x+crop.w/2-.75)<1e-9);
    assert.ok(Math.abs(crop.y+crop.h/2-.5)<1e-9);
    assert.ok(crop.x>=0&&crop.y>=0&&crop.x+crop.w<=1+1e-9&&crop.y+crop.h<=1+1e-9);
    assert.ok(Math.abs(crop.w/crop.h*(16/9)-aspect)<1e-9);
  }
});
