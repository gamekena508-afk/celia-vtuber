/*
 * celia-corpo-config.js — aba "Corpo" das Configuracoes (ordem do Pai, 2026-10-18).
 *   1. A aba "Live2D" passa a se chamar "Corpo" (a Celia tem 2D e 3D). So o texto do rotulo muda; o data-value continua "live2d".
 *   2. Dentro dela: opcao "Oculos" (Celia) e opcao "Slime (voce)", com o MESMO interruptor azul da casca (a linha e clonada de uma linha
 *      que ja existe no painel, entao herda as classes; nada de visual novo).
 *   3. Oculos 2D: escreve Param73 (Glasses Round) = 0.1 dentro de cubism.update (mesma tecnica do lipsync; e o mesmo valor que a expressao
 *      "Glasses (round)" do modelo aplica). Oculos 3D: mensagem {celia3d:1,tipo:"oculos",v} para a bancada (celia-3d-oculos.js).
 *   4. Slime (Tamaneko, copia de VTube Studio em web/live2d-models/tamaneko_vts/): segundo LAppModel empurrado em getLive2DManager()._models,
 *      desenhado pelo mesmo laco da Celia, preso ao topo da cabeca dela (vertices reais visiveis), com os angulos da cabeca e do corpo COPIADOS dos
 *      dela (acompanha a cabeca). A boca vem SO do microfone (evento rms-level = voz do Pai); nenhuma emocao/expressao da Celia chega nele.
 *      So no corpo 2D (no 3D nao ha Live2D para ele acompanhar).
 * Estado do Pai: localStorage "celia-corpo-config:v1" {oculos, slime, hud_minecraft}; campo ausente herda o padrao (typeof === "undefined").
 *   5. HUD Minecraft (Fase 5 V5): UM unico interruptor azul, UM unico booleano `hud_minecraft`. A HUD e uma interface so (celia-minecraft-hud.js);
 *      inventario/mapa/bussola nao tem interruptor proprio. A voz ("ligar mapa", "desligar HUD"...) grava o mesmo campo em /bridge/corpo-opcoes,
 *      e o servidor e a fonte da verdade desse campo: esta aba o segue (1 s) para refletir a voz, e o Pet o segue (250 ms).
 * Observador so de childList + rAF + escrita idempotente (regra da arvore: observador que escreve DOM sem cache trava a pagina).
 */
(function () {
  "use strict";
  if (window.celiaCorpoConfig) return;
  const Q = new URLSearchParams(location.search);
  const PET = Q.has("pet");
  // Publicacao para o Pet (storage proprio no Electron): so por gesto humano; automacao (webdriver) nao publica, para um teste nunca mudar o Pet real.
  const PUBLICAR = !navigator.webdriver || Q.has("publicaropcoes");
  const ROTA = "/bridge/corpo-opcoes";
  const CHAVE = "celia-corpo-config:v1";
  const PADRAO = { oculos: false, slime: false, hud_minecraft: false, voz_publico: false, modo_minecraft: false, enquete_mobile: false, chat_tiktok: true };
  const SLIME_DIR = "/live2d-models/tamaneko_vts/", SLIME_ARQ = "Tamaneko.model3.json";
  const SLIME_MODELO = SLIME_DIR + SLIME_ARQ;
  const PARAM_OCULOS = "Param73", VALOR_OCULOS = 0.1;

  function ler() {
    let bruto = {};
    try { bruto = JSON.parse(localStorage.getItem(CHAVE) || "{}") || {}; } catch (_) { bruto = {}; }
    const e = {};
    for (const k of Object.keys(PADRAO)) e[k] = typeof bruto[k] === "undefined" ? PADRAO[k] : bruto[k] === true;
    return e;
  }
  const estado = { ...ler(), slimeModelo: "desconhecido", slimeEstado: "desligado", slimeQuadros: 0, slimeRecriacoes: 0, pet: PET, remoto: { ok: false, boca: 0 }, mic: { rms: 0, ativo: false, th: 0.022 }, escritasOculos2d: 0, mensagens3d: 0, hook2d: false };
  window.celiaCorpoConfig = {
    estado: () => ({ ...estado }),
    definir(chave, valor) { if (!(chave in PADRAO)) return false; estado[chave] = valor === true; salvar(); if (chave === "hud_minecraft") { toqueHudEm = Date.now(); postar({ hud_minecraft: estado.hud_minecraft }); } aplicar(); sincronizarSwitches(); return true; },
  };
  function postar(corpo) {
    if (PET || !PUBLICAR) return;
    try { fetch(ROTA, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo) }).catch(() => {}); } catch (_) { /* sem rede local */ }
  }
  function salvar() {
    if (PET) return; // o Pet segue o servidor; nunca grava o storage dele
    try { localStorage.setItem(CHAVE, JSON.stringify({ oculos: estado.oculos, slime: estado.slime, hud_minecraft: estado.hud_minecraft, voz_publico: estado.voz_publico, modo_minecraft: estado.modo_minecraft, enquete_mobile: estado.enquete_mobile, chat_tiktok: estado.chat_tiktok })); } catch (_) { /* sem storage: vale na sessao */ }
    postar({ oculos: estado.oculos, slime: estado.slime });
    try { window.dispatchEvent(new CustomEvent("celia-voz-publico", { detail: { ligado: estado.voz_publico === true } })); } catch (_) { /* sem eventos */ }
  }

  // ---------- oculos 2D ----------
  let cubismHook = null, indiceOculos = -1, nucleoHook = null, eraDono = false;
  function modelo2d() {
    try {
      const ad = typeof window.getLAppAdapter === "function" ? window.getLAppAdapter() : null;
      const m = ad && typeof ad.getModel === "function" ? ad.getModel() : null;
      const cubism = m && m._model;
      const core = cubism && typeof cubism.getModel === "function" ? cubism.getModel() : null;
      return { cubism, core };
    } catch (_) { return { cubism: null, core: null }; }
  }
  function escreverOculos2d() {
    const core = nucleoHook;
    if (!core || indiceOculos < 0 || !core.parameters) return;
    if (estado.oculos) { core.parameters.values[indiceOculos] = VALOR_OCULOS; eraDono = true; estado.escritasOculos2d += 1; }
    else if (eraDono) { core.parameters.values[indiceOculos] = 0; eraDono = false; }
  }
  function instalarHook2d() {
    const { cubism, core } = modelo2d();
    if (!cubism || !core || typeof cubism.update !== "function") { estado.hook2d = false; return; }
    nucleoHook = core;
    indiceOculos = Array.from(core.parameters.ids || []).indexOf(PARAM_OCULOS);
    if (cubism !== cubismHook || !cubism.__celiaOculosHook) {
      cubismHook = cubism;
      if (!cubism.__celiaOculosHook) {
        const original = cubism.update.bind(cubism);
        cubism.update = function celiaOculosUpdate() { escreverOculos2d(); return original(...arguments); };
        cubism.__celiaOculosHook = "oculos-v1";
      }
    }
    estado.hook2d = indiceOculos >= 0;
  }
  setInterval(instalarHook2d, 500);

  // ---------- oculos 3D ----------
  function quadro3d() { return document.getElementById("celia-3d-quadro"); }
  function enviar3d() {
    const q = quadro3d();
    if (!q || !q.contentWindow) return;
    try { q.contentWindow.postMessage({ celia3d: 1, tipo: "oculos", v: estado.oculos }, location.origin); estado.mensagens3d += 1; } catch (_) { /* iframe ainda sem janela */ }
  }
  window.addEventListener("message", (e) => {
    const q = quadro3d();
    if (!q || e.source !== q.contentWindow || e.origin !== location.origin) return;
    if (e.data && e.data.celia3d === 1 && e.data.tipo === "pronto") enviar3d(); // a bancada carregou: reenvia o pedido guardado
  });
  document.addEventListener("load", (e) => { if (e.target && e.target.id === "celia-3d-quadro") enviar3d(); }, true);

  function aplicar() { instalarHook2d(); escreverOculos2d(); enviar3d(); garantirSlime(); }

  // ---------- slime: so o estado real ----------
  async function medirSlime() {
    try {
      const r = await fetch(SLIME_MODELO, { method: "HEAD", cache: "no-store" });
      estado.slimeModelo = r.ok ? "presente" : "ausente";
    } catch (_) { estado.slimeModelo = "ausente"; }
    pintarSlimeStatus();
  }

  // ---------- slime sobre a cabeca ----------
  let slime = null, slimeCarregadoEm = 0, boca = 0, slimeCore = null, slimeHookado = null;
  let piscar = { prox: 2, t: -1 };
  // Referencia do Pai (VTube Studio, backup antigo suiika_1_05_b2/items_pinned_to_model.json): o slime ia PRESO ao ArtMesh "face_skin4" da Celia,
  // vertices 81 99 88 80 97 87, com PinnedVertexCenterOffset (-0.0009, +0.047) e rotacao ~1 grau; Tamaneko com a expressao "head only" (so a cabeca).
  // Medido neste runtime: o drawable "face_skin4" existe e esses 6 vertices dao y 0.762..0.766, igual ao arquivo antigo (0.765).
  const PIN_MALHA = "face_skin4", PIN_VERTICES = [81, 99, 88, 80, 97, 87], PIN_DESLOC = [-0.0009, 0.047];
  const SLIME_LARGURA = 0.3675;    // largura da cabeca do slime, em unidades locais da Celia (a pele do rosto dela tem ~0.25). Pai, 2026-10-18: +75% sobre 0.21
  const SLIME_APOIO = -0.028;      // o pe do slime pousa NO TOPO DO CABELO, entre as orelhas e acima da tiara (local; negativo = abaixo da media dos vertices do rosto). Pai marcou a regiao em 2026-10-18
  const CABECA_Y_MIN = -0.3; // so a cabeca do slime comeca em y~-0.15; com o corpo desenhado passa de -0.48
  const HEAD_ONLY = ["Param33", "Param34", "Param35"]; // "head only.exp3.json": braco, rabo e corpo fora; fica so a cabeca
  let recriarSlime = false, caixaSlime = null, quadrosSlime = 0, malhaPin = { modelo: null, i: -1 };
  function gerente() { try { return typeof window.getLive2DManager === "function" ? window.getLive2DManager() : null; } catch (_) { return null; } }
  document.addEventListener("rms-level", (e) => {
    const d = e && e.detail; if (!d) return;
    estado.mic.rms = Number(d.rms) || 0; estado.mic.ativo = d.active === true;
    estado.mic.capturando = d.capturing === true; estado.mic.em = performance.now();
    if (Number.isFinite(d.threshold) && d.threshold > 0) estado.mic.th = d.threshold;
    publicarBoca();
  });
  function visivel(c, i) {
    if (typeof c.getDrawableDynamicFlagIsVisible === "function" && !c.getDrawableDynamicFlagIsVisible(i)) return false;
    return typeof c.getDrawableOpacity !== "function" || c.getDrawableOpacity(i) > 0.01;
  }
  // ponto de fixacao no espaco local da Celia: media dos 6 vertices da malha do rosto + o deslocamento do arquivo de referencia
  function pontoPin(cc) {
    const raw = cc.getModel();
    if (malhaPin.modelo !== raw) { malhaPin = { modelo: raw, i: Array.from(raw.drawables.ids).indexOf(PIN_MALHA) }; }
    if (malhaPin.i < 0) return null;
    const v = cc.getDrawableVertices(malhaPin.i); let x = 0, y = 0, n = 0;
    for (const k of PIN_VERTICES) if (2 * k + 1 < v.length) { x += v[2 * k]; y += v[2 * k + 1]; n += 1; }
    return n ? { x: x / n + PIN_DESLOC[0], y: y / n } : null;
  }
  function medirCaixa(c) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let i = 0; i < c.getDrawableCount(); i++) {
      if (!visivel(c, i)) continue;
      const v = c.getDrawableVertices(i);
      for (let k = 0; k + 1 < v.length; k += 2) { if (v[k] < x0) x0 = v[k]; if (v[k] > x1) x1 = v[k]; if (v[k + 1] < y0) y0 = v[k + 1]; if (v[k + 1] > y1) y1 = v[k + 1]; }
    }
    return x1 > x0 && y1 > y0 ? [x0, y0, x1, y1] : null;
  }
  function alvoBoca() {
    const m = estado.mic, th = Math.max(0.005, m.th);
    return (m.ativo || m.rms > th) ? Math.min(1, Math.max(0, (m.rms - th * 0.5) / (th * 1.25)  /* regua mais 50% menor (Pai, 2026-10-19): ainda ficava fraca; antes th*5 -> th*2.5 -> th*1.25 */)) : 0;
  }
  function alvoBocaSlime() {
    // Modo Live: a boca do slime segue o AUDIO que ele proprio toca (celia-live-personagens.js), nunca o microfone do dono
    if (window.celiaLive && window.celiaLive.ativo()) { estado.bocaFonte = "audio_do_slime"; return window.celiaLive.boca("slime"); }
    if (window.__micToggle && !window.__micToggle.state()) { estado.bocaFonte = "mudo"; return 0; }
    // The Pet can own the ASR microphone while the ordinary browser has no stream.
    const local = estado.mic.capturando && performance.now() - estado.mic.em < 500;
    estado.bocaFonte = local ? "microfone_local" : PET ? "interface_remota" : "sem_captura";
    return local ? alvoBoca() : PET ? estado.remoto.boca : 0;
  }
  function petLigado() {
    try { const st = window.celiaBrowserPet && window.celiaBrowserPet.state(); return Boolean(st && (st.desktopPetLaunch === "requested" || st.desktopPetLaunch === "pending" || st.petRequested)); } catch (_) { return false; }
  }
  let ultimaBocaPub = -1, ultimaBocaEm = 0;
  function publicarBoca() { // interface -> servidor -> Pet. So com o Pet ligado e o slime ligado; so quando muda; no maximo a cada 100 ms
    if (PET || !PUBLICAR || !estado.slime) return; // 2026-10-25: sem exigir o Pet de desktop; o estudio mobile (pet=1) tambem le a boca daqui
    const v = Math.round((window.celiaLive && window.celiaLive.ativo() ? window.celiaLive.boca("slime") : alvoBoca()) * 20) / 20, agora = performance.now();
    if (v === ultimaBocaPub && agora - ultimaBocaEm < 400) return;
    if (agora - ultimaBocaEm < 100) return;
    ultimaBocaPub = v; ultimaBocaEm = agora; postar({ boca: v });
  }
  function aplicarSlime() {
    const mgr = gerente(); if (!mgr || !slime) return;
    const celia = mgr.getModel(0); const cc = celia && celia.getModel && celia.getModel();
    const raw = slimeCore; if (!cc || !raw || !celia._modelMatrix) return;
    // 1) acompanha a cabeca/corpo dela: copia os parametros de angulo (nunca emocao/expressao)
    const ids = Array.from(raw.parameters.ids), rc = (cc.getModel && cc.getModel()) || null;
    if (rc) {
      const idsC = Array.from(rc.parameters.ids);
      // ids do slime <- ids que a Celia realmente usa (medido no cdi3 dela: AngleX/Y/Z, nao ParamAngleX)
      const MAPA = [["ParamAngleX", ["AngleX", "ParamAngleX"]], ["ParamAngleY", ["AngleY", "ParamAngleY"]], ["ParamAngleZ", ["AngleZ", "ParamAngleZ"]],
        ["ParamBodyAngleX", ["ParamBodyAngleX"]], ["ParamBodyAngleZ", ["ParamBodyAngleZ"]]];
      for (const [dest, fontes] of MAPA) {
        const a = ids.indexOf(dest); if (a < 0) continue;
        for (const f of fontes) { const b = idsC.indexOf(f); if (b >= 0) { raw.parameters.values[a] = rc.parameters.values[b]; break; } }
      }
    }
    // 2) boca: so a voz do Pai (RMS do microfone); fecha sozinha em silencio
    const alvo = alvoBocaSlime();
    boca += (alvo - boca) * (alvo > boca ? 0.55 : 0.25); if (boca < 0.01) boca = 0;
    const iab = ids.indexOf("ParamMouthOpenY"); if (iab >= 0) raw.parameters.values[iab] = boca;
    // piscada propria (nao vem da Celia)
    const agora = performance.now() / 1000;
    if (piscar.t < 0 && agora > piscar.prox) piscar.t = agora;
    let abertura = 1;
    if (piscar.t >= 0) { const k = (agora - piscar.t) / 0.16; if (k >= 1) { piscar.t = -1; piscar.prox = agora + 2.5 + Math.random() * 3.5; } else abertura = Math.abs(1 - 2 * k); }
    for (const nome of ["ParamEyeLOpen", "ParamEyeROpen"]) { const j = ids.indexOf(nome); if (j >= 0) raw.parameters.values[j] = abertura; }
    // 3) so a cabeca (expressao "head only") e sem respiracao: o Pai nao quer o slime crescendo e diminuindo
    for (const nome of HEAD_ONLY) { const j = ids.indexOf(nome); if (j >= 0) raw.parameters.values[j] = 1; }
    const jb = ids.indexOf("ParamBreath"); if (jb >= 0) raw.parameters.values[jb] = 0;
    // 4) posicao: preso ao rosto dela (mesma matriz que o laco usa para desenhar a Celia); escala FIXA, nunca derivada do corpo que se mexe
    const pin = pontoPin(cc); if (!pin) return;
    quadrosSlime += 1;
    if (!caixaSlime && quadrosSlime > 6) {
      const cx = medirCaixa(slime.getModel());
      if (cx && cx[1] < CABECA_Y_MIN && estado.slimeRecriacoes < 3) { estado.slimeRecriacoes += 1; recriarSlime = true; return; } // corpo ainda desenhado: head-only nao pegou, recria
      caixaSlime = cx; // caixa so da cabeca, depois do head-only valer; medida uma vez
    }
    if (!caixaSlime) { slime._modelMatrix.getArray()[0] = 0; return; } // ate medir, nao desenha (evita o corpo piscar)
    const M = celia._modelMatrix.getArray(), sx = M[0], sy = M[5];
    const px = sx * pin.x + M[12], py = sy * pin.y + M[13];
    const larg = caixaSlime[2] - caixaSlime[0], cxs = (caixaSlime[0] + caixaSlime[2]) / 2;
    const aj = window.celiaLive ? window.celiaLive.ajuste("slime") : { scale: 1, dx: 0, dy: 0 };   // painel do modo Live: escala e posicao
    const k = (SLIME_LARGURA * Math.abs(sx) * aj.scale) / larg;
    const A = slime._modelMatrix.getArray();
    const base = sy * SLIME_APOIO; // o pe do slime (borda de baixo do desenho) pousa nesta altura, nao o centro
    A[0] = k; A[5] = k; A[12] = px + sx * aj.dx - k * cxs; A[13] = py + sy * aj.dy + base - k * caixaSlime[1];
    estado.slimeQuadros += 1;
  }
  function instalarHookSlime() {
    if (!slime || !slime.getModel || !slime.getModel()) return false;
    const cub = slime.getModel(); const raw = cub.getModel && cub.getModel();
    if (!raw) return false;
    slimeCore = raw;
    if (slimeHookado !== cub) {
      slimeHookado = cub;
      if (!cub.__celiaSlimeHook) {
        const original = cub.update.bind(cub);
        cub.update = function celiaSlimeUpdate() { try { aplicarSlime(); } catch (e) { estado.slimeErro = String((e && e.message) || e); } return original(...arguments); };
        cub.__celiaSlimeHook = "slime-v1";
      }
    }
    return true;
  }
  function removerSlime() {
    const mgr = gerente();
    if (mgr && slime) {
      try { const v = mgr._models; for (let i = 0; i < v.getSize(); i++) if (v.at(i) === slime) { v.remove(i); break; } } catch (_) { /* gerente trocado */ }
      try { slime.release(); } catch (_) { /* ja liberado */ }
    }
    slime = null; slimeCore = null; slimeHookado = null; caixaSlime = null; quadrosSlime = 0;
  }
  function garantirSlime() {
    if (recriarSlime) { recriarSlime = false; removerSlime(); }
    const mgr = gerente(); const celia = mgr && mgr.getModel(0);
    if (!estado.slime) { if (slime) removerSlime(); estado.slimeEstado = "desligado"; pintarSlimeStatus(); return; }
    if (!mgr || !celia || !celia.constructor) { if (slime) removerSlime(); estado.slimeEstado = "sem_2d"; pintarSlimeStatus(); return; }
    if (slime) { // o gerente pode ter liberado todos os modelos (troca de cena): confere se ainda esta la
      let presente = false; try { const v = mgr._models; for (let i = 0; i < v.getSize(); i++) if (v.at(i) === slime) presente = true; } catch (_) { presente = false; }
      if (!presente) { slime = null; slimeCore = null; slimeHookado = null; caixaSlime = null; quadrosSlime = 0; }
    }
    if (!slime) {
      if (celia._state !== 22) { estado.slimeEstado = "carregando"; pintarSlimeStatus(); return; } // medido: slime criado ANTES da Celia terminar de carregar nao obedecia o "head only" (27 desenhos em vez de 23)
      if (estado.slimeModelo === "ausente") { estado.slimeEstado = "sem_modelo"; pintarSlimeStatus(); return; }
      try { const S = celia.constructor; slime = new S(); slime.loadAssets(SLIME_DIR, SLIME_ARQ); mgr._models.pushBack(slime); slimeCarregadoEm = Date.now(); estado.slimeEstado = "carregando"; }
      catch (e) { slime = null; estado.slimeEstado = "erro"; estado.slimeErro = String((e && e.message) || e); }
    } else if (instalarHookSlime()) estado.slimeEstado = estado.slimeQuadros > 0 ? "no_ar" : "carregando";
    else if (Date.now() - slimeCarregadoEm > 15000) estado.slimeEstado = "erro";
    pintarSlimeStatus();
  }
  setInterval(garantirSlime, 500);
  medirSlime();

  // Pet: segue as opcoes e a boca publicadas pela interface (servidor), de 250 em 250 ms; sem resposta, mantem o ultimo estado conhecido.
  async function seguirServidor() {
    try {
      const r = await fetch(ROTA, { cache: "no-store" }); if (!r.ok) throw new Error("http_" + r.status);
      const d = await r.json();
      estado.remoto.ok = true; estado.remoto.boca = Number(d.boca) || 0;
      const o = d.oculos === true, sl = d.slime === true;
      if (PET && (o !== estado.oculos || sl !== estado.slime)) { estado.oculos = o; estado.slime = sl; aplicar(); } // so o Pet segue oculos/slime; a interface e a dona deles
      adotarHudDoServidor(d); // a HUD e a unica opcao que a interface tambem le do servidor (a voz grava la)
    } catch (_) { estado.remoto.ok = false; estado.remoto.boca = 0; }
  }
  // HUD Minecraft: o servidor manda (a voz grava la). Logo depois de um toque local ignora a leitura por 2 s, para o POST chegar antes.
  let toqueHudEm = 0;
  function adotarHudDoServidor(d) {
    if (typeof d.modo_minecraft === "boolean" && Date.now() - toqueHudEm >= 2000 && d.modo_minecraft !== estado.modo_minecraft) { estado.modo_minecraft = d.modo_minecraft; sincronizarSwitches(); }
    if (typeof d.chat_tiktok === "boolean" && Date.now() - toqueHudEm >= 2000 && d.chat_tiktok !== estado.chat_tiktok) { estado.chat_tiktok = d.chat_tiktok; sincronizarSwitches(); }
    if (typeof d.enquete_mobile === "boolean" && Date.now() - toqueHudEm >= 2000 && d.enquete_mobile !== estado.enquete_mobile) { estado.enquete_mobile = d.enquete_mobile; sincronizarSwitches(); }
    if (typeof d.hud_minecraft !== "boolean" || Date.now() - toqueHudEm < 2000) return;
    if (d.hud_minecraft !== estado.hud_minecraft) { estado.hud_minecraft = d.hud_minecraft; sincronizarSwitches(); if (!PET) { try { localStorage.setItem(CHAVE, JSON.stringify({ oculos: estado.oculos, slime: estado.slime, hud_minecraft: estado.hud_minecraft })); } catch (_) { /* sem storage */ } } }
  }
  if (PET) { seguirServidor(); setInterval(seguirServidor, 250); }
  else {
    if (PUBLICAR) postar({ oculos: estado.oculos, slime: estado.slime }); // a interface entrega ao Pet o que o Pai ja tinha escolhido (a HUD NAO: o servidor e a verdade dela)
    seguirServidor(); setInterval(seguirServidor, 1000);
  }

  // ---------- interface ----------
  let contador = 0;
  function clonarLinha(modelo, rotulo, chave) {
    const linha = modelo.cloneNode(true);
    const sufixo = "-corpo" + (++contador);
    for (const el of linha.querySelectorAll("[id]")) el.id += sufixo;
    for (const el of [linha, ...linha.querySelectorAll("*")]) {
      if (el.id && el === linha) el.id += sufixo;
      if (el.hasAttribute("for")) el.setAttribute("for", el.getAttribute("for") + sufixo);
      if (el.hasAttribute("aria-labelledby")) el.setAttribute("aria-labelledby", el.getAttribute("aria-labelledby") + sufixo);
    }
    const p = linha.querySelector("label p");
    if (p) p.textContent = rotulo;
    linha.dataset.celiaOpcao = chave;
    const entrada = linha.querySelector('input[type="checkbox"]');
    if (entrada) entrada.addEventListener("change", () => {
      estado[chave] = entrada.checked; salvar();
      if (chave === "hud_minecraft") { toqueHudEm = Date.now(); postar({ hud_minecraft: estado.hud_minecraft }); }
      if (chave === "modo_minecraft") { toqueHudEm = Date.now(); postar({ modo_minecraft: estado.modo_minecraft }); }
      if (chave === "chat_tiktok") { toqueHudEm = Date.now(); postar({ chat_tiktok: estado.chat_tiktok }); }
      if (chave === "enquete_mobile") { toqueHudEm = Date.now(); postar({ enquete_mobile: estado.enquete_mobile }); }
      aplicar(); sincronizarSwitches(); pintarSlimeStatus();
    });
    return linha;
  }
  function pintarSwitch(linha, ligado) {
    const entrada = linha.querySelector('input[type="checkbox"]');
    if (!entrada) return;
    if (entrada.checked !== ligado) entrada.checked = ligado;
    if (ligado) entrada.setAttribute("checked", ""); else entrada.removeAttribute("checked");
    const st = ligado ? "checked" : "unchecked";
    for (const el of linha.querySelectorAll("[data-state]")) if (el.dataset.state !== st) el.dataset.state = st;
  }
  function sincronizarSwitches() {
    for (const linha of document.querySelectorAll("[data-celia-opcao]")) pintarSwitch(linha, estado[linha.dataset.celiaOpcao] === true);
  }
  function pintarSlimeStatus() {
    const e = estado.slimeEstado;
    const texto = estado.slimeModelo === "ausente" ? "Modelo do slime não encontrado."
      : !estado.slime ? "Slime desligado."
      : e === "no_ar" ? "Slime ligado: acompanha a cabeça dela e fala com a sua voz."
      : e === "carregando" ? "Carregando o slime…"
      : e === "sem_2d" ? "O slime aparece só no corpo 2D."
      : e === "erro" ? "Não consegui carregar o slime." : "Verificando o slime…";
    for (const el of document.querySelectorAll("#celia-corpo-slime-status")) if (el.textContent !== texto) el.textContent = texto;
  }
  function titulo(texto) {
    const h = document.createElement("h3");
    h.textContent = texto;
    h.style.cssText = "margin:14px 0 8px;font-size:14px;font-weight:600;color:rgba(255,255,255,.85);";
    return h;
  }

  // ---------- Discord: bot Secundario (testes, padrao) / Primario ----------
  const ROTA_DISCORD = "/bridge/discord/bot";
  let discordEstado = null, discordTimer = 0;
  function pintarDiscord() {
    const caixa = document.getElementById("celia-discord-bot");
    if (!caixa) { if (discordTimer) { clearInterval(discordTimer); discordTimer = 0; } return; }
    const d = discordEstado, nota = caixa.querySelector("[data-discord-nota]");
    for (const b of caixa.querySelectorAll("button[data-bot]")) {
      const ativo = !!d && d.bot === b.dataset.bot;
      b.setAttribute("aria-pressed", ativo ? "true" : "false");
      b.style.background = ativo ? "#3b5b94" : "transparent";
      b.style.fontWeight = ativo ? "600" : "400";
    }
    let t = "Lendo o estado do Discord…";
    if (d) {
      const nome = d.effective_bot === "primary" ? "Primário" : "Secundário (testes)";
      const st = d.status || {};
      const trocando = st.known && st.bot && st.bot !== d.effective_bot;
      t = !d.configured ? "O token do bot " + nome + " não está configurado: não conecta (não usa o outro)."
        : trocando ? "Trocando para " + nome + "… (até alguns segundos)"
        : st.connected ? "Conectado: " + nome + (st.bot_user_id_suffix ? " (…" + st.bot_user_id_suffix + ")" : "") + "."
        : "Desconectado (" + nome + "). O bot do Discord pode estar desligado.";
      if (d.forced_secondary) t += " Forçado a Secundário por CELIA_DISCORD_FORCE_SECONDARY.";
    }
    if (nota && nota.textContent !== t) nota.textContent = t;
  }
  async function lerDiscord() {
    try { const r = await fetch(ROTA_DISCORD); if (r.ok) discordEstado = await r.json(); } catch (_) { /* central fora: mantem o ultimo estado */ }
    pintarDiscord();
  }
  async function escolherDiscord(bot) {
    try {
      const r = await fetch(ROTA_DISCORD, { method: "POST", headers: { "Content-Type": "application/json", "X-Celia-Source": "ui" }, body: JSON.stringify({ bot }) });
      if (r.ok) discordEstado = await r.json();
    } catch (_) { /* sem resposta: o proximo poll mostra a verdade */ }
    pintarDiscord();
  }
  function montarFilaDiscord() {
    const rota = "/bridge/discord/call";
    const painel = document.createElement("section");
    painel.setAttribute("aria-labelledby", "celia-discord-fila-titulo");
    painel.style.cssText = "margin-top:14px;padding:12px;border:1px solid #6580ac;border-radius:8px;";
    const heading = titulo("Fila de fala");
    heading.id = "celia-discord-fila-titulo";
    heading.style.marginTop = "0";
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.style.cssText = "margin:0 0 12px;font-size:13px;overflow-wrap:anywhere;";
    status.textContent = "Carregando fila…";

    const campos = document.createElement("div");
    campos.style.cssText = "display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,140px),1fr));gap:8px;";
    function campo(rotulo, min, max, ajuda) {
      const label = document.createElement("label");
      label.style.cssText = "display:grid;align-content:start;gap:4px;font-size:13px;";
      const nome = document.createElement("span");
      nome.textContent = rotulo;
      const input = document.createElement("input");
      input.type = "number";
      input.min = String(min);
      input.max = String(max);
      input.step = "1";
      input.required = true;
      input.style.cssText = "box-sizing:border-box;width:100%;min-height:36px;padding:5px 8px;color:inherit;background:transparent;border:1px solid #6580ac;border-radius:6px;";
      const hint = document.createElement("small");
      hint.id = "celia-fila-ajuda-" + min;
      hint.textContent = ajuda;
      input.setAttribute("aria-describedby", hint.id);
      label.append(nome, input, hint);
      campos.append(label);
      return input;
    }
    const limiar = campo("Pontuação mínima", 1, 8, "1 a 8. Quanto maior, mais seletiva.");
    const silencio = campo("Silêncio (segundos)", 0, 60, "0 desliga a iniciativa no silêncio.");
    const chatDuranteCallLabel = document.createElement("label");
    chatDuranteCallLabel.style.cssText = "display:flex;align-items:center;gap:8px;margin-top:10px;font-size:13px;cursor:pointer;";
    const chatDuranteCall = document.createElement("input");
    chatDuranteCall.type = "checkbox";
    chatDuranteCall.setAttribute("aria-label", "Responder no chat durante a call");
    const chatDuranteCallTexto = document.createElement("span");
    chatDuranteCallTexto.textContent = "Responder no chat durante a call";
    chatDuranteCallLabel.append(chatDuranteCall, chatDuranteCallTexto);

    const acoes = document.createElement("div");
    acoes.style.cssText = "display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;";
    function botao(texto) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = texto;
      b.style.cssText = "min-height:40px;padding:6px 10px;border:1px solid #6580ac;border-radius:6px;color:inherit;font:inherit;font-size:13px;";
      acoes.append(b);
      return b;
    }
    const salvar = botao("Salvar");
    const vez = botao("Agora eu falo");
    vez.title = "Segura as próximas falas da Célia por até 30 segundos.";
    salvar.disabled = vez.disabled = true;
    const aviso = document.createElement("p");
    aviso.setAttribute("role", "status");
    aviso.style.cssText = "margin:6px 0 0;font-size:12px;";
    const fases = {
      listening: "Ouvindo", queued: "Célia na fila", permission: "Esperando um “pode falar”",
      speaking: "Turno da Célia", human: "Sua vez",
    };

    let atual = null;
    let gravando = false;
    function mostrar(dados, preencher = false) {
      if (preencher || !atual) {
        limiar.value = dados.threshold;
        silencio.value = dados.silence_seconds;
        chatDuranteCall.checked = dados.chat_replies_during_call !== false;
      }
      atual = dados;
      const texto = dados.calls.length
        ? dados.calls.map((call) => fases[call.phase] + (call.score ? " · " + call.score + "/8" : "")).join(" | ")
        : "Sem call ativa";
      if (status.textContent !== texto) status.textContent = texto;
      const acao = dados.holding ? "Liberar minha vez" : "Agora eu falo";
      if (vez.textContent !== acao) vez.textContent = acao;
      salvar.disabled = gravando;
      chatDuranteCall.disabled = gravando;
      vez.disabled = gravando || (!dados.calls.length && !dados.holding);
    }
    async function consultar() {
      if (!painel.isConnected || gravando) return;
      try {
        const r = await fetch(rota, { cache: "no-store" });
        if (!r.ok) throw Error();
        const dados = await r.json();
        if (!gravando) mostrar(dados);
      } catch (_) {
        if (!atual) status.textContent = "Fila indisponível.";
      }
    }
    async function gravar(dados) {
      gravando = true;
      salvar.disabled = vez.disabled = true;
      try {
        const r = await fetch(rota, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(dados),
        });
        if (!r.ok) throw Error();
        mostrar(await r.json(), !("hold" in dados));
        aviso.textContent = "hold" in dados
          ? (dados.hold ? "Sua vez por até 30 segundos." : "Vez liberada.")
          : "Configurações salvas.";
      } catch (_) {
        aviso.textContent = "Não consegui salvar. Tente novamente.";
      } finally {
        gravando = false;
        if (atual) mostrar(atual);
      }
    }
    salvar.onclick = () => {
      if (!limiar.reportValidity() || !silencio.reportValidity()) return;
      gravar({ threshold: limiar.valueAsNumber, silence_seconds: silencio.valueAsNumber,
        chat_replies_during_call: chatDuranteCall.checked });
    };
    vez.onclick = () => gravar({ hold: !atual.holding });
    painel.append(heading, status, chatDuranteCallLabel, campos, acoes, aviso);
    setTimeout(consultar, 0);
    const timer = setInterval(() => {
      if (painel.isConnected) consultar();
      else clearInterval(timer);
    }, 2000);
    return painel;
  }

  function montarVADDiscord() {
    const rota = "/bridge/discord/call";
    const painel = document.createElement("section");
    painel.id = "celia-discord-vad";
    painel.style.cssText = "margin-top:14px;padding:12px;border:1px solid #6580ac;border-radius:8px;";
    const tituloVad = titulo("Detecção de voz (VAD) — Discord");
    tituloVad.style.marginTop = "0";
    const explicacao = document.createElement("p");
    explicacao.textContent = "Escolha o detector da call. O áudio continua em PCM mono 16 kHz e vai ao ASR somente após os filtros.";
    explicacao.style.cssText = "margin:0 0 10px;font-size:12px;line-height:1.5;";
    const seletorLabel = document.createElement("label");
    seletorLabel.style.cssText = "display:grid;gap:4px;font-size:13px;max-width:360px;";
    const rotulo = document.createElement("span");
    rotulo.textContent = "Motor VAD";
    const motor = document.createElement("select");
    motor.id = "celia-discord-vad-engine";
    motor.style.cssText = "min-height:38px;padding:6px 8px;color:inherit;background:#1e2c42;border:1px solid #6580ac;border-radius:6px;";
    for (const [valor, nome] of [
      ["webrtcvad", "WebRTC VAD — voz, agressividade"],
      ["auditok", "auditok — limiar de energia"],
      ["numpy_rms", "NumPy RMS + ZCR — rápido"],
    ]) {
      const item = document.createElement("option");
      item.value = valor; item.textContent = nome; motor.append(item);
    }
    seletorLabel.append(rotulo, motor);

    const detalhes = document.createElement("details");
    detalhes.style.cssText = "margin-top:10px;";
    const cabecalho = document.createElement("summary");
    cabecalho.textContent = "Sensibilidade e filtros pré-ASR";
    cabecalho.style.cursor = "pointer";
    const campos = document.createElement("div");
    campos.style.cssText = "display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,175px),1fr));gap:9px;margin-top:10px;";
    const inputs = {};
    function campo(chave, texto, min, max, step, valor, ajuda) {
      const label = document.createElement("label");
      label.style.cssText = "display:grid;align-content:start;gap:4px;font-size:12px;";
      const labelText = document.createElement("span");
      labelText.textContent = texto;
      const input = document.createElement("input");
      input.type = "number"; input.min = String(min); input.max = String(max);
      input.step = String(step); input.value = String(valor);
      input.style.cssText = "width:100%;box-sizing:border-box;min-height:35px;padding:5px 8px;border:1px solid #6580ac;border-radius:6px;color:inherit;background:transparent;";
      const hint = document.createElement("small");
      hint.textContent = ajuda;
      label.append(labelText, input, hint);
      campos.append(label);
      inputs[chave] = input;
    }
    campo("vad_webrtc_mode", "WebRTC agressividade", 0, 3, 1, 3, "3 = mais restritivo.");
    campo("vad_auditok_energy", "auditok energia (dB)", 0, 120, 1, 50, "50 = padrão do auditok.");
    campo("vad_numpy_rms", "NumPy RMS (0–1)", 0, 1, 0.001, 0.018, "Aumentar filtra áudio baixo.");
    campo("vad_numpy_zcr_max", "NumPy ZCR máximo", 0, 1, 0.01, 0.42, "Diminuir limita chiados com muitos cruzamentos de zero.");
    campo("vad_min_segment_ms", "Duração mínima (ms)", 400, 5000, 20, 400, "400 ms ou mais, incluindo pausas.");
    campo("vad_min_voiced_ms", "Voz real mínima (ms)", 0, 2000, 20, 120, "Evita 20 ms de estalo + silêncio.");
    campo("vad_min_rms", "Energia mínima no WAV (0–1)", 0, 1, 0.001, 0.008, "Porta final antes do ASR.");
    detalhes.append(cabecalho, campos);

    const acoes = document.createElement("div");
    acoes.style.cssText = "display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:10px;";
    const salvar = document.createElement("button");
    salvar.type = "button"; salvar.textContent = "Salvar VAD";
    salvar.style.cssText = "min-height:38px;padding:7px 12px;border:1px solid #6580ac;border-radius:6px;color:inherit;font:inherit;";
    const status = document.createElement("span");
    status.setAttribute("role", "status");
    status.style.fontSize = "12px";
    acoes.append(salvar, status);
    painel.append(tituloVad, explicacao, seletorLabel, detalhes, acoes);

    let alterado = false;
    let gravando = false;
    motor.addEventListener("change", () => { alterado = true; });
    for (const input of Object.values(inputs)) input.addEventListener("input", () => { alterado = true; });
    async function ler(atualizar = false) {
      if (!painel.isConnected || gravando) return;
      try {
        const resposta = await fetch(rota, { cache: "no-store" });
        if (!resposta.ok) throw Error("offline");
        const dados = await resposta.json();
        if (!alterado || atualizar) {
          motor.value = dados.vad_engine;
          for (const [key, input] of Object.entries(inputs)) input.value = String(dados[key]);
          alterado = false;
        }
        const call = dados.calls.find((c) => c.vad_engine);
        status.textContent = call
          ? "Em uso: " + call.vad_engine + (call.vad_requested !== call.vad_engine ? " (fallback; solicitado: " + call.vad_requested + ")" : "")
          : "Configurado: " + dados.vad_engine + ". Sem call ativa; mudanças aplicadas em até ~1 segundo após a captura recomeçar.";
      } catch (_) {
        status.textContent = "Estado VAD indisponível. A central pode estar desligada.";
      }
    }
    salvar.onclick = async () => {
      if (!Object.values(inputs).every((input) => input.reportValidity())) return;
      const payload = { vad_engine: motor.value };
      for (const [key, input] of Object.entries(inputs)) payload[key] = input.valueAsNumber;
      gravando = true; salvar.disabled = true;
      try {
        const resposta = await fetch(rota, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!resposta.ok) throw Error("save");
        alterado = false;
        status.textContent = "VAD salvo. Mudança aplicada ao próximo bloco capturado.";
      } catch (_) {
        status.textContent = "Não foi possível salvar VAD. Confira os valores e a central.";
      } finally {
        gravando = false; salvar.disabled = false;
      }
    };
    setTimeout(() => ler(), 0);
    const timer = setInterval(() => {
      if (painel.isConnected) ler();
      else clearInterval(timer);
    }, 3000);
    return painel;
  }

  function montarDiscord() {
    const caixa = document.createElement("div");
    caixa.id = "celia-discord-bot";
    const rotulo = document.createElement("p");
    rotulo.id = "celia-discord-bot-rotulo";
    rotulo.textContent = "Bot do Discord";
    rotulo.style.cssText = "margin:0 0 6px;font-size:13px;color:rgba(255,255,255,.85);";
    const grupo = document.createElement("div");
    grupo.setAttribute("role", "group");
    grupo.setAttribute("aria-labelledby", "celia-discord-bot-rotulo");
    grupo.style.cssText = "display:flex;gap:0;max-width:360px;border:1px solid #6580ac;border-radius:8px;overflow:hidden;";
    for (const [bot, texto] of [["secondary", "Sina · secundário"], ["primary", "Célia · primário"]]) {
      const b = document.createElement("button");
      b.type = "button"; b.dataset.bot = bot; b.textContent = texto;
      b.style.cssText = "flex:1;padding:8px 10px;border:0;cursor:pointer;color:inherit;font:inherit;";
      b.addEventListener("click", () => escolherDiscord(bot));
      grupo.append(b);
    }
    const nota = document.createElement("p");
    nota.dataset.discordNota = "1";
    nota.style.cssText = "margin:6px 0 0;color:rgba(255,255,255,.62);font-size:12px;line-height:1.4;";
    caixa.append(rotulo, grupo, nota, montarVADDiscord(), montarFilaDiscord());
    lerDiscord();
    if (!discordTimer) discordTimer = setInterval(lerDiscord, 5000);
    return caixa;
  }

  function montar(dialogo) {
    const aba = dialogo.querySelector('[role="tab"][data-value="live2d"]');
    const painel = dialogo.querySelector('[role="tabpanel"][id$="content-live2d"]');
    if (aba) {
      const w = document.createTreeWalker(aba, NodeFilter.SHOW_TEXT);
      for (let n = w.nextNode(); n; n = w.nextNode()) if (n.nodeValue.trim() === "Live2D") n.nodeValue = "Corpo";
    }
    if (!painel || painel.querySelector("#celia-corpo-config")) return;
    const modelo = painel.querySelector(".chakra-field__root:not([data-celia-opcao])");
    if (!modelo) return; // a casca ainda nao montou as linhas originais
    dialogo.classList.add("celia-config-corpo-dialogo");
    dialogo.parentElement.classList.add("celia-config-corpo-posicao");
    if (!document.getElementById("celia-config-corpo-layout")) {
      const css = document.createElement("style");
      css.id = "celia-config-corpo-layout";
      css.textContent = 'body:has(.celia-config-corpo-dialogo[data-state="open"]) #celia-debug-boxes{visibility:hidden;}' +
        "@media (max-width:480px) {" +
        ".celia-config-corpo-posicao{overflow:visible!important;transform:none!important;}" +
        ".celia-config-corpo-dialogo{position:fixed!important;inset:38px 8px auto!important;" +
        "min-width:0!important;width:auto!important;max-width:none!important;" +
        "max-height:calc(100dvh - 46px)!important;overflow:hidden!important;}" +
        ".celia-config-corpo-dialogo .chakra-drawer__body{min-height:0;overflow:auto;overscroll-behavior:contain;}" +
        "}";
      document.head.append(css);
    }
    const secao = document.createElement("section");
    secao.id = "celia-corpo-config";
    secao.style.cssText = "padding:4px 0 10px;";
    const stack = document.createElement("div");
    stack.className = modelo.parentElement ? modelo.parentElement.className : "";
    const slimeNota = document.createElement("p");
    slimeNota.id = "celia-corpo-slime-status";
    slimeNota.style.cssText = "margin:-4px 0 0;color:rgba(255,255,255,.62);font-size:12px;line-height:1.4;";
    stack.append(clonarLinha(modelo, "Óculos 👓", "oculos"));
    secao.append(titulo("Célia"), stack);
    const stack2 = stack.cloneNode(false);
    stack2.append(clonarLinha(modelo, "Slime (você) sobre a cabeça", "slime"), slimeNota);
    secao.append(titulo("Slime"), stack2);
    const stack3 = stack.cloneNode(false);
    stack3.append(clonarLinha(modelo, "Modo Minecraft (desligado: live só de conversa)", "modo_minecraft"), clonarLinha(modelo, "HUD Minecraft", "hud_minecraft"));
    secao.append(titulo("Minecraft"), stack3);
    const stack4 = stack.cloneNode(false);
    stack4.append(clonarLinha(modelo, "Minha voz na live (use fone)", "voz_publico"));
    secao.append(titulo("Voz"), stack4);
    const ouvido = document.createElement('button');
    ouvido.type='button'; ouvido.textContent='Ligar ouvido (Tagarela)';
    ouvido.style.cssText='padding:8px 12px;border:1px solid #6580ac;border-radius:8px;cursor:pointer';
    ouvido.onclick=async()=>{
      ouvido.disabled=true;
      try{
        const r=await fetch('/bridge/live/tagarela/start',{method:'POST'});
        if(!r.ok)throw Error('Não foi possível ligar o ouvido');
        const d=await r.json();ouvido.textContent=d.state==='running'?'Ouvido ligado':'Ouvido iniciando…';
      }catch(e){ouvido.textContent='Tentar ligar ouvido novamente';}
      finally{ouvido.disabled=false;}
    };
    stack4.append(ouvido);
    const stack5 = stack.cloneNode(false);
    stack5.append(clonarLinha(modelo, "Enquete da live (mobile)", "enquete_mobile"));
    stack5.append(clonarLinha(modelo, "Célia lê o chat do TikTok", "chat_tiktok"));
    secao.append(titulo("Mobile"), stack5);
    secao.append(titulo("Discord"), montarDiscord());
    painel.append(secao);
    sincronizarSwitches();
    pintarSlimeStatus();
    medirSlime();
  }

  let agendado = false;
  function agir() {
    agendado = false;
    for (const d of document.querySelectorAll('[role="dialog"]')) montar(d);
  }
  function agendar() {
    if (agendado) return;
    agendado = true;
    requestAnimationFrame(agir);
  }
  new MutationObserver(agendar).observe(document.documentElement, { childList: true, subtree: true });
  agendar();
})();
