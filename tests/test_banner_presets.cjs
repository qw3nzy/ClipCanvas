'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {proScheduled}=require('../web/pro.js'),{bubavpnGeometry}=require('../web/banner-presets.js');
test('Bubavpn area is at least 25 percent and centered for portrait, square and landscape',()=>{
  for(const [width,height] of [[1080,1920],[1920,1080],[1080,1080],[1080,1350],[240,300]]){
    const geometry=bubavpnGeometry(width,height);
    assert.ok(geometry.w*geometry.h/(width*height)>=.25);
    assert.ok(geometry.x>=0&&geometry.y>=0&&geometry.w<=width&&geometry.h<=height);
    assert.ok(Math.abs(geometry.x+geometry.w/2-width/2)<.00001);
    assert.ok(Math.abs(geometry.y+geometry.h/2-height/2)<.00001);
  }
});
test('Bubavpn main show is centered, extras count complete minutes and remain independently removable',()=>{
  const banner={id:'buba',enabled:true,schedule:{mode:'bubavpn',duration:5}};
  for(const [duration,count] of [[3,1],[60,1],[119.999,1],[120,2],[179.999,2],[180,3],[240,4]]){
    const shows=proScheduled([banner],duration,1);assert.equal(shows.length,count);
    assert.equal((shows[0].start+shows[0].end)/2,duration/2);
    assert.ok(shows.every(show=>show.start>=0&&show.end<=duration));
    const ordered=[...shows].sort((a,b)=>a.start-b.start);
    assert.ok(ordered.every((show,i)=>!i||ordered[i-1].end<=show.start));
  }
  assert.equal(proScheduled([{...banner,schedule:{...banner.schedule,excluded_occurrences:[0]}}],180,1).length,2);
});
