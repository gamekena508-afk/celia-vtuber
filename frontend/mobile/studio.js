(() => {
  "use strict";
  // ?observar=1 (diagnostic probes) announces claim 0: such a page never takes the publisher seat.
  const PAGE_CLAIM = new URLSearchParams(location.search).get("observar") === "1" ? 0 : Date.now();
  // Output is 1080x1920 (Full HD vertical). S scales every pixel constant that was measured on the
  // old 720x1280 canvas (framing points, caption metrics) so the composition does not change.
  const S = 1.5, W = 720 * S, H = 900 * S, /* 1080x1350 (4:5), pedido do Pai 25/10 */ byId = (id) => document.getElementById(id);
  // Compositor choice is read BEFORE any context exists: the visible canvas becomes WebGL or 2D,
  // never both. Default (no ?compositor=webgl) is the existing 2D path, untouched.
  let canvas = byId("composite"), ctx = null;
  const compositorRequest = new URLSearchParams(location.search).get("compositor") === "webgl" ? "webgl" : "2d";
  let glCompositor = null, glFailure = "";
  if (compositorRequest === "webgl") {
    try {glCompositor = createGlCompositor(canvas);}
    catch (err) {glCompositor = null; glFailure = String(err && err.message || err);}
    // A canvas that got a webgl context cannot give 2D: replace it (same id, size, label).
    if (!glCompositor) canvas = replaceCanvas(canvas);
  }
  if (!glCompositor) ctx = canvas.getContext("2d", {alpha: false});
  const compositorLabel = glCompositor ? "webgl" : (compositorRequest === "webgl" ? "2d (fallback: " + glFailure + ")" : "2d");
  const iframe = byId("source-stage"), status = byId("studio-status");
  // The Live2D canvas inside the iframe is sized clientWidth*devicePixelRatio. Size the iframe in CSS
  // pixels so that product is exactly W x H: a scaled KDE display (dpr 0.9) otherwise yields a source
  // below the output size and the composite just upscales it.
  function fitSource() {
    const dpr = Number(window.devicePixelRatio) > 0 ? Number(window.devicePixelRatio) : 1;
    iframe.style.width = Math.round(W / dpr) + "px";
    iframe.style.height = Math.round(H / dpr) + "px";
  }
  fitSource();
  window.addEventListener("resize", fitSource);
  const colors = {black: "#040408", violet: "#221135", blue: "#0e2342", green: "#0c3034", studio: "#28202e"};
  const key = "celia-mobile-studio-framing-v1";
  // A/B switch: ?codec=vp8 prefers video/VP8 for the stream; default stays video/H264.
  const codecPreference = new URLSearchParams(location.search).get("codec") === "vp8" ? "video/vp8" : "video/h264";
  const BG_DB = "celia-mobile-studio", BG_STORE = "backgrounds", BG_KEY = "current", BG_MAX = 12 * 1024 * 1024;
  // Measured on the 720x1280 source canvas at zoom 1, pose 0 (headless Xvfb capture,
  // 2026-10-23): top of the slime outline at y=17; bottom of the corset/belt (waist) at y=850.
  const FRAMING = {srcTop: 17 * S, srcWaist: 850 * S, topMargin: 48 * S};
  // Pose that puts head, ears and slime fully inside the frame and cuts the figure at the waist.
  // Output point = H*(0.5+y) + (srcY - H/2)*zoom  ->  solve for zoom and y.
  function waistUpPose() {
    const zoom = (H - FRAMING.topMargin) / (FRAMING.srcWaist - FRAMING.srcTop);
    const y = (FRAMING.topMargin - (FRAMING.srcTop - H / 2) * zoom) / H - 0.5;
    return {x: 0, y: Math.round(y * 1000) / 1000, zoom: Math.round(zoom * 1000) / 1000};
  }
  let pose = {x: 0, y: 0, zoom: 1, scene: "violet", body: "2d", model: "celia_v3_web.vrm"};
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "null");
    if (saved && typeof saved === "object") {
      for (const k of Object.keys(pose)) if (saved[k] !== undefined) pose[k] = saved[k];
    }
  } catch (_) {}
  pose.zoom = Math.max(0.4, Math.min(2.8, Number(pose.zoom) || 1));
  pose.x = Math.max(-0.85, Math.min(0.85, Number(pose.x) || 0));
  pose.y = Math.max(-0.85, Math.min(0.85, Number(pose.y) || 0));
  if (!(pose.scene in colors)) pose.scene = "violet";
  if (!["2d", "3d"].includes(pose.body)) pose.body = "2d";
  if (!["celia_v3_web.vrm", "celia_v4_web.vrm"].includes(pose.model)) pose.model = "celia_v3_web.vrm";
  let caption = "";
  // Altura da legenda (fracao da altura da tela, 0 = topo). Ajustada no controle "Legenda: altura"; antes era fixa no centro (0.5), em cima do rosto.
  const capKey = "celia-mobile-studio-legenda-v2";
  let capY = 0.833; // centro do terco de baixo da tela (25/10/2026)
  try {const savedCap = JSON.parse(localStorage.getItem(capKey) || "null"); if (savedCap && Number.isFinite(Number(savedCap.y))) capY = Number(savedCap.y);} catch (_) {}
  capY = Math.max(0.1, Math.min(0.92, capY));
  // ---- Enquete da live (pedido do Pai, 2026-10-25) ----
  // Arte da enquete no topo do quadro, com placar vivo por presentes do TikTok (central: /bridge/enquete).
  // Liga/desliga: aba Corpo > Mobile > "Enquete da live (mobile)". Ligada, a Celia e reenquadrada abaixo
  // da arte (cintura para cima, um pouco menor) para a enquete nao cobrir o slime nem as orelhas.
  const ENQ = {
    img: new Image(), pronta: false, ativa: false, rodada: null,
    alvo: {lula: 0, flavio: 0}, mostra: {lula: 0, flavio: 0},
    // 25/10 15h: arte nova 1895x830 (sem nomes, sem "enquete"). Caixas da contagem e barra rosa/azul, em fracao da arte.
    caixas: {flavio: [377 / 1895, 562 / 830, 657 / 1895, 676 / 830], lula: [1443 / 1895, 562 / 830, 1711 / 1895, 676 / 830]}, // invertido 25/10 (metades da arte trocadas)
    barra: [86 / 1895, 729 / 830, 1805 / 1895, 780 / 830],
    cores: {lula: "#e8457f", flavio: "#3b8fe8"},
  };
  ENQ.img.onload = () => {ENQ.pronta = true;};
  ENQ.selo = new Image(); ENQ.selo.src = "/mobile/static/selo-ia.png?v=20261025";
  ENQ.img.src = "/mobile/static/enquete-base.webp?v=20261025inv4";
  const enqKey = "celia-mobile-studio-enquete-v1";
  let enqLargura = 0.8;
  try {const savedEnq = JSON.parse(localStorage.getItem(enqKey) || "null"); if (savedEnq && Number.isFinite(Number(savedEnq.largura))) enqLargura = Number(savedEnq.largura);} catch (_) {}
  enqLargura = Math.max(0.5, Math.min(1, enqLargura));
  function enqVisivel() { return false; } // enquete desligada 25/10 (restricao TikTok); antes: return ENQ.ativa && ENQ.pronta;
  function enqRect() {
    const w = W * enqLargura, prop = (ENQ.img.naturalHeight && ENQ.img.naturalWidth) ? ENQ.img.naturalHeight / ENQ.img.naturalWidth : 1024 / 1536;
    return {x: (W - w) / 2, y: 14 * S, w, h: w * prop};
  }
  // inclui um "tique" de 80 ms para a faisca da barra piscar na textura de cima
  function enqChave() { return enqVisivel() ? enqLargura + ":" + ENQ.mostra.lula + ":" + ENQ.mostra.flavio + ":" + Math.floor(performance.now() / 80) : ""; }
  // Barra rosa x azul: so as cores, sem numero. Total 0 -> meio a meio. Faisca no encontro das cores.
  function paintBarra(c, r) {
    const [fx0, fy0, fx1, fy1] = ENQ.barra;
    const x0 = r.x + fx0 * r.w, y0 = r.y + fy0 * r.h, bw = (fx1 - fx0) * r.w, bh = (fy1 - fy0) * r.h;
    const tot = ENQ.mostra.lula + ENQ.mostra.flavio;
    const frac = tot > 0 ? Math.max(0.04, Math.min(0.96, ENQ.mostra.flavio / tot)) : 0.5; // invertido 25/10: flavio na esquerda
    ENQ.fracVis = ENQ.fracVis == null ? frac : ENQ.fracVis + (frac - ENQ.fracVis) * 0.12;
    // vai-e-volta de poucos pixels no encontro das cores
    const xm = x0 + bw * ENQ.fracVis + Math.sin(performance.now() / 450) * bh * 0.18, rad = bh / 2;
    c.save();
    roundedRect(c, x0, y0, bw, bh, rad); c.clip();
    c.fillStyle = ENQ.cores.flavio; c.fillRect(x0, y0, xm - x0, bh);
    c.fillStyle = ENQ.cores.lula; c.fillRect(xm, y0, x0 + bw - xm, bh);
    const brilho = c.createLinearGradient(0, y0, 0, y0 + bh);
    brilho.addColorStop(0, "rgba(255,255,255,0.35)"); brilho.addColorStop(0.5, "rgba(255,255,255,0)");
    c.fillStyle = brilho; c.fillRect(x0, y0, bw, bh);
    c.restore();
    // Four-point sparkle, with a tiny horizontal drift; the score boundary stays exact.
    const t = performance.now() / 1000;
    const sx = xm + Math.sin(t * 1.7) * 2 * S, sy = y0 + rad;
    const size = bh * (0.85 + 0.08 * Math.sin(t * 2.2));
    c.save();
    c.shadowColor = "#ffe9bb"; c.shadowBlur = 9 * S;
    c.fillStyle = "#fff6dc";
    c.beginPath();
    c.moveTo(sx, sy - size);
    c.quadraticCurveTo(sx + size * 0.12, sy - size * 0.12, sx + size * 0.7, sy);
    c.quadraticCurveTo(sx + size * 0.12, sy + size * 0.12, sx, sy + size);
    c.quadraticCurveTo(sx - size * 0.12, sy + size * 0.12, sx - size * 0.7, sy);
    c.quadraticCurveTo(sx - size * 0.12, sy - size * 0.12, sx, sy - size);
    c.fill(); c.restore();
  }

  // Numero "rolando": a cada quadro anda ~18% do que falta ate o placar real.
  function enqPasso() {
    for (const k of ["lula", "flavio"]) {
      const a = ENQ.alvo[k], m = ENQ.mostra[k];
      if (m === a) continue;
      const d = a - m;
      ENQ.mostra[k] = Math.abs(d) <= 1 ? a : m + Math.sign(d) * Math.max(1, Math.ceil(Math.abs(d) * 0.18));
    }
  }
  // Pose usada enquanto a enquete aparece: cabeca/slime logo abaixo da arte, corte na cintura (mesma conta do "Cintura para cima").
  function poseEfetiva() {
    if (!enqVisivel()) return pose;
    const r = enqRect(), topo = r.y + r.h + 10 * S;
    const zoom = (H - topo) / (FRAMING.srcWaist - FRAMING.srcTop);
    const y = (topo - (FRAMING.srcTop - H / 2) * zoom) / H - 0.5;
    return Object.assign({}, pose, {y, zoom});
  }
  function paintEnquete(c) {
    if (!enqVisivel()) return;
    const r = enqRect();
    c.save();
    c.drawImage(ENQ.img, r.x, r.y, r.w, r.h);
    for (const k of ["lula", "flavio"]) {
      const [fx0, fy0, fx1, fy1] = ENQ.caixas[k];
      const x0 = r.x + fx0 * r.w, y0 = r.y + fy0 * r.h, bw = (fx1 - fx0) * r.w, bh = (fy1 - fy0) * r.h;
      c.fillStyle = "#ffffff";
      roundedRect(c, x0 + 2 * S, y0 + 2 * S, bw - 4 * S, bh - 4 * S, Math.min(bw, bh) * 0.16);
      c.fill();
      const txt = String(ENQ.mostra[k]);
      let fs = bh * 0.74;
      c.font = "900 " + Math.round(fs) + "px system-ui, sans-serif";
      while (fs > 8 && c.measureText(txt).width > bw * 0.86) { fs *= 0.9; c.font = "900 " + Math.round(fs) + "px system-ui, sans-serif"; }
      c.fillStyle = ENQ.cores[k]; c.textAlign = "center"; c.textBaseline = "middle";
      c.fillText(txt, x0 + bw / 2, y0 + bh / 2 + fs * 0.05);
    }
    if (ENQ.selo.complete && ENQ.selo.naturalWidth) {
      const lado = (480 / 1895) * r.w, cx = r.x + 0.5 * r.w, cy = r.y + (394 / 830) * r.h; // tamanho dos retratos
      c.drawImage(ENQ.selo, cx - lado / 2, cy - lado / 2, lado, lado);
    }
    c.restore();
    paintBarra(c, r);
  }
  function paintAiLabel(c) {
    c.save();
    c.font = "500 " + Math.round(14 * S) + "px Georgia, serif";
    c.fillStyle = "rgba(10,14,22,.7)";
    roundedRect(c, 16 * S, H - 37 * S, 176 * S, 25 * S, 8 * S); c.fill();
    c.fillStyle = "#f1ecdf"; c.textBaseline = "middle";
    c.fillText("IA \u2022 assistente virtual", 25 * S, H - 24 * S);
    c.restore();
  }
  let renderFrames = 0; // counts composite redraws; read-only telemetry, see sampleTransport
  let background = null, backgroundUrl = null;
  let ws = null, retry = null, running = false, output = null, shareStream = null;
  let shareVideo = null, audioCtx = null, audioDest = null;
  const peers = new Map(), pendingIce = new Map(), waiting = new Set();

  function setStatus(message) { status.textContent = message; }
  function save() {
    try {localStorage.setItem(key, JSON.stringify(pose));} catch (_) {}
  }
  function publishState() {
    save();
    for (const name of ["scene", "body", "model"]) byId(name).value = pose[name];
    syncFramingInputs();
    send({type: "stage-state", state: pose});
  }
  function syncFramingInputs() {
    byId("zoom").value = String(pose.zoom);
    byId("pan-x").value = String(pose.x);
    byId("pan-y").value = String(pose.y);
    byId("framing-readout").textContent = "Zoom " + pose.zoom.toFixed(2) + " · horizontal " + pose.x.toFixed(2) + " · vertical " + pose.y.toFixed(2);
  }
  function setPose(next) {
    if (typeof next.zoom === "number") pose.zoom = Math.max(0.4, Math.min(2.8, next.zoom));
    if (typeof next.x === "number") pose.x = Math.max(-0.85, Math.min(0.85, next.x));
    if (typeof next.y === "number") pose.y = Math.max(-0.85, Math.min(0.85, next.y));
    publishState();
  }
  const capInput = byId("caption-y");
  if (capInput) {
    capInput.value = String(capY);
    capInput.addEventListener("input", (e) => {
      const v = Number(e.target.value);
      if (!Number.isFinite(v)) return;
      capY = Math.max(0.1, Math.min(0.92, v));
      try {localStorage.setItem(capKey, JSON.stringify({y: capY}));} catch (_) {}
    });
  }
  for (const id of ["zoom", "pan-x", "pan-y"]) {
    byId(id).addEventListener("input", (e) => {
      const value = Number(e.target.value);
      if (!Number.isFinite(value)) return;
      if (id === "zoom") setPose({zoom: value});
      else if (id === "pan-x") setPose({x: value});
      else setPose({y: value});
    });
  }
  byId("waist-up").addEventListener("click", () => {
    const preset = waistUpPose();
    setPose({x: preset.x, y: preset.y, zoom: preset.zoom});
    setStatus("Enquadramento: cintura para cima (cabeça, orelhas e slime inteiros).");
  });
  function sourceUrl() {
    const url = new URL("/", location.origin);
    // pet=1 makes the iframe adopt the server's slime/glasses options; liveOutput=1
    // forbids physical camera/microphone/screen capture inside it (mic_toggle.js).
    url.searchParams.set("pet", "1");
    url.searchParams.set("liveOutput", "1");
    url.searchParams.set("palco", "1");
    url.searchParams.set("corpo", pose.body);
    url.searchParams.set("fundo", "cena");
    url.searchParams.set("mobileStudio", "1");
    // Versioned query: forces the iframe to refetch HTML cached before
    // celia-live-output-guard.js was inserted as the first script.
    url.searchParams.set("liveRevision", "20261023a");
    if (pose.body === "3d") url.searchParams.set("vrm", pose.model);
    return url.pathname + url.search;
  }
  function reloadSource() {
    iframe.src = sourceUrl();
    setStatus("Carregando Célia " + pose.body.toUpperCase() + " no PC…");
  }
  for (const name of ["scene", "body", "model"]) {
    byId(name).value = pose[name];
    byId(name).addEventListener("change", (e) => setChoice(name, e.target.value));
  }
  function setChoice(name, value) {
    if (name === "scene" && !(value in colors)) return;
    if (name === "body" && !["2d", "3d"].includes(value)) return;
    if (name === "model" && !["celia_v3_web.vrm", "celia_v4_web.vrm"].includes(value)) return;
    if (pose[name] === value) return;
    pose[name] = value;
    if (name !== "scene" && (name === "body" || pose.body === "3d")) reloadSource();
    publishState();
  }
  function applyControl(message) {
    switch (message.action) {
      case "zoom": pose.zoom = Math.max(0.4, Math.min(2.8, pose.zoom + message.step)); break;
      case "move":
        pose.x = Math.max(-0.85, Math.min(0.85, pose.x + message.dx));
        pose.y = Math.max(-0.85, Math.min(0.85, pose.y + message.dy)); break;
      case "center": pose.x = 0; pose.y = 0; pose.zoom = 1; break;
      case "scene": case "body": case "model": setChoice(message.action, message.value);return;
      default: return;
    }
    publishState();
  }
  byId("recenter").addEventListener("click", () => applyControl({action: "center"}));
  function setBgState(message) { byId("background-state").textContent = message; }
  function errText(err) { return String(err && err.message || err); }
  // Background image lives in IndexedDB (bounded: one record, <= BG_MAX), not localStorage,
  // because a 2 MB PNG as base64 would crowd the quota that the pose key also uses.
  function bgTx(mode, action) {
    return new Promise((resolve, reject) => {
      if (!window.indexedDB) {reject(new Error("armazenamento local indisponível neste navegador")); return;}
      const open = indexedDB.open(BG_DB, 1);
      open.onupgradeneeded = () => open.result.createObjectStore(BG_STORE);
      open.onerror = () => reject(open.error || new Error("não abriu o armazenamento"));
      open.onsuccess = () => {
        const db = open.result;
        const tx = db.transaction(BG_STORE, mode);
        const request = action(tx.objectStore(BG_STORE));
        tx.oncomplete = () => {db.close(); resolve(request ? request.result : undefined);};
        tx.onerror = tx.onabort = () => {db.close(); reject(tx.error || new Error("gravação local falhou"));};
      };
    });
  }
  function imageFromBlob(blob) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(blob), image = new Image();
      image.onload = () => resolve({image, url});
      image.onerror = () => {URL.revokeObjectURL(url); reject(new Error("imagem ilegível"));};
      image.src = url;
    });
  }
  function applyBackground(image, url) {
    if (backgroundUrl) URL.revokeObjectURL(backgroundUrl);
    backgroundUrl = url; background = image;
  }
  function clearBackground() {
    background = null;
    if (backgroundUrl) URL.revokeObjectURL(backgroundUrl);
    backgroundUrl = null;
  }
  byId("stop-background").addEventListener("click", async () => {
    clearBackground();
    try {
      await bgTx("readwrite", (store) => store.delete(BG_KEY));
      setBgState("Cenário próprio removido.");
    } catch (err) {
      setBgState("Saiu da tela, mas o arquivo guardado não foi apagado: " + errText(err));
    }
  });
  byId("background-image").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file || !["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > BG_MAX) {
      setBgState("Escolha PNG/JPG/WebP de até 12 MB."); return;
    }
    setBgState("Carregando cenário próprio…");
    let loaded;
    try {loaded = await imageFromBlob(file);}
    catch (err) {setBgState("Não foi possível carregar o cenário: " + errText(err)); return;}
    applyBackground(loaded.image, loaded.url);
    try {
      await bgTx("readwrite", (store) => store.put({name: file.name, type: file.type, size: file.size, blob: file, savedAt: Date.now()}, BG_KEY));
      setBgState("Cenário próprio guardado neste PC (" + file.name + "). Continua após recarregar.");
    } catch (err) {
      setBgState("Aplicado nesta sessão, mas NÃO foi guardado: " + errText(err));
    }
  });
  async function restoreBackground() {
    try {
      const record = await bgTx("readonly", (store) => store.get(BG_KEY));
      if (!record) {setBgState("Nenhum cenário próprio guardado."); return;}
      const blob = record.blob;
      if (!(blob instanceof Blob) || blob.size > BG_MAX || !["image/png", "image/jpeg", "image/webp"].includes(blob.type)) {
        setBgState("O cenário guardado está inválido e foi ignorado. Escolha o arquivo de novo."); return;
      }
      const loaded = await imageFromBlob(blob);
      applyBackground(loaded.image, loaded.url);
      setBgState("Cenário próprio restaurado do PC (" + (record.name || "sem nome") + ").");
    } catch (err) {
      setBgState("Não foi possível restaurar o cenário guardado: " + errText(err));
    }
  }

  function frameCanvas() {
    try {
      const doc = iframe.contentDocument;
      // In pet mode the iframe follows the server's body choice (/bridge/body), so
      // trust the body it actually started with when it reports one.
      const actual = iframe.contentWindow && iframe.contentWindow.__celiaCorpo;
      if ((actual === "2d" || actual === "3d" ? actual : pose.body) === "3d") {
        const nested = doc && doc.getElementById("celia-3d-quadro");
        return nested && nested.contentDocument && nested.contentDocument.querySelector("#palco canvas");
      }
      return doc && doc.getElementById("canvas");
    } catch (_) {return null;}
  }
  function cover(image) {
    const sw = image.videoWidth || image.naturalWidth || image.width;
    const sh = image.videoHeight || image.naturalHeight || image.height;
    if (!sw || !sh) return;
    const factor = Math.max(W / sw, H / sh);
    const dw = sw * factor, dh = sh * factor;
    ctx.drawImage(image, (W - dw) / 2, (H - dh) / 2, dw, dh);
  }
  function roundedRect(c, x, y, w, h, radius) {
    c.beginPath();
    c.roundRect(x, y, w, h, radius);
  }
  // Caption layout. c = target 2D context (visible canvas in 2D mode, caption canvas in WebGL mode).
  function paintCaption(c = ctx, text = caption) {
    if (!text) return;
    c.save();
    const words = text.trim().split(/\s+/);
    c.font = 'bold ' + Math.round(37 * S) + 'px system-ui, sans-serif';
    const lines = [], maxWidth = W - 100 * S;
    let line = "";
    for (const word of words) {
      const next = (line ? line + " " : "") + word;
      if (line && c.measureText(next).width > maxWidth) { lines.push(line); line = word; }
      else line = next;
    }
    if (line) lines.push(line);
    const display = lines.slice(0, 4), lineHeight = 51 * S, boxHeight = display.length * lineHeight + 32 * S;
    const boxWidth = W - 56 * S, cx = W / 2;
    const cy = Math.max(boxHeight / 2 + 8 * S, Math.min(H - boxHeight / 2 - 8 * S, H * capY));
    c.fillStyle = "rgba(0,0,0,.91)"; roundedRect(c, cx - boxWidth/2, cy - boxHeight/2, boxWidth, boxHeight, 21 * S); c.fill();
    c.fillStyle = "#fff"; c.textAlign = "center"; c.textBaseline = "middle";
    display.forEach((s, index) => c.fillText(s, cx, cy + (index - (display.length - 1)/2) * lineHeight, maxWidth));
    c.restore();
  }

  // ---- Optional WebGL2 compositor (?compositor=webgl). Same geometry as the 2D path. ----
  function replaceCanvas(old) {
    // Fresh element with the same id, size and label; the old one goes away.
    const next = document.createElement("canvas");
    next.id = old.id; next.width = old.width; next.height = old.height;
    const label = old.getAttribute("aria-label");
    if (label) next.setAttribute("aria-label", label);
    old.replaceWith(next);
    return next;
  }
  function parseHex(hex) {
    const n = parseInt(String(hex).slice(1), 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  // Destination rects [x, y, w, h] in output pixels, mirroring the 2D code exactly.
  // cover(): factor = max(W/sw, H/sh), centred. Layer: base = min(W/sw, H/sh); the 2D code
  // translates to (W*(0.5+x), H*(0.5+y)), scales by zoom and draws centred on that point.
  function coverRect(sw, sh, dstW, dstH) {
    const factor = Math.max(dstW / sw, dstH / sh), dw = sw * factor, dh = sh * factor;
    return [(dstW - dw) / 2, (dstH - dh) / 2, dw, dh];
  }
  function layerRect(sw, sh, p, dstW, dstH) {
    const scale = Math.min(dstW / sw, dstH / sh) * p.zoom;
    const cx = dstW * (0.5 + p.x), cy = dstH * (0.5 + p.y);
    const dw = sw * scale, dh = sh * scale;
    return [cx - dw / 2, cy - dh / 2, dw, dh];
  }
  function createGlCompositor(el) {
    const gl = el.getContext("webgl2", {alpha: false, antialias: false, depth: false, stencil: false, preserveDrawingBuffer: true});
    if (!gl) throw new Error("getContext(webgl2) devolveu null");
    const compile = (type, src) => {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src); gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh) || "shader");
      return sh;
    };
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, `#version 300 es
in vec2 aUnit;
uniform vec4 uDest;
uniform vec2 uRes;
out vec2 vUv;
void main() {
  vec2 p = uDest.xy + aUnit * uDest.zw;
  gl_Position = vec4(p.x / uRes.x * 2.0 - 1.0, 1.0 - p.y / uRes.y * 2.0, 0.0, 1.0);
  vUv = aUnit;
}`));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, `#version 300 es
precision mediump float;
uniform sampler2D uTex;
in vec2 vUv;
out vec4 outColor;
void main() { outColor = texture(uTex, vUv); }`));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) || "program");
    gl.useProgram(prog);
    const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]), gl.STATIC_DRAW);
    const aUnit = gl.getAttribLocation(prog, "aUnit");
    gl.enableVertexAttribArray(aUnit); gl.vertexAttribPointer(aUnit, 2, gl.FLOAT, false, 0, 0);
    const uDest = gl.getUniformLocation(prog, "uDest");
    gl.uniform1i(gl.getUniformLocation(prog, "uTex"), 0);
    gl.uniform2f(gl.getUniformLocation(prog, "uRes"), W, H);
    // Row 0 = top of the image (v=0 at top). Straight alpha: no premultiply on upload or in the shader.
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    const makeTexture = () => {
      const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      return t;
    };
    const srcTex = makeTexture(), bgTex = makeTexture(), capTex = makeTexture();
    const capCanvas = document.createElement("canvas");
    capCanvas.width = W; capCanvas.height = H;
    const capCtx = capCanvas.getContext("2d");
    const state = {error: "", lost: false, capKey: "", capDirty: false, bgSource: null};
    el.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
      state.lost = true;
      if (!state.error) state.error = "contexto WebGL perdido";
    });
    const upload = (tex, source) => {
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
    };
    const drawQuad = (tex, r) => {
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.uniform4f(uDest, r[0], r[1], r[2], r[3]);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    };
    return {
      state,
      // Caption texture is repainted only when the text changes (caption canvas cleared first).
      setCaption(text) {
        const next = text || "";
        // A textura de cima carrega legenda + enquete; so e refeita quando uma das duas muda.
        const chave = next + "|" + enqChave();
        if (chave === state.capKey) return;
        state.capKey = chave;
        capCtx.clearRect(0, 0, W, H);
        paintEnquete(capCtx);
        paintAiLabel(capCtx);
        if (next) paintCaption(capCtx, next);
        state.capDirty = true;
      },
      draw(opts) {
        if (state.lost || gl.isContextLost()) {
          state.lost = true;
          if (!state.error) state.error = "contexto WebGL perdido";
          return false;
        }
        try {
          const c = parseHex(colors[opts.scene] || "#040408");
          gl.bindVertexArray(vao); gl.useProgram(prog);
          gl.viewport(0, 0, W, H);
          gl.disable(gl.SCISSOR_TEST);
          gl.clearColor(c[0], c[1], c[2], 1); gl.clear(gl.COLOR_BUFFER_BIT);
          gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
          const bg = opts.background;
          if (bg && bg.complete && bg.naturalWidth) {
            if (state.bgSource !== bg) {upload(bgTex, bg); state.bgSource = bg;}
            drawQuad(bgTex, coverRect(bg.naturalWidth, bg.naturalHeight, W, H));
          }
          const layer = opts.layer;
          const sw = layer && (layer.videoWidth || layer.width), sh = layer && (layer.videoHeight || layer.height);
          if (sw && sh) {
            upload(srcTex, layer); // per frame: the Live2D frame changes every draw
            drawQuad(srcTex, layerRect(sw, sh, opts.pose, W, H));
          }
          if (opts.hasCaption) {
            if (state.capDirty) {upload(capTex, capCanvas); state.capDirty = false;}
            drawQuad(capTex, [0, 0, W, H]);
          }
          return true;
        } catch (err) {
          state.error = "falha ao desenhar: " + String(err && err.message || err);
          return false;
        }
      },
    };
  }
  let glErrorShown = false;
  function renderGl() {
    const layer = (shareVideo && shareVideo.readyState >= 2) ? shareVideo : frameCanvas();
    enqPasso();
    glCompositor.setCaption(caption);
    const ok = glCompositor.draw({scene: pose.scene, background, layer, pose: poseEfetiva(), hasCaption: true});
    if (!ok && !glErrorShown) {
      glErrorShown = true;
      setStatus("ERRO do compositor WebGL: " + glCompositor.state.error + ". Recarregue o estúdio ou use sem ?compositor=webgl. Nada foi transmitido como sucesso.");
    }
  }
  function render() {
    renderFrames += 1;
    if (glCompositor) {renderGl(); requestAnimationFrame(render); return;}
    ctx.fillStyle = colors[pose.scene] || "#040408";
    ctx.fillRect(0, 0, W, H);
    if (background && background.complete) cover(background);
    const jogo = (shareVideo && shareVideo.readyState >= 2) ? shareVideo : null;
    if (jogo) {
      // Modo Minecraft (25/10): jogo inteiro em cima, Celia pequena embaixo (canvas 4:5).
      const gw = jogo.videoWidth, gh = jogo.videoHeight;
      if (gw && gh) {
        const f = W / gw, dh = Math.min(gh * f, H * 0.6);
        ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, dh);
        try {ctx.drawImage(jogo, 0, 0, W, gh * f > dh ? dh : gh * f);} catch (_) {}
        const av = frameCanvas();
        const aw = av && (av.width), ah = av && (av.height);
        if (aw && ah) {
          const areaH = H - dh, b = Math.min((W * 0.45) / aw, areaH / ah);
          try {ctx.drawImage(av, 8 * S, dh + (areaH - ah * b) / 2, aw * b, ah * b);} catch (_) {}
        }
      }
    }
    const layer = jogo ? null : frameCanvas();
    if (layer) {
      // Draw once at the source's own aspect (contain into 720x1280): a 720x1280 Pet canvas
      // is 1:1, and any other source is never stretched. No second avatar, no extra pass.
      const sw = layer.videoWidth || layer.width, sh = layer.videoHeight || layer.height;
      if (sw && sh) {
        const base = Math.min(W / sw, H / sh);
        ctx.save();
        const pe = poseEfetiva();
        ctx.translate(W * (0.5 + pe.x), H * (0.5 + pe.y));
        ctx.scale(pe.zoom, pe.zoom);
        try {ctx.drawImage(layer, -sw * base / 2, -sh * base / 2, sw * base, sh * base);} catch (_) {}
        ctx.restore();
      }
    }
    enqPasso(); paintEnquete(ctx); // enquete por cima do avatar, abaixo da legenda
    paintAiLabel(ctx);
    paintCaption(); // always drawn after the avatar; fixed at absolute center
    requestAnimationFrame(render);
  }
  requestAnimationFrame(render);
  async function refreshCaption() {
    try {
      const r = await fetch("/mobile/studio/status", {cache: "no-store"});
      if (r.ok) {const data = await r.json(); caption = data.caption && data.caption.text || "";}
    } catch (_) {caption = "";}
  }
  refreshCaption(); setInterval(refreshCaption, 220);
  async function refreshEnquete() {
    try {
      const r = await fetch("/bridge/enquete", {cache: "no-store"});
      if (!r.ok) return;
      const d = await r.json();
      ENQ.ativa = d.ativa === true;
      if (Number.isFinite(d.lula)) ENQ.alvo.lula = d.lula;
      if (Number.isFinite(d.flavio)) ENQ.alvo.flavio = d.flavio;
      if (d.rodada !== ENQ.rodada) {ENQ.rodada = d.rodada; ENQ.mostra.lula = ENQ.alvo.lula; ENQ.mostra.flavio = ENQ.alvo.flavio;} // carga inicial ou zerada: sem rolar para tras
    } catch (_) {}
  }
  refreshEnquete(); setInterval(refreshEnquete, 1000);
  const enqInput = byId("enquete-size");
  if (enqInput) {
    enqInput.value = String(enqLargura);
    enqInput.addEventListener("input", (e) => {
      const v = Number(e.target.value);
      if (!Number.isFinite(v)) return;
      enqLargura = Math.max(0.5, Math.min(1, v));
      try {localStorage.setItem(enqKey, JSON.stringify({largura: enqLargura}));} catch (_) {}
    });
  }
  const enqZerar = byId("enquete-zerar");
  if (enqZerar) enqZerar.addEventListener("click", async () => {
    if (!confirm("Zerar a enquete? O placar volta para 0 x 0.")) return;
    try {const r = await fetch("/bridge/enquete/zerar", {method: "POST"}); setStatus(r.ok ? "Enquete zerada: 0 x 0." : "ERRO ao zerar a enquete (HTTP " + r.status + ")."); refreshEnquete();}
    catch (err) {setStatus("ERRO ao zerar a enquete: " + String(err && err.message || err));}
  });

  // ---- Read-only transmission telemetry ----
  // Only the fields listed here are read or shown. No SDP, addresses, candidates or
  // internal ids reach the page. Nothing here changes the stream, codec or peers.
  const statsEl = byId("transport-stats");
  const clock = () => (typeof performance !== "undefined" ? performance.now() : Date.now());
  const renderTrack = {frames: 0, at: clock()};
  let statsBusy = false, lastEncoded = null;
  const fmt = (v, digits) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "—");
  const text = (v) => (v === undefined || v === null || v === "" ? "—" : String(v));
  function activePeer() {
    for (const peer of peers.values()) {
      if (peer.connectionState !== "closed" && peer.connectionState !== "failed") return peer;
    }
    return null;
  }
  function showStats(lines) { statsEl.textContent = lines.join("\n"); }
  async function sampleTransport() {
    if (statsBusy) return; // single-flight: a slow getStats never stacks another call
    statsBusy = true;
    try {
      const now = clock();
      const dtRender = (now - renderTrack.at) / 1000;
      const renderFps = dtRender > 0 ? (renderFrames - renderTrack.frames) / dtRender : null;
      renderTrack.frames = renderFrames; renderTrack.at = now;
      const renderError = glCompositor && glCompositor.state.error ? " · ERRO: " + glCompositor.state.error : "";
      // Measured 2026-10-23: a hidden tab gets ~1 fps (rAF is frozen for the studio and for the
      // Live2D iframe), and the encoder then downscales to 180x320. Say it on screen.
      const hiddenTab = document.visibilityState !== "visible";
      const renderLine = "Render do composite (PC, " + compositorLabel + "): " + W + "x" + H + " · " + fmt(renderFps, 1) + " fps" + renderError +
        (hiddenTab ? "\nATENÇÃO: esta aba está em segundo plano; o navegador congela o vídeo. Deixe o estúdio numa janela própria e visível." : "");
      if (!running) {showStats([renderLine, "Transmissão: parada."]); return;}
      const peer = activePeer();
      if (!peer) {showStats([renderLine, "Transmissão: aguardando celular (nenhum par ativo)."]); return;}
      let report;
      try {report = await peer.getStats();}
      catch (_) {showStats([renderLine, "Estatística indisponível: conexão fechada ou recusada."]); lastEncoded = null; return;}
      let out = null;
      for (const s of report.values()) {
        if (s.type === "outbound-rtp" && (s.kind === "video" || s.mediaType === "video")) {out = s; break;}
      }
      if (!out) {showStats([renderLine, "Codificação: sem fluxo de vídeo de saída ainda."]); return;}
      const codec = out.codecId ? report.get(out.codecId) : null;
      // Encoder fps: the browser's own figure when present; otherwise measured from framesEncoded.
      let encFps = null, encSource = "—";
      if (typeof out.framesPerSecond === "number") {encFps = out.framesPerSecond; encSource = "reportado";}
      else if (lastEncoded && lastEncoded.peer === peer && typeof out.framesEncoded === "number") {
        const dt = (now - lastEncoded.at) / 1000;
        if (dt > 0) {encFps = (out.framesEncoded - lastEncoded.frames) / dt; encSource = "medido Δframes/Δt";}
      }
      if (typeof out.framesEncoded === "number") lastEncoded = {peer, frames: out.framesEncoded, at: now};
      showStats([
        renderLine,
        "Codificação: " + text(out.frameWidth) + "x" + text(out.frameHeight) + " · " + fmt(encFps, 1) + " fps (" + encSource + ") · codec " + text(codec && codec.mimeType),
        "Frames codificados: " + text(out.framesEncoded) + " · tempo de codificação: " + fmt(out.totalEncodeTime, 2) + " s",
        "Limitação de qualidade: " + text(out.qualityLimitationReason),
        "Pacotes enviados: " + text(out.packetsSent) + " · bytes enviados: " + text(out.bytesSent),
      ]);
    } catch (_) {
      showStats(["Estatística indisponível neste momento."]);
    } finally {
      statsBusy = false;
    }
  }
  setInterval(sampleTransport, 2000);
  sampleTransport();

  function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  }
  function connect() {
    clearTimeout(retry);
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    // claim = when this page was loaded: the newest studio page takes the seat from an older one.
    ws = new WebSocket(scheme + "//" + location.host + "/mobile/ws?role=studio&claim=" + PAGE_CLAIM);
    ws.addEventListener("open", publishState);
    ws.addEventListener("message", async (event) => {
      let msg; try {msg = JSON.parse(event.data);} catch (_) {return;}
      if (msg.type === "ready") {
        setStatus("Estúdio online no PC. " + (running ? "Transmitindo." : "Clique Iniciar transmissão."));
        for (const id of msg.viewers || []) waiting.add(id);
        if (running) for (const id of Array.from(waiting)) createPeer(id);
      }
      if (msg.type === "viewer-joined") {
        waiting.add(msg.id);
        publishState();
        if (running) createPeer(msg.id);
      }
      if (msg.type === "viewer-left") {waiting.delete(msg.id);closePeer(msg.id);}
      if (msg.type === "control") applyControl(msg);
      if (msg.type === "signal" && msg.id && msg.data) {
        const peer = peers.get(msg.id);
        if (peer) {
          try {
            if (msg.data.type === "answer" && msg.data.sdp) {
              await peer.setRemoteDescription({type: "answer", sdp: msg.data.sdp});
              for (const ice of pendingIce.get(msg.id) || []) await peer.addIceCandidate(ice);
              pendingIce.delete(msg.id);
            }
            if (msg.data.type === "candidate" && msg.data.candidate) {
              if (peer.remoteDescription) await peer.addIceCandidate(msg.data.candidate);
              else {
                const list = pendingIce.get(msg.id) || [];
                if (list.length < 80) list.push(msg.data.candidate);
                pendingIce.set(msg.id, list);
              }
            }
          } catch (err) {console.warn("Sinalização recebida rejeitada", err);}
        }
      }
    });
    ws.addEventListener("close", () => {
      for (const id of Array.from(peers.keys())) closePeer(id);
      setStatus("Conexão com servidor perdida. Reconectando…");
      retry = setTimeout(connect, 2300);
    });
    ws.addEventListener("error", () => {if (ws) ws.close();});
  }

  function closePeer(id) {
    const peer = peers.get(id);
    pendingIce.delete(id);
    if (peer) {peer.close(); peers.delete(id);}
  }
  async function createPeer(id) {
    closePeer(id);
    if (!running || !output) return;
    const peer = new RTCPeerConnection({iceServers: []});
    peers.set(id, peer);
    peer.onicecandidate = ({candidate}) => {
      if (candidate) send({type: "signal", id, data: {type: "candidate", candidate: candidate.toJSON()}});
    };
    peer.onconnectionstatechange = () => {
      if (peer.connectionState === "failed" || peer.connectionState === "closed") closePeer(id);
    };
    const video = output.getVideoTracks()[0];
    const transceiver = peer.addTransceiver(video, {direction: "sendonly", streams: [output]});
    try {
      const codecs = RTCRtpSender.getCapabilities("video").codecs || [];
      const preferred = codecs.filter(c => c.mimeType.toLowerCase() === codecPreference);
      if (preferred.length) transceiver.setCodecPreferences([...preferred, ...codecs.filter(c => !preferred.includes(c))]);
    } catch (_) {}
    for (const track of output.getAudioTracks()) peer.addTrack(track, output);
    try {
      await peer.setLocalDescription(await peer.createOffer());
      // Measured 2026-10-23: the receiver got 180x320 from a 720x1280 source because the
      // encoder had no bitrate target and downscaled on its own. Ask for detail, keep the
      // resolution, and give it a real ceiling. Failure here only loses the hint.
      try {
        if (video.contentHint !== undefined) video.contentHint = "detail";
        const sender = transceiver.sender, params = sender.getParameters();
        if (!params.encodings || !params.encodings.length) params.encodings = [{}];
        params.encodings[0].maxBitrate = 4500000;
        params.encodings[0].scaleResolutionDownBy = 1;
        params.encodings[0].maxFramerate = 30;
        params.degradationPreference = "maintain-resolution";
        await sender.setParameters(params);
      } catch (err) {console.warn("Parâmetros de qualidade do vídeo não aplicados", err);}
      send({type: "signal", id, data: {type: "offer", sdp: peer.localDescription.sdp}});
    } catch (err) {console.warn("Falha ao iniciar vídeo", err);closePeer(id);}
  }
  function hookAudio() {
    // Stage plays the original TTS; capture that element's PCM once, without playing twice.
    if (!audioCtx || !audioDest) return;
    let win; try {win = iframe.contentWindow;} catch (_) {return;}
    if (!win || !win.HTMLMediaElement) return;
    const proto = win.HTMLMediaElement.prototype;
    if (proto.__celiaMobileCaptured) return;
    const original = proto.play;
    const wired = new WeakSet();
    proto.play = function (...args) {
      if (!wired.has(this)) {
        try {
          const source = audioCtx.createMediaElementSource(this);
          source.connect(audioDest);
          source.connect(audioCtx.destination);
          wired.add(this);
        } catch (err) {console.warn("Áudio não capturado neste elemento", err);}
      }
      return original.apply(this, args);
    };
    proto.__celiaMobileCaptured = true;
  }
  // Voz do Pai na transmissao do celular (2026-10-25). Segue o mesmo botao do Corpo, "Minha voz na live (use fone)":
  // o localStorage e o mesmo (mesma origem) e o evento "storage" avisa quando a outra aba muda. O microfone vai SO para
  // o audio que segue para o celular (audioDest), nunca para os alto-falantes do PC (sem eco no proprio PC).
  let micStream = null, micGain = null, micPedido = false;
  function vozPublicoLigada() {
    try {return JSON.parse(localStorage.getItem("celia-corpo-config:v1") || "{}").voz_publico === true;} catch (_) {return false;}
  }
  async function aplicarMicLive() {
    if (!audioCtx || !audioDest) return;
    const ligada = vozPublicoLigada();
    if (ligada && !micStream && !micPedido) {
      micPedido = true;
      try {
        micStream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false}}) /* 2026-10-25: com AEC ligado o Chrome cancelava a propria voz que o botao voz_publico toca no PC (voz cortada, fraca, repetida) */;
        micGain = audioCtx.createGain(); micGain.gain.value = 0;
        audioCtx.createMediaStreamSource(micStream).connect(micGain);
        micGain.connect(audioDest);
      } catch (err) {setStatus("ERRO: microfone não liberado para a transmissão (" + String(err && err.message || err) + ").");}
      micPedido = false;
    }
    if (micGain) micGain.gain.setTargetAtTime(ligada ? 1 : 0, audioCtx.currentTime, 0.03);
  }
  window.addEventListener("storage", (e) => {if (e.key === "celia-corpo-config:v1") aplicarMicLive();});
  iframe.addEventListener("load", () => {hookAudio();setStatus(running ? "Palco sincronizado. Transmitindo." : "Palco carregado. Clique Iniciar transmissão.");});
  byId("share-window").addEventListener("click", async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      setStatus("Captura de janela indisponível neste navegador."); return;
    }
    try {
      if (shareStream) shareStream.getTracks().forEach(t => t.stop());
      shareStream = await navigator.mediaDevices.getDisplayMedia({video: {frameRate: 30}, audio: true});
      shareVideo = document.createElement("video");
      shareVideo.srcObject = shareStream; shareVideo.muted = true;
      await shareVideo.play();
      shareStream.getVideoTracks()[0].addEventListener("ended", () => {shareVideo = null;shareStream = null;setStatus("Compartilhamento encerrado; usando palco interno.");});
      setStatus("Janela selecionada; agora clique Iniciar transmissão.");
    } catch (_) {setStatus("Captura cancelada. O palco interno permanece disponível.");}
  });
  byId("start").addEventListener("click", async () => {
    if (running) return;
    if (!canvas.captureStream || !window.RTCPeerConnection) {
      setStatus("Este navegador não oferece WebRTC / canvas.captureStream."); return;
    }
    try {
      const AudioEngine = window.AudioContext || window.webkitAudioContext;
      if (AudioEngine && !audioCtx) {
        audioCtx = new AudioEngine();
        audioDest = audioCtx.createMediaStreamDestination();
      }
      if (audioCtx) await audioCtx.resume();
      hookAudio();
      await aplicarMicLive();
      const captured = canvas.captureStream(30);
      output = new MediaStream(captured.getVideoTracks());
      const sharedAudio = shareStream && shareStream.getAudioTracks()[0];
      if (sharedAudio) output.addTrack(sharedAudio);
      else if (audioDest) output.addTrack(audioDest.stream.getAudioTracks()[0]);
      running = true;
      byId("start").disabled = true; byId("stop").disabled = false;
      for (const id of Array.from(waiting)) await createPeer(id);
      setStatus("Transmitindo vídeo 9:16. Confirme o áudio no celular antes da live.");
    } catch (error) {setStatus("Não foi possível transmitir: " + String(error && error.message || error));}
  });
  byId("stop").addEventListener("click", () => {
    running = false;
    for (const id of Array.from(peers.keys())) closePeer(id);
    if (output) output.getTracks().forEach(t => {if (!shareStream || !shareStream.getTracks().includes(t)) t.stop();});
    output = null;byId("start").disabled = false;byId("stop").disabled = true;
    setStatus("Transmissão interrompida. O celular aguardará uma nova conexão.");
  });
  if (!["localhost", "127.0.0.1", "::1"].includes(location.hostname)) {
    byId("mobile-url").textContent = location.origin + "/mobile";
  }
  reloadSource();
  syncFramingInputs();
  restoreBackground();
  connect();
  window.celiaMobileStudioFraming = {waistUpPose, FRAMING, W, H};
  window.celiaMobileStudioCompositor = {coverRect, layerRect, parseHex, label: () => compositorLabel, error: () => (glCompositor ? glCompositor.state.error : "")};
})();
