'use strict';

(() => {
  const centered = text => (text || '').split(/[.!?;\n]/).some(clause => /(?:по\s+центру|в\s+центре|центрировать|\bcent(?:er|re)(?:ed)?\b)/i.test(clause) && !/(?:не|нельзя|not|never)\s+(?:(?:размещать|ставить|показывать|place|put)\s+)?(?:по\s+центру|в\s+центре|center|centre)/i.test(clause) && !/(?:логотип|надпись|текст).*?(?:баннера|изображения)/i.test(clause));
  function applyBannerBrief(banner, values, config) {
    const next = structuredClone(banner), position = values.position;
    next.instructions = values.instructions.trim();
    next.layout_rules = {movable:true,resizable:false,min_width:0,min_height:0,zone:'any',...next.layout_rules};
    if (centered(next.instructions) && ['top','bottom','left','right'].includes(position)) throw Error('В инструкции указан центр, а в настройке выбрана другая зона. Согласуйте положение.');
    const place = position === 'auto' && centered(next.instructions) ? 'center' : position;
    next.locked = place === 'keep'; next.layout_rules.movable = place !== 'keep';
    next.layout_rules.zone = ['center','top','bottom','left','right'].includes(place) ? place : 'any';
    next.layout_rules.center_reference = values.reference;
    if (place === 'center') {
      const reference = values.reference === 'video' ? config.video : {x:0,y:0,w:config.canvas.width,h:config.canvas.height};
      next.geometry.x = reference.x + (reference.w - next.geometry.w) / 2;
      next.geometry.y = reference.y + (reference.h - next.geometry.h) / 2;
      next.motion = (next.motion || []).map(point => ({time:point.time,opacity:point.opacity??1}));
    }
    if (!(values.speed >= .25 && values.speed <= 4 && Number.isFinite(values.speed))) throw Error('Скорость баннера: от 0,25× до 4×.');
    if (!(values.volume >= 0 && values.volume <= 1 && Number.isFinite(values.volume))) throw Error('Громкость баннера: от 0 до 100%.');
    next.speed = values.speed; next.volume = values.sound ? values.volume : 0;
    return next;
  }
  if (typeof document === 'undefined') { module.exports = {applyBannerBrief,centered}; return; }

  let active = false;
  const dialog = document.createElement('dialog'); dialog.id = 'briefDialog'; dialog.className = 'brief-dialog';
  dialog.innerHTML = '<form id="briefForm"><h2 id="briefTitle">Подтвердите задание</h2><p id="briefSummary" class="muted"></p><p>Проверьте и при необходимости измените инструкции. Даже сохранённое ТЗ применяется после вашего подтверждения.</p><div id="briefBanners"></div><p id="briefError" class="notice" role="alert" hidden></p><div class="brief-actions"><button id="briefCancel" type="button" class="button secondary">Отмена</button><button id="briefConfirm" type="submit" class="button primary">Подтвердить и запустить</button></div></form>';
  document.body.append(dialog);
  const make = (tag, text) => { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; return node; };
  const label = (text,input) => { const node=make('label',text);node.className='field';node.append(input);return node; };
  const select = (options,id,value) => { const node=make('select');node.id=id;for(const [key,text]of options)node.add(new Option(text,key));node.value=value;return node; };
  async function confirm(groups, title, summary) {
    if (active) throw Error('Сначала подтвердите или отмените открытое задание.');
    active = true;
    const pending = structuredClone(groups), cards = [], players = [];
    try {
      $('briefTitle').textContent = title; $('briefSummary').textContent = summary; $('briefBanners').replaceChildren(); $('briefError').hidden=true;
      for (const [groupIndex,group] of pending.entries()) {
        const list = [group.config.banner,...(group.config.banners||[])].filter(b=>b?.enabled);
        for(const b of list) {
          const number=cards.length, asset=await api('/api/assets/'+b.asset_id), card=make('section');card.className='brief-banner';
          card.append(make('h3',`${group.name || 'Проект'} · ${b.name || 'Баннер'}`));
          const instructions=make('textarea');instructions.id='briefInstructions-'+number;instructions.rows=3;instructions.maxLength=4000;instructions.value=b.instructions||'';
          card.append(label('Инструкции для этого баннера',instructions));
          const position=select([['auto','Подобрать по ТЗ'],['keep','Сохранить текущее положение'],['center','Точно по центру'],['top','Верхняя половина'],['bottom','Нижняя половина'],['left','Левая половина'],['right','Правая половина']],
            'briefPosition-'+number,b.locked||b.layout_rules?.movable===false?'keep':centered(b.instructions)?'center':b.layout_rules?.zone==='any'||!b.layout_rules?.zone?'auto':b.layout_rules.zone);
          const reference=select([['canvas','Холст целиком'],['video','Область основного видео']], 'briefReference-'+number,b.layout_rules?.center_reference||'canvas');
          card.append(label('Положение',position),label('Относительно чего считать центр',reference));
          const exact=make('p','Точный центр вычисляется программой. Перемещение и масштаб по ключевым кадрам при этом отключаются.');exact.className='muted';card.append(exact);
          const toggle=()=>{reference.closest('label').hidden=position.value!=='center';exact.hidden=position.value!=='center';};position.onchange=toggle;instructions.oninput=()=>{if(position.value==='auto'&&centered(instructions.value))position.value='center';toggle();};toggle();
          const speed=select([.25,.5,.75,1,1.25,1.5,2,3,4].map(n=>[String(n),n+'×']), 'briefSpeed-'+number,String(b.speed||1));
          if(!speed.value){speed.add(new Option((b.speed||1)+'×',String(b.speed||1)));speed.value=String(b.speed||1);}
          const sound=make('input');sound.type='checkbox';sound.id='briefSound-'+number;sound.checked=(b.volume||0)>0;sound.disabled=!asset.audio;
          const volume=make('input');volume.type='range';volume.min=0;volume.max=1;volume.step=.01;volume.id='briefVolume-'+number;volume.value=(b.volume||0)>0?b.volume:1;
          const volumeValue=make('output'), duration=make('p');duration.className='muted';
          let preview=null;
          if(asset.media_type==='video') {
            const soundLabel=label(asset.audio?'Включить звук баннера':'В файле нет звуковой дорожки',sound);soundLabel.className='toggle-row';
            card.append(label('Скорость видео и звука',speed),soundLabel,label('Громкость баннера',volume),volumeValue,duration);
            preview=make('video');preview.controls=true;preview.playsInline=true;preview.loop=b.loop!==false;preview.preload='metadata';preview.crossOrigin='use-credentials';preview.src=engine+'/api/banners/media/'+b.asset_id;preview.className='brief-preview';preview.onloadedmetadata=()=>{preview.currentTime=Math.min(b.offset||0,Math.max(0,preview.duration-.001));};players.push(preview);
            card.append(preview);
          }
          const sync=()=>{volume.disabled=!asset.audio||!sound.checked;volumeValue.textContent=sound.checked?Math.round(Number(volume.value)*100)+'%':'Звук выключен';duration.textContent=`Один проход: ${(Math.max(0,(asset.duration||0)-(b.offset||0))/Number(speed.value)).toFixed(2)} с. Длительность основного видео сохраняется.`;if(preview){preview.playbackRate=Number(speed.value);preview.volume=Number(volume.value);preview.muted=!sound.checked;}};
          speed.onchange=sync;sound.onchange=sync;volume.oninput=sync;sync();
          cards.push({b,groupIndex,instructions,position,reference,speed,sound,volume});$('briefBanners').append(card);
        }
      }
      if(!cards.length)$('briefBanners').append(make('p','Баннеры не выбраны. Проверьте перечисленные операции перед запуском.'));
      return await new Promise(resolve=>{
        let result=null;
        $('briefForm').onsubmit=event=>{event.preventDefault();try{for(const row of cards){const next=applyBannerBrief(row.b,{instructions:row.instructions.value,position:row.position.value,reference:row.reference.value,speed:Number(row.speed.value),sound:row.sound.checked,volume:Number(row.volume.value)},pending[row.groupIndex].config);Object.assign(row.b,next);}result=pending;dialog.close();}catch(error){$('briefError').textContent=error.message;$('briefError').hidden=false;}};
        $('briefCancel').onclick=()=>dialog.close();dialog.onclose=()=>resolve(result);dialog.showModal();
      });
    } finally { for(const player of players){player.pause();player.removeAttribute('src');player.load();}active=false; }
  }
  window.ClipBrief={confirm,applyBannerBrief,centered};
})();
