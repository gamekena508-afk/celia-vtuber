/* A legenda APROVADA da Celia, dentro da janela Electron dela.
 *
 * Correcao do Pai (2026-08-24): a primeira versao deste arquivo desenhava um
 * texto HTML proprio, copiado da superficie de legenda do Codex. NAO era a
 * legenda que ele aprovou. A aprovada esta descrita em docs/obs_legenda_real.md:
 * overlay em canvas, tema "neuro", fonte Chewy, animacao word pop, contorno
 * #13254e, empilhando ate 4 linhas. Em vez de imitar, esta versao embute a
 * PROPRIA pagina do overlay.
 *
 * Medido em 2026-08-24, com a Celia no ar:
 *  - http://127.0.0.1:8788/obs_subtitles?theme=neuro devolve 200;
 *  - /obs_subtitles/fonts/chewy.woff2 devolve 200 e o SHA-256 bate com
 *    ~/.local/share/fonts/Chewy-Regular.ttf — a fonte NAO falta no Linux;
 *  - a pagina renderiza com document.fonts.check("64px Chewy") === true;
 *  - o overlay nao manda X-Frame-Options nem CSP, entao o iframe entre a porta
 *    12393 e a 8788 carrega — conferido com os dois quadros na pagina.
 *  - a pagina tem 1920x1080 e o canvas 1920x430 encostado embaixo.
 *
 * Mover e dar zoom continua no Ctrl+Alt, separado do Live2D. Um gesto, um dono:
 * este arquivo e carregado ANTES de celia-pet.js no index.html, registra os
 * ouvintes de captura primeiro e corta a propagacao quando Ctrl+Alt esta ativo.
 */
(function installApprovedSubtitle() {
  "use strict";

  const parametros = new URLSearchParams(location.search);
  if (parametros.get("pet") !== "1") return;
  // CONTRATO: esta é a única superfície de legenda do Pet. Não adicionar uma
  // segunda BrowserWindow/DOM de legenda. Ver docs/CONTRATO_LEGENDA_PET.md.

  const ID = "celia-legenda-embutida";
  // v2: a superficie deixou de ser um paragrafo e virou a faixa de 1920x430 do
  // overlay. A geometria antiga nao serve, entao a chave muda em vez de herdar
  // coordenada de outro formato.
  const CHAVE = "celia-legenda-embutida:v2";
  const CHAVE_TEMA = "celia-legenda-tema";
  const BASE = "http://127.0.0.1:8788/obs_subtitles";
  const LARGURA = 1920;
  const ALTURA_PAGINA = 1080;
  const ALTURA_FAIXA = 430;
  const ESCALA_MIN = 0.25;
  const ESCALA_MAX = 2.5;
  const PASSO_ESCALA = 0.06;
  const PASSO_TECLA = 8;
  const PASSO_TECLA_RAPIDO = 32;
  const WATCHDOG_MS = 700;
  const MAX_RELOADS = 2;

  let estado = carregar();
  let modoAjuste = false;
  let arrastando = null;
  let gravarTimer = 0;
  let caixa = null;
  let quadro = null;
  let dica = null;
  let fallback = null;
  let watchdogTimer = 0;
  const telemetria = {
    ready: false,
    wsConnected: false,
    expectedSpeechId: "",
    lastRenderedSpeechId: "",
    reloads: 0,
    fallbackActive: false,
    lastState: null,
  };

  function limitar(v, a, b) { return Math.max(a, Math.min(b, v)); }

  function tema() {
    try {
      return localStorage.getItem(CHAVE_TEMA) || "neuro";
    } catch (_) {
      return "neuro";
    }
  }

  function padrao() {
    return { versao: 2, x: null, y: null, escala: null, demo: false, janela: null };
  }

  // Posicao so vale para a janela em que ela foi escolhida.
  //
  // MEDIDO em 2026-10-01: o Pet rodou um tempo no modo janela limitada
  // (520x720) por causa de uma regressao em tools/pet-electron/main.js. Ali a
  // escala automatica e 520/1920 = 0,27 e a legenda vira um fio ilegivel — foi
  // o "a legenda sumiu". Ao voltar para a superficie fixa (1920x1080), o x=-336
  // e o y=618 gravados naquele formato continuaram valendo e jogaram a faixa
  // para fora da borda esquerda.
  //
  // E a mesma licao que fez a chave subir para v2: herdar coordenada de outro
  // formato deixa a legenda fora da tela. A chave nao precisa subir de novo —
  // basta lembrar em QUAL janela a coordenada foi escolhida e descartar quando
  // a janela mudou de tamanho de verdade. O dono nao perde nada: o padrao
  // recentraliza, e a proxima vez que ele arrastar grava a janela nova.
  const TOLERANCIA_JANELA = 0.25; // 25% de diferenca ja e outro formato

  function janelaAtual() {
    return { w: window.innerWidth, h: window.innerHeight };
  }

  function mesmaJanela(gravada) {
    if (!gravada || typeof gravada.w !== "number" || typeof gravada.h !== "number") {
      // Estado antigo, sem registro de janela: so confiavel se couber na atual.
      return false;
    }
    const atual = janelaAtual();
    if (!gravada.w || !gravada.h) return false;
    const dw = Math.abs(atual.w - gravada.w) / gravada.w;
    const dh = Math.abs(atual.h - gravada.h) / gravada.h;
    return dw <= TOLERANCIA_JANELA && dh <= TOLERANCIA_JANELA;
  }

  function carregar() {
    const base = padrao();
    try {
      const cru = JSON.parse(localStorage.getItem(CHAVE) || "null");
      if (!cru || cru.versao !== 2) return base;
      // Coordenada de outro formato de janela nao vale: recentraliza.
      const herdavel = mesmaJanela(cru.janela);
      if (!herdavel && (cru.x !== null || cru.y !== null)) {
        console.info(
          "[legenda] posicao gravada em outra janela",
          cru.janela, "-> recentralizando em", janelaAtual()
        );
      }
      // Migracao: campo ausente herda o padrao. undefined nao e zero.
      return {
        versao: 2,
        x: !herdavel || typeof cru.x === "undefined" ? base.x : cru.x,
        y: !herdavel || typeof cru.y === "undefined" ? base.y : cru.y,
        escala: !herdavel || typeof cru.escala === "undefined" ? base.escala : cru.escala,
        demo: typeof cru.demo === "undefined" ? base.demo : Boolean(cru.demo),
        janela: herdavel ? cru.janela : null,
      };
    } catch (_) {
      return base;
    }
  }

  function gravar() {
    window.clearTimeout(gravarTimer);
    gravarTimer = window.setTimeout(() => {
      // Grava em QUAL janela esta posicao foi escolhida, para nao herdar
      // coordenada de outro formato depois.
      try {
        estado.janela = janelaAtual();
        localStorage.setItem(CHAVE, JSON.stringify(estado));
      } catch (_) { }
    }, 250);
  }

  // A faixa tem 1920 de largura fixa; numa janela menor ela precisa caber.
  function escalaAtual() {
    return estado.escala === null
      ? Number(Math.min(1, window.innerWidth / LARGURA).toFixed(3))
      : estado.escala;
  }

  function posicaoAtual() {
    const s = escalaAtual();
    const x = estado.x === null ? Math.round((window.innerWidth - LARGURA * s) / 2) : estado.x;
    const y = estado.y === null ? Math.round(window.innerHeight - ALTURA_FAIXA * s - 24) : estado.y;
    return { x, y, s };
  }

  function sanearGeometria() {
    const s = escalaAtual();
    const larguraVisivel = LARGURA * s;
    const alturaVisivel = ALTURA_FAIXA * s;
    const padraoX = Math.round((window.innerWidth - larguraVisivel) / 2);
    const padraoY = Math.round(window.innerHeight - alturaVisivel - 24);
    let x = estado.x === null ? padraoX : Number(estado.x);
    let y = estado.y === null ? padraoY : Number(estado.y);
    let mudou = false;

    if (!Number.isFinite(x)) { estado.x = padraoX; x = padraoX; mudou = true; }
    if (!Number.isFinite(y)) { estado.y = padraoY; y = padraoY; mudou = true; }

    const intersecaoW = Math.max(
      0,
      Math.min(x + larguraVisivel, window.innerWidth) - Math.max(x, 0),
    );
    const intersecaoH = Math.max(
      0,
      Math.min(y + alturaVisivel, window.innerHeight) - Math.max(y, 0),
    );
    const minimoW = Math.min(larguraVisivel, window.innerWidth) * 0.55;
    const minimoH = Math.min(alturaVisivel, window.innerHeight) * 0.45;

    if (intersecaoW < minimoW) { estado.x = padraoX; mudou = true; }
    if (intersecaoH < minimoH) { estado.y = padraoY; mudou = true; }

    if (estado.escala !== null) {
      const normalizada = Number(limitar(Number(estado.escala) || 1, ESCALA_MIN, ESCALA_MAX).toFixed(3));
      if (normalizada !== estado.escala) { estado.escala = normalizada; mudou = true; }
    }

    if (mudou) gravar();
    return mudou;
  }

  function aplicar() {
    if (!caixa) return;
    const { x, y, s } = posicaoAtual();
    const larguraVisivel = LARGURA * s;
    const alturaVisivel = ALTURA_FAIXA * s;
    caixa.style.left = Math.round(limitar(x, -larguraVisivel + 120, window.innerWidth - 120)) + "px";
    caixa.style.top = Math.round(limitar(y, -alturaVisivel + 40, window.innerHeight - 40)) + "px";
    caixa.style.transform = "scale(" + s + ")";
  }

  function mover(dx, dy) {
    const { x, y } = posicaoAtual();
    estado.x = Math.round(x + dx);
    estado.y = Math.round(y + dy);
    aplicar();
    gravar();
  }

  function ampliar(delta) {
    const antes = escalaAtual();
    estado.escala = Number(limitar(antes + delta, ESCALA_MIN, ESCALA_MAX).toFixed(3));
    // Fixa a posicao atual para o zoom nao empurrar a faixa pra fora da tela.
    const p = posicaoAtual();
    estado.x = p.x;
    estado.y = p.y;
    aplicar();
    gravar();
  }

  function restaurar() {
    estado = padrao();
    montarQuadro();
    aplicar();
    gravar();
  }

  function urlOverlay() {
    return BASE + "?theme=" + encodeURIComponent(tema()) + (estado.demo ? "&demo=1" : "");
  }

  function cancelarWatchdog() {
    window.clearTimeout(watchdogTimer);
    watchdogTimer = 0;
  }

  function ativarFallback(ativo) {
    telemetria.fallbackActive = Boolean(ativo);
    if (fallback) {
      fallback.style.display = telemetria.fallbackActive ? "grid" : "none";
      fallback.textContent = telemetria.fallbackActive && telemetria.lastState
        ? String(telemetria.lastState.text || "")
        : "";
    }
    if (quadro) quadro.style.visibility = telemetria.fallbackActive ? "hidden" : "visible";
  }

  function recarregarQuadro() {
    if (!quadro) return;
    const url = new URL(urlOverlay());
    url.searchParams.set("watchdog", String(Date.now()));
    telemetria.ready = false;
    telemetria.wsConnected = false;
    telemetria.reloads += 1;
    quadro.setAttribute("src", url.toString());
  }

  function agendarWatchdog(speechId) {
    cancelarWatchdog();
    if (!speechId) return;
    watchdogTimer = window.setTimeout(() => {
      if (
        telemetria.expectedSpeechId === speechId
        && telemetria.lastRenderedSpeechId !== speechId
      ) {
        ativarFallback(true);
        if (telemetria.reloads < MAX_RELOADS) {
          recarregarQuadro();
          agendarWatchdog(speechId);
        }
      }
    }, WATCHDOG_MS);
  }

  function receberEstadoOverlay(state) {
    telemetria.lastState = state || null;
    const playing = Boolean(
      state
      && state.type === "subtitle"
      && state.status === "playing"
      && state.speech_id
    );
    if (!playing) {
      telemetria.expectedSpeechId = "";
      cancelarWatchdog();
      ativarFallback(false);
      return;
    }
    const speechId = String(state.speech_id);
    if (telemetria.expectedSpeechId !== speechId) {
      telemetria.expectedSpeechId = speechId;
      telemetria.reloads = 0;
      telemetria.lastRenderedSpeechId = "";
    }
    if (fallback && telemetria.fallbackActive) {
      fallback.textContent = String(state.text || "");
    }
    agendarWatchdog(speechId);
  }

  function montarQuadro() {
    if (!caixa) return;
    if (!quadro) {
      quadro = document.createElement("iframe");
      quadro.id = ID + "-quadro";
      quadro.setAttribute("title", "Legenda da Celia");
      quadro.setAttribute("scrolling", "no");
      quadro.setAttribute("tabindex", "-1");
      Object.assign(quadro.style, {
        position: "absolute", left: "0px",
        // A pagina tem 1080 de altura e o canvas mora nos 430 de baixo. Subir a
        // pagina recorta exatamente a faixa util, sem area morta por cima.
        top: "-" + (ALTURA_PAGINA - ALTURA_FAIXA) + "px",
        width: LARGURA + "px", height: ALTURA_PAGINA + "px",
        border: "0", background: "transparent",
        // Medido em 2026-08-24: com a pagina em `color-scheme: dark` (vem do
        // celia-tema.css) e o overlay sem declarar esquema, o Chromium pinta um
        // fundo BRANCO OPACO atras do iframe — a legenda virava uma tarja
        // branca sobre a Celia. Provado na propria pagina do Pet: `normal` e
        // `light` devolvem o pixel de fundo (18,18,18); `dark` continua branco,
        // e mexer no esquema da raiz nao resolve.
        colorScheme: "normal",
        // O quadro NUNCA come clique: quem recebe gesto e a caixa, e so no
        // modo de ajuste.
        pointerEvents: "none",
      });
      caixa.insertBefore(quadro, caixa.firstChild);
    }
    const alvo = urlOverlay();
    if (quadro.getAttribute("src") !== alvo) quadro.setAttribute("src", alvo);
  }

  function pintarModo() {
    if (!caixa) return;
    caixa.dataset.ajuste = modoAjuste ? "true" : "false";
    caixa.style.pointerEvents = modoAjuste ? "auto" : "none";
    caixa.style.outline = modoAjuste ? "2px dashed rgba(255,255,255,.95)" : "none";
    caixa.style.outlineOffset = "4px";
    caixa.style.boxShadow = modoAjuste ? "0 0 0 6px rgba(0,0,0,.5)" : "none";
    caixa.style.cursor = modoAjuste ? "move" : "default";
    if (dica) dica.style.opacity = modoAjuste ? "1" : "0";
  }

  function ehAjuste(e) { return Boolean(e.ctrlKey && e.altKey); }

  function montar() {
    if (document.getElementById(ID) || !document.body) return;
    caixa = document.createElement("div");
    caixa.id = ID;
    caixa.dataset.ajuste = "false";
    Object.assign(caixa.style, {
      position: "fixed", zIndex: "2147483005",
      width: LARGURA + "px", height: ALTURA_FAIXA + "px",
      overflow: "hidden", transformOrigin: "top left",
      pointerEvents: "none", userSelect: "none", touchAction: "none",
      background: "transparent", borderRadius: "8px",
    });

    dica = document.createElement("small");
    dica.textContent = "Ctrl+Alt · arrasta move · roda amplia · setas ajustam · 0 volta ao padrao";
    Object.assign(dica.style, {
      position: "absolute", left: "0", right: "0", bottom: "6px", textAlign: "center",
      opacity: "0", transition: "opacity .12s ease", color: "#fff",
      font: "700 15px/1.35 system-ui, sans-serif", letterSpacing: ".06em",
      textShadow: "0 0 5px #000, 0 1px 3px #000, 0 -1px 3px #000", pointerEvents: "none",
    });

    fallback = document.createElement("div");
    fallback.id = ID + "-fallback";
    Object.assign(fallback.style, {
      position: "absolute", inset: "0",
      display: "none", placeItems: "end center",
      padding: "0 72px 42px", textAlign: "center",
      color: "#a8ffff", background: "transparent",
      fontFamily: '"Chewy", "Arial Black", Arial, sans-serif',
      fontSize: "64px", fontWeight: "700", lineHeight: "1.08",
      textShadow: [
        "0 0 3px #13254e", "3px 0 0 #13254e", "-3px 0 0 #13254e",
        "0 3px 0 #13254e", "0 -3px 0 #13254e", "0 8px 18px rgba(0,0,0,.7)",
      ].join(","),
      pointerEvents: "none",
    });

    caixa.appendChild(fallback);
    caixa.appendChild(dica);
    document.body.appendChild(caixa);
    montarQuadro();
    sanearGeometria();
    aplicar();
    pintarModo();
  }

  /* ── dono do gesto: Ctrl+Alt e da legenda, o resto e do Live2D ─────────── */
  document.addEventListener("pointerdown", (e) => {
    if (!ehAjuste(e) || e.button !== 0) return;
    e.stopImmediatePropagation(); e.preventDefault();
    arrastando = { x: e.screenX || e.clientX, y: e.screenY || e.clientY };
  }, true);

  document.addEventListener("pointermove", (e) => {
    if (!arrastando) return;
    e.stopImmediatePropagation();
    const x = e.screenX || e.clientX, y = e.screenY || e.clientY;
    mover(x - arrastando.x, y - arrastando.y);
    arrastando = { x, y };
  }, true);

  for (const tipo of ["pointerup", "pointercancel"]) {
    document.addEventListener(tipo, (e) => {
      if (!arrastando) return;
      e.stopImmediatePropagation();
      arrastando = null;
    }, true);
  }

  document.addEventListener("wheel", (e) => {
    if (!ehAjuste(e)) return;
    e.stopImmediatePropagation(); e.preventDefault();
    ampliar(e.deltaY < 0 ? PASSO_ESCALA : -PASSO_ESCALA);
  }, { capture: true, passive: false });

  document.addEventListener("keydown", (e) => {
    if (!ehAjuste(e)) return;
    const passo = e.shiftKey ? PASSO_TECLA_RAPIDO : PASSO_TECLA;
    const setas = {
      ArrowLeft: () => mover(-passo, 0), ArrowRight: () => mover(passo, 0),
      ArrowUp: () => mover(0, -passo), ArrowDown: () => mover(0, passo),
    };
    if (setas[e.key]) { e.stopImmediatePropagation(); e.preventDefault(); setas[e.key](); return; }
    if (e.key === "+" || e.key === "=") { e.stopImmediatePropagation(); e.preventDefault(); ampliar(PASSO_ESCALA); }
    else if (e.key === "-" || e.key === "_") { e.stopImmediatePropagation(); e.preventDefault(); ampliar(-PASSO_ESCALA); }
    else if (e.key === "0") { e.stopImmediatePropagation(); e.preventDefault(); restaurar(); }
  }, true);

  function conferirModo(e) {
    const agora = Boolean(e.ctrlKey && e.altKey);
    if (agora === modoAjuste) return;
    modoAjuste = agora;
    if (!modoAjuste) arrastando = null;
    pintarModo();
  }
  document.addEventListener("keydown", conferirModo, true);
  document.addEventListener("keyup", conferirModo, true);
  window.addEventListener("blur", () => {
    if (!modoAjuste) return;
    modoAjuste = false; arrastando = null; pintarModo();
  });
  window.addEventListener("message", (event) => {
    const msg = event && event.data;
    if (!msg || msg.channel !== "celia-subtitle") return;
    if (quadro && quadro.contentWindow && event.source !== quadro.contentWindow) return;

    if (msg.event === "ready") {
      telemetria.ready = true;
      return;
    }
    if (msg.event === "ws") {
      telemetria.wsConnected = Boolean(msg.connected);
      return;
    }
    if (msg.event === "state") {
      receberEstadoOverlay(msg.state);
      return;
    }
    if (msg.event === "rendered") {
      const speechId = String(msg.speech_id || "");
      if (!speechId) return;
      telemetria.lastRenderedSpeechId = speechId;
      if (speechId === telemetria.expectedSpeechId) {
        telemetria.reloads = 0;
        cancelarWatchdog();
        ativarFallback(false);
      }
    }
  });

  window.addEventListener("resize", () => {
    sanearGeometria();
    aplicar();
  });

  window.celiaLegendaEmbutida = {
    estado: () => ({
      ...estado,
      escalaEfetiva: escalaAtual(),
      modoAjuste,
      tema: tema(),
      url: urlOverlay(),
      overlay: { ...telemetria },
    }),
    mover, ampliar, restaurar,
    // Liga as frases automaticas do proprio overlay, para conferir o visual
    // sem depender do TTS. Nao inventa texto: e a demo que ja existe na pagina.
    demo: (ligar) => { estado.demo = Boolean(ligar); montarQuadro(); gravar(); return estado.demo; },
    definirTema: (nome) => {
      try { localStorage.setItem(CHAVE_TEMA, String(nome)); } catch (_) { }
      montarQuadro();
      return tema();
    },
    chave: () => CHAVE,
  };

  if (document.body) montar();
  else document.addEventListener("DOMContentLoaded", montar, { once: true });
}());
