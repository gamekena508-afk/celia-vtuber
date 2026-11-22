"use strict";
// celia-visao-frame.js: tela e câmera independentes, nada liga sozinho.
// DOM, fetch e timers falsos: nenhuma câmera/tela/rede/GPU de verdade.
const assert = require("node:assert");
const path = require("node:path");

const world = {
  screen: false, camera: false, accept: true,
  calls: [], intervals: [], pixel: 10, listeners: {}, beacons: [],
};

function fakeVideo(kind) {
  return {
    get srcObject() {
      return world[kind]
        ? { getVideoTracks: () => [{ readyState: "live" }] }
        : null;
    },
    readyState: 4, videoWidth: kind === "screen" ? 1280 : 640, videoHeight: kind === "screen" ? 720 : 480,
  };
}
const painel = (kind) => ({ querySelector: (s) => (s === "video" ? fakeVideo(kind) : null) });

global.localStorage = (() => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) }; })();
global.document = {
  querySelector(sel) {
    if (sel.includes("content-screen")) return painel("screen");
    if (sel.includes("content-camera")) return painel("camera");
    return null;
  },
  createElement() {
    return {
      width: 0, height: 0,
      getContext: () => ({
        drawImage() {},
        getImageData: () => ({ data: new Uint8ClampedArray(16 * 9 * 4).fill(world.pixel) }),
      }),
      toDataURL: () => "data:image/jpeg;base64,AAAA",
    };
  },
};
global.window = global;
global.addEventListener = (t, cb) => { world.listeners[t] = cb; };
Object.defineProperty(global, "navigator", {
  value: { sendBeacon: (url, blob) => { world.beacons.push({ url, blob }); return true; } },
  configurable: true,
});
global.setInterval = (fn, ms) => { world.intervals.push({ fn, ms }); return world.intervals.length; };
global.clearInterval = () => {};
global.fetch = async (url, opts) => {
  const body = JSON.parse(opts.body);
  world.calls.push({ url, body });
  return {
    ok: true,
    json: async () => (url.endsWith("/state")
      ? { ok: true, active: body.active }
      : (world.accept ? { ok: true, accepted: true, bytes: 4 } : { ok: true, accepted: false, reason: "source_inactive" })),
  };
};

require(path.resolve(__dirname, "..", "frontend", "celia-visao-frame.js"));
const vigia = world.intervals.find((i) => i.ms === 500).fn;
const ciclo = world.intervals.find((i) => i.ms !== 500).fn;
const flush = () => new Promise((r) => setImmediate(r));
const estados = () => world.calls.filter((c) => c.url.endsWith("/state")).map((c) => `${c.body.source}:${c.body.active}`);
const frames = () => world.calls.filter((c) => c.url.endsWith("/frame")).map((c) => c.body.source);

(async () => {
  // 1) nada ligado: abrir/recarregar a página não inicia NADA, por mais ciclos que passem
  for (let i = 0; i < 6; i++) { vigia(); ciclo(); await flush(); }
  assert.deepStrictEqual(world.calls, [], "sem stream vivo não há aviso nem frame");

  // 2) o clique liga só a tela: avisa a tela, depois manda frames só da tela
  world.screen = true;
  ciclo(); await flush();
  assert.deepStrictEqual(frames(), [], "frame nunca antes do aviso 'ligada'");
  vigia(); await flush();
  assert.deepStrictEqual(estados(), ["screen:true"]);
  assert.strictEqual(world.calls[0].body.width, 1280);
  ciclo(); await flush();
  assert.deepStrictEqual(frames(), ["screen"]);

  // 3) ligar a câmera é um clique SEPARADO; só então ela é avisada e enviada
  world.camera = true;
  vigia(); await flush();
  assert.deepStrictEqual(estados(), ["screen:true", "camera:true"]);
  ciclo(); await flush();
  assert.deepStrictEqual(frames().slice(-2).sort(), ["camera", "screen"]);

  // 4) desligar a tela avisa SÓ a tela; a câmera segue
  world.screen = false;
  vigia(); await flush();
  assert.deepStrictEqual(estados().slice(-1), ["screen:false"]);
  const antes = frames().length;
  ciclo(); await flush();
  assert.deepStrictEqual(frames().slice(antes), ["camera"], "só a câmera continua enviando");

  // 5) servidor recusa (reiniciou) com a câmera ainda viva: reenvia o aviso sozinho
  world.accept = false;
  ciclo(); await flush();
  world.accept = true;
  const n = estados().length;
  vigia(); await flush();
  assert.deepStrictEqual(estados().slice(n), ["camera:true"], "re-anuncia sem precisar de novo clique");

  // 6) página fechando: sendBeacon desliga o que estava ligado
  assert.ok(world.listeners.pagehide, "ouve pagehide");
  world.listeners.pagehide();
  assert.strictEqual(world.beacons.length, 1);
  assert.match(world.beacons[0].url, /\/bridge\/vision\/state$/);

  // 7) trava de emergência: desligar() avisa que tudo foi desligado, mesmo com stream vivo
  window.celiaVisaoFrame.desligar();
  await flush();
  vigia(); await flush();
  const resumo = window.celiaVisaoFrame.estado();
  assert.strictEqual(resumo.config.ligado, false);
  const aposTrava = frames().length;
  world.camera = true;
  ciclo(); await flush();
  assert.strictEqual(frames().length, aposTrava, "trava de emergência não envia frame");

  // estado() mantém as chaves antigas e traz as duas fontes
  assert.ok("fontes" in resumo && "screen" in resumo.fontes && "camera" in resumo.fontes);
  assert.ok("enviados" in resumo && "painelPresente" in resumo && "videoVivo" in resumo);

  console.log("test_celia_visao_frame_fontes: ok");
})().catch((e) => { console.error(e); process.exit(1); });
