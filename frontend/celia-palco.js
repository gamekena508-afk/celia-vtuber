/* Isolates the existing Live2D path when the page is used as a stage/Pet. */
(function instalarPalcoCelia() {
  "use strict";

  const params = new URLSearchParams(location.search);
  const ehPalco = params.get("palco") === "1";

  // Sobrepostas nossas que TEM de sobreviver a limpeza do palco. Sem esta
  // lista a legenda embutida nascia e era escondida com !important no mesmo
  // quadro — medido em 2026-08-24.
  const PERMITIDOS = new Set(["mic-toggle-banner", "celia-legenda-embutida", "celia-confusion-accessory", "celia-3d-quadro", "celia-mc-hud"]); // celia-mc-hud: HUD Minecraft (uma interface so), camada abaixo do Live2D

  const FUNDOS = {
    escuro: "#0b0714",
    verde: "#00b140", // chroma key padrão de TV
    cena: "transparent", // deixa o fundo do próprio app aparecer
  };

  /* ── modo palco ─────────────────────────────────────────────────────── */
  // Escrita idempotente: so conta como mudanca o que de fato muda. Sem isso, cada
  // mutacao de DOM (legenda, "Thinking...") reaplicava os mesmos estilos e
  // disparava um resize falso; React e Live2D reescreviam canvas.width (1440 e
  // 1439, arredondamentos diferentes com dpr 0.9) e cada atribuicao apaga o
  // buffer WebGL. Medido 2026-10-05: ~3 resizes falsos/s, quadros vazios = a
  // Celia "sumindo".
  let mudou = false;
  // Trilhos do PerfectScrollbar sao sobreposicoes absolutas: nao mudam o layout
  // do canvas. O PerfectScrollbar escuta resize e reexibe os trilhos no update();
  // se esconde-los contasse como mudanca, o resize que disparamos o faria
  // reexibir, e o laco voltava (medido: 20 ciclos em 8 s so com ps__rail-x/y).
  function afetaLayout(el) {
    for (const c of el.classList) if (c.startsWith("ps__rail")) return false;
    return true;
  }
  // Compara o atributo style antes/depois: o navegador serializa valores de
  // forma diferente da escrita (background "transparent" volta expandido), entao
  // comparar el.style[prop] com o valor escrito acusava mudanca falsa toda vez.
  function estilo(el, prop, valor) {
    const antes = el.getAttribute("style");
    el.style[prop] = valor;
    if (el.getAttribute("style") !== antes && afetaLayout(el)) mudou = true;
  }

  function limparInterface() {
    const alvo = document.getElementById("live2d-internal-wrapper");
    const raiz = document.getElementById("root");
    if (!alvo || !raiz) return false;
    mudou = false;

    /* Esconder por classe seria frágil: o bundle gera nomes embaralhados a
     * cada build. Em vez disso, subo de `alvo` até `root` e escondo os
     * IRMÃOS de cada nível. O que sobra é exatamente o caminho até o canvas
     * — funciona mesmo se a árvore mudar de forma no próximo build.
     */
    let no = alvo;
    while (no && no !== raiz && no.parentElement) {
      for (const irmao of Array.from(no.parentElement.children)) {
        if (irmao !== no) estilo(irmao, "display", "none");
      }
      estilo(no, "flex", "1 1 auto");
      estilo(no, "width", "100%");
      estilo(no, "height", "100%");
      estilo(no, "maxWidth", "none");
      estilo(no, "maxHeight", "none");
      no = no.parentElement;
    }

    // The compiled shell also creates portals directly under body (for example
    // notifications). They are outside #root, so sibling cleanup alone cannot
    // remove them from the native Pet. Only the root/canvas path is allowed.
    for (const filho of Array.from(document.body.children)) {
      if (filho === raiz || PERMITIDOS.has(filho.id)) continue;
      const antes = filho.getAttribute("style");
      filho.style.setProperty("display", "none", "important");
      if (filho.getAttribute("style") !== antes && afetaLayout(filho)) mudou = true;
    }

    const fundo = FUNDOS[params.get("fundo")] || FUNDOS.escuro;
    for (const el of [document.documentElement, document.body, raiz]) {
      estilo(el, "background", fundo);
      estilo(el, "margin", "0");
      estilo(el, "padding", "0");
      estilo(el, "overflow", "hidden");
    }
    estilo(raiz, "width", "100vw");
    estilo(raiz, "height", "100vh");

    if (mudou) window.dispatchEvent(new Event("resize"));
    return true;
  }

  let cleanupScheduled = false;
  function scheduleCleanup() {
    if (cleanupScheduled) return;
    cleanupScheduled = true;
    requestAnimationFrame(() => {
      cleanupScheduled = false;
      limparInterface();
    });
  }

  if (ehPalco) {
    // React can mount its shell after the Live2D wrapper. Reapply only when
    // that tree changes, including for the native Electron Pet.
    new MutationObserver(scheduleCleanup).observe(document.documentElement, {
      childList: true,
      subtree: true,
    });
    scheduleCleanup();
  }

  window.celiaPalco = {
    estado: () => ({
      modoPalco: ehPalco,
      fundo: params.get("fundo") || (ehPalco ? "escuro" : null),
      canvasPresente: !!document.getElementById("canvas"),
      // prova de que a limpeza aconteceu: no palco, o irmão direto do root
      // que não contém o canvas deve estar com display:none
      escondidos: ehPalco
        ? Array.from(document.querySelectorAll('[style*="display: none"]')).length
        : 0,
    }),
  };

})();
