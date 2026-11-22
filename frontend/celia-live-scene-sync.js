/* Transmite a pose real da Célia à cena local, somente quando ela solicita. */
(() => {
  const query = new URLSearchParams(location.search);
  if (query.get('celiaScene') !== '1') return;
  const sceneId = query.get('sceneId');
  if (!sceneId) return;
  if (!('BroadcastChannel' in window)) return;
  const channel = new BroadcastChannel('celia-live-scene-v1');
  let requestedUntil = 0, last = 0, speech=null;
  window.addEventListener('celia-lipsync-start',event=>{speech=event.detail;});
  window.addEventListener('celia-lipsync-stop',()=>{speech=null;});
  function celiaMouth(){
    if(!speech?.audio||speech.audio.paused||speech.audio.ended)return 0;
    const volumes=speech.volumes||[], slice=(speech.sliceLengthMs||20)/1000;
    const volume=Number(volumes[Math.floor(speech.audio.currentTime/slice)])||0;
    return Math.min(.62,Math.max(0,(volume-.015)*2.4));
  }
  channel.onmessage = event => {
    if (event.data?.type === 'request-pose' && event.data.sceneId === sceneId) requestedUntil = Date.now() + 6000;
  };
  function frame(now) {
    requestAnimationFrame(frame);
    if (Date.now() > requestedUntil || now - last < 16) return;
    last = now;
    try {
      const lapp = window.getLAppAdapter?.()?.getModel?.();
      const raw = lapp?._model?.getModel?.();
      if (!raw?.parameters?.ids || !raw.parameters.values) return;
      // Não transmitir o modelo cru durante a carga das cores e identidade.
      if (!(window.celiaCores?.estado?.().aplicadas > 0)) return;
      const live = window.celiaLive?.estado?.() || {};
      channel.postMessage({type:'pose', sceneId, ids:Array.from(raw.parameters.ids), values:Array.from(raw.parameters.values),
        mouths:{...(live.bocas||{}),celia:live.falando==='celia'?(live.bocas?.celia||0):celiaMouth()}, mode:live.modo || 'off', speaking:live.falando || null, lease:live.lease === true});
    } catch (_) { /* aguarda o modelo terminar de carregar */ }
  }
  requestAnimationFrame(frame);
})();
