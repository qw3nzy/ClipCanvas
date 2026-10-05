/* YouTube session settings are kept on the engine, outside project data. */
(() => {
  'use strict';
  let pending = false;
  let sessionConnected = false;

  function syncButtons() {
    const disabled = !connected || busy() || pending;
    $('youtubeCookies').disabled = disabled;
    $('disconnectYouTube').disabled = disabled || !sessionConnected;
    if (pending) {
      $('inspectLink').disabled = true;
      $('downloadLink').disabled = true;
    }
  }

  function showSession(result) {
    sessionConnected = result.connected === true;
    $('youtubeSessionStatus').textContent = sessionConnected
      ? 'Сессия YouTube подключена на ПК с движком.'
      : 'YouTube загружается без входа в аккаунт.';
    refreshButtons();
  }

  async function refreshSession() {
    try {
      showSession(await api('/api/links/youtube-session'));
    } catch (error) {
      $('youtubeSessionStatus').textContent = error.message;
    }
  }

  function invalidateInspection() {
    inspectJobId = null;
    $('linkChoices').hidden = true;
  }

  $('videoUrl').addEventListener('input', invalidateInspection);
  $('youtubeCookies').onchange = handle(async event => {
    const file = event.target.files[0];
    if (!file) return;
    if (!connected || busy()) throw Error('Дождитесь подключения движка и завершения текущей задачи.');
    pending = true;
    refreshButtons();
    try {
      if (file.size > 512 * 1024) throw Error('Экспортируйте только cookies youtube.com: файл до 512 КБ.');
      const form = new FormData();
      form.append('file', file);
      showSession(await api('/api/links/youtube-session', {method: 'POST', body: form}));
      invalidateInspection();
      toast('Сессия подключена. Нажмите «Проверить ссылку» заново.');
    } finally {
      event.target.value = '';
      pending = false;
      refreshButtons();
    }
  });
  $('disconnectYouTube').onclick = handle(async () => {
    if (busy() || pending) return;
    pending = true;
    refreshButtons();
    try {
      showSession(await api('/api/links/youtube-session', {method: 'DELETE'}));
      invalidateInspection();
      toast('Сессия YouTube удалена с ПК.');
    } finally {
      pending = false;
      refreshButtons();
    }
  });

  window.ClipImports = {connected: refreshSession, refreshButtons: syncButtons};
  refreshButtons();
  if (connected) refreshSession();
})();
