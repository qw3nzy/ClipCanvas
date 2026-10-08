'use strict';

// Each placed banner owns its player: two uses of one asset may have different
// offsets. All players follow source/output time rather than a separate clock.
(() => {
  function targetTime(banner, duration, clock) {
    const position = (Number(banner.offset) || 0) + (clock - banner.start) * (Number(banner.speed) || 1);
    if (!(duration > 0)) return {time: 0, ended: true};
    if (banner.loop !== false) return {time: ((position % duration) + duration) % duration, ended: false};
    return {time: Math.max(0, Math.min(duration - .001, position)), ended: position < 0 || position >= duration};
  }
  if (typeof document === 'undefined') { if (typeof module !== 'undefined') module.exports = {targetTime}; return; }

  const records = new Map(), metadata = new Map(), metadataRequests = new Map(), failures = new Set();
  const previewPanel = document.querySelector('.preview-panel');
  let synchronizing = false;

  async function asset(id, supplied) {
    if (supplied) metadata.set(id, supplied);
    if (metadata.has(id)) return metadata.get(id);
    if (!metadataRequests.has(id)) metadataRequests.set(id, api('/api/assets/'+id).then(result => { metadata.set(id, result); return result; }).finally(() => metadataRequests.delete(id)));
    return metadataRequests.get(id);
  }
  const info = id => metadata.get(id);
  const get = banner => { const record = records.get(banner.id); return record?.assetId === banner.asset_id ? record.element : null; };

  function frameChanged(record) {
    record.frame++;
    if (record.disposed) return;
    requestDraw();
    window.ClipEditor?.bannerFrameChanged?.(record.id);
  }
  function pauseRecord(record) {
    record.element.pause();
    if (record.frameHandle !== null && record.element.cancelVideoFrameCallback) record.element.cancelVideoFrameCallback(record.frameHandle);
    record.frameHandle = null;
  }
  function watchFrames(record) {
    if (record.disposed || record.frameHandle !== null || !record.element.requestVideoFrameCallback) return;
    record.frameHandle = record.element.requestVideoFrameCallback(() => {
      record.frameHandle = null;
      if (record.disposed) return;
      frameChanged(record);
      if (!record.element.paused) watchFrames(record);
    });
  }
  function dispose(id) {
    const record = records.get(id); if (!record) return;
    record.disposed = true; pauseRecord(record);
    record.rejectLoad?.(Object.assign(Error('Загрузка баннера отменена.'),{name:'AbortError'}));
    record.element.removeAttribute('src'); record.element.load(); record.element.remove();
    records.delete(id);
  }
  function disposeAll() { for (const id of [...records.keys()]) dispose(id); failures.clear(); }

  async function load(banner, supplied) {
    const details = await asset(banner.asset_id, supplied);
    if (details.media_type !== 'video') return null;
    const previous = records.get(banner.id);
    if (previous?.assetId === banner.asset_id) return previous.promise;
    if (previous) dispose(banner.id);
    const element = document.createElement('video');
    element.hidden = true; element.playsInline = true; element.preload = 'auto'; element.crossOrigin = 'use-credentials';
    element.muted = true; element.volume = 0; element.dataset.bannerId = banner.id;
    const record = {id: banner.id, assetId: banner.asset_id, element, asset: details, frame: 0, frameHandle: null, playPending: false, disposed: false, attached: false, created: performance.now()};
    records.set(banner.id, record); document.body.append(element);
    element.addEventListener('seeked', () => frameChanged(record));
    element.addEventListener('loadeddata', () => frameChanged(record));
    element.addEventListener('timeupdate', () => { if (!element.requestVideoFrameCallback) frameChanged(record); });
    element.addEventListener('play', () => watchFrames(record));
    element.addEventListener('ended', requestDraw);
    record.promise = new Promise((resolve, reject) => {
      const clean = () => { clearTimeout(timer); element.removeEventListener('loadeddata', ready); element.removeEventListener('error', fail); record.rejectLoad = null; };
      const ready = () => { clean(); failures.delete(banner.id); resolve(element); requestDraw(); };
      const fail = () => { clean(); failures.add(banner.id); reject(Error('Не удалось подготовить видеобаннер. Проверьте подключение к движку.')); };
      // The engine allows three minutes to build a browser-compatible proxy.
      const timer = setTimeout(fail, 200000);
      record.rejectLoad = error => { clean(); reject(error); };
      element.addEventListener('loadeddata', ready, {once: true}); element.addEventListener('error', fail, {once: true});
      element.src = engine+'/api/banners/media/'+encodeURIComponent(banner.asset_id); element.load();
    });
    return record.promise;
  }

  function timing(banner, details = info(banner.asset_id)) {
    const inAd = previewIndex !== null && segments[playSegment]?.kind === 'advert';
    const sourceTime = video.currentTime || 0;
    const clock = banner.timebase === 'output' ? (window.ClipPro?.outputClock?.() ?? (previewIndex === null ? (window.ClipEditor?.sourceToOutput(sourceTime) ?? sourceTime) : timelineOffset())) : sourceTime;
    const target = details && details.media_type !== 'video' ? {time:0,ended:false} : targetTime(banner, details?.duration || 0, clock);
    const rawFullscreen = Boolean(document.fullscreenElement) && previewIndex === null;
    const visible = banner.enabled && clock >= banner.start && clock < banner.end && !(inAd && banner.timebase !== 'output') && !target.ended && !rawFullscreen && !previewPanel.hidden && !document.hidden;
    return {...target, visible, inAd};
  }
  function sync() {
    if (synchronizing) return; synchronizing = true;
    try {
      const preview = window.ClipPro?.previewBanners?.() || state.banners || [];
      const master = Math.max(0,Math.min(1,Number($('previewVolume')?.value ?? video.volume)));
      const audio=window.ClipPro?.previewAudio?.()||{};
      const sourceVolume = master * (audio.mute_source?0:Math.min(1,audio.source_volume??1)) * (window.ClipPro?.previewDuckGain?.(preview) ?? 1);
      if (Math.abs(video.volume-sourceVolume) > .000001) video.volume = sourceVolume;
      const advertVolume=master*Math.min(1,audio.advert_volume??1);
      if(Math.abs(advertVideo.volume-advertVolume)>.000001)advertVideo.volume=advertVolume;
      const banners = new Map(preview.map(b => [b.id, b]));
      const originals = new Set((state.banners || []).map(b => b.id));
      for (const [id, record] of records) {
        const banner = banners.get(id);
        if (!banner && originals.has(id)) { pauseRecord(record); continue; }
        if (!banner || banner.asset_id !== record.assetId) { if (record.attached || (!record.rejectLoad && performance.now() - record.created > 60000)) dispose(id); else pauseRecord(record); continue; }
        record.attached = true;
        const media = record.element, target = timing(banner, record.asset), transport = selectedMedia();
        const volume = Math.max(0, Math.min(1, Number(banner.volume) || 0));
        media.volume = volume * master;
        media.muted = video.muted || !volume || !target.visible;
        media.loop = banner.loop !== false;
        if (!target.visible || media.readyState < 2) { pauseRecord(record); continue; }
        const running = !transport.paused && !transport.ended && !transport.seeking;
        const rate = target.inAd ? transport.playbackRate / state.advertising.speed : transport.playbackRate;
        media.playbackRate = (rate || 1) * (Number(banner.speed) || 1);
        const desired = Math.min(Math.max(0, media.duration - .005), target.time);
        // Never seek again until the previous seek finishes. Paused scrubbing
        // needs frame precision; normal playback only corrects visible drift.
        if (!media.seeking && Math.abs(media.currentTime - desired) > (running ? .16 : .018)) media.currentTime = desired;
        if (!running) { pauseRecord(record); continue; }
        if (media.paused && !record.playPending) {
          record.playPending = true;
          media.play().then(() => {
            const current = (window.ClipPro?.previewBanners?.() || state.banners || []).find(b => b.id === id);
            if (record.disposed || !current || !timing(current, record.asset).visible || selectedMedia().paused) pauseRecord(record);
            else watchFrames(record);
          }).catch(error => { if (error.name !== 'AbortError' && !record.playError) { record.playError = true; toast('Звук или видео баннера не запустились. Нажмите воспроизведение ещё раз.'); } }).finally(() => { record.playPending = false; });
        }
      }
      // Undo/redo can restore a previously disposed placement.
      for (const banner of banners.values()) if (info(banner.asset_id)?.media_type === 'video' && !records.has(banner.id) && !failures.has(banner.id)) {
        load(banner).catch(error => { if (error.name === 'AbortError') return; failures.add(banner.id); toast(error.message); });
      }
    } finally { synchronizing = false; }
  }

  function keyFrame(media, banner) {
    const key = banner.chromakey || {}; if (!key.enabled) return media;
    const record = records.get(banner.id); if (!record || media.readyState < 2) return media;
    const signature = [record.frame, key.color, key.similarity, key.blend].join(':');
    if (record.keySignature === signature) return record.keyCanvas;
    const canvas = record.keyCanvas ||= document.createElement('canvas'), ratio = Math.min(1, 720 / Math.max(media.videoWidth, media.videoHeight));
    const width = Math.max(1, Math.round(media.videoWidth * ratio)), height = Math.max(1, Math.round(media.videoHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    canvas.naturalWidth = width; canvas.naturalHeight = height;
    const context = canvas.getContext('2d', {willReadFrequently: true}); context.clearRect(0, 0, width, height); context.drawImage(media, 0, 0, width, height);
    const pixels = context.getImageData(0, 0, width, height), color = key.color || '#00ff00';
    const red = parseInt(color.slice(1, 3), 16), green = parseInt(color.slice(3, 5), 16), blue = parseInt(color.slice(5, 7), 16), similarity = key.similarity ?? .15, blend = key.blend ?? .05;
    for (let i = 0; i < pixels.data.length; i += 4) {
      const distance = Math.hypot(pixels.data[i] - red, pixels.data[i + 1] - green, pixels.data[i + 2] - blue) / (255 * Math.sqrt(3));
      const alpha = blend ? Math.max(0, Math.min(1, (distance - similarity) / blend)) : Number(distance > similarity);
      pixels.data[i + 3] = Math.round(pixels.data[i + 3] * alpha);
    }
    context.putImageData(pixels, 0, 0); record.keySignature = signature; return canvas;
  }
  for (const media of [video, advertVideo]) for (const event of ['play', 'pause', 'seeking', 'seeked', 'timeupdate', 'ratechange', 'volumechange', 'ended']) media.addEventListener(event, () => { sync(); requestDraw(); });
  for (const event of ['fullscreenchange', 'visibilitychange']) document.addEventListener(event, () => { sync(); requestDraw(); });
  new MutationObserver(sync).observe(previewPanel, {attributes: true, attributeFilter: ['hidden']});
  window.addEventListener('pagehide', disposeAll);
  window.ClipVideoBanners = {asset, info, get, load, sync, timing, targetTime, keyFrame, dispose, disposeAll};
})();
