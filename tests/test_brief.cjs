'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {applyBannerBrief,centered}=require('../web/brief.js');
const {targetTime}=require('../web/video-banners.js');
const banner={id:'banner',geometry:{x:10,y:10,w:160,h:80},instructions:'Баннер по центру.',volume:0,motion:[{time:0,x:10,y:10,scale:2,opacity:.5}]};
const config={canvas:{width:640,height:360},video:{x:80,y:40,w:400,h:160}};
const values={instructions:banner.instructions,position:'auto',reference:'canvas',speed:2,sound:true,volume:.6};
test('confirmation applies exact canvas or video centre, sound and speed without mutating preset',()=>{
  const before=JSON.stringify(banner),result=applyBannerBrief(banner,values,config);
  assert.deepEqual(result.geometry,{x:240,y:140,w:160,h:80});assert.equal(result.volume,.6);assert.equal(result.speed,2);
  assert.deepEqual(result.motion,[{time:0,opacity:.5}]);assert.equal(JSON.stringify(banner),before);
  assert.deepEqual(applyBannerBrief(banner,{...values,reference:'video'},config).geometry,{x:200,y:80,w:160,h:80});
});
test('keep current position and mute override saved preset; contradictory zones and invalid speeds fail',()=>{
  const result=applyBannerBrief(banner,{...values,position:'keep',sound:false},config);
  assert.equal(result.locked,true);assert.deepEqual(result.geometry,banner.geometry);assert.equal(result.volume,0);
  assert.throws(()=>applyBannerBrief(banner,{...values,position:'top'},config),/центр/);
  assert.throws(()=>applyBannerBrief(banner,{...values,speed:0},config),/Скорость/);
  for(const text of ['Не ставить по центру.','Текст по центру баннера.','Логотип в центре изображения.'])assert.equal(centered(text),false);
});
test('preview speed changes source phase and non-loop lifetime',()=>{
  assert.equal(targetTime({start:1,offset:.25,speed:2,loop:true},2,2).time,.25);
  assert.equal(targetTime({start:1,offset:.25,speed:.5,loop:false},2,2).time,.75);
  assert.equal(targetTime({start:1,offset:0,speed:4,loop:false},2,1.6).ended,true);
});
