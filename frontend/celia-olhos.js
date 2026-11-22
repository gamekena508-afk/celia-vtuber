/* Own Live2D eyelid closure and recover safely from stuck-eye states. */
(function instalarOlhosCelia() {
  "use strict";

  const IDS = ["ParamEyeLOpen", "ParamEyeROpen"];
  const FECHADO = 0.1;
  const MAX_FECHADO_MS = 350;
  const BLINK_INTERVALS = Object.freeze({
    neutral: 5.6, happy: 5.0, funny: 4.8, teasing: 5.6,
    in_love: 6.2, sad: 6.8, angry: 7.2, surprised: 4.8,
    confused: 5.8, scared: 5.2, holding_back_laughter: 5.6, passionate: 5.6,
  });
  let instalado = null;
  let emocaoAtual = "neutral";
  let blinkUntil = 0;
  let blinkReason = null;
  let blinkWasActive = false;

  function pegarCore() {
    try {
      const adapter = typeof window.getLAppAdapter === "function" ? window.getLAppAdapter() : null;
      const lapp = adapter && typeof adapter.getModel === "function" ? adapter.getModel() : null;
      const cubism = lapp && lapp._model;
      const core = cubism && typeof cubism.getModel === "function" ? cubism.getModel() : null;
      return core && core.parameters ? { lapp, cubism, core } : null;
    } catch (_) { return null; }
  }

  function intervaloPiscadaNativa() {
    return BLINK_INTERVALS[emocaoAtual] || BLINK_INTERVALS.neutral;
  }

  function configurarPiscadaNativa(lapp) {
    const eyeBlink = lapp && lapp._eyeBlink;
    if (!eyeBlink || typeof eyeBlink.setBlinkingInterval !== "function") return null;
    const intervalo = intervaloPiscadaNativa();
    eyeBlink.setBlinkingInterval(intervalo);
    return intervalo;
  }

  function receberEmocao(event) {
    const detail = event && event.detail ? event.detail : {};
    const raw = String(detail.emotion || detail.emocao || "").trim().toLowerCase();
    const alias = raw === "trol_idle" || raw === "trol_speaking" ? "teasing" : raw;
    if (!BLINK_INTERVALS[alias]) return;
    emocaoAtual = alias;
    if (instalado) instalado.nativeBlinkInterval = configurarPiscadaNativa(instalado.lapp);
  }

  function pedirPiscada(event) {
    const detail = event && event.detail ? event.detail : {};
    const duration = Math.max(70, Math.min(170, Number(detail.durationMs) || 110));
    blinkUntil = Math.max(blinkUntil, performance.now() + duration);
    blinkReason = String(detail.reason || "motion");
  }

  if (typeof window.addEventListener === "function") {
    window.addEventListener("celia-blink-request", pedirPiscada);
    window.addEventListener("celia-emotion", receberEmocao);
  }

  function tentarInstalar() {
    const atual = pegarCore();
    if (!atual || (instalado && instalado.core === atual.core)) return;
    const ids = Array.from(atual.core.parameters.ids);
    const indices = IDS.map((id) => ids.indexOf(id));
    if (indices.some((index) => index < 0)) return;

    const atualizarNativo = atual.cubism.update.bind(atual.cubism);
    let fechadoDesde = null;
    let aberturaForcada = false;

    atual.cubism.update = function celiaOlhosUpdate() {
      const valores = atual.core.parameters.values;
      const agora = performance.now();
      const blinkAtivo = agora < blinkUntil;

      if (blinkAtivo) {
        for (const index of indices) valores[index] = 0.04;
        blinkWasActive = true;
        fechadoDesde = agora;
        aberturaForcada = false;
      } else {
        if (blinkWasActive) {
          if (indices.every((index) => valores[index] <= FECHADO)) {
            for (const index of indices) valores[index] = 1;
          }
          blinkWasActive = false;
          blinkReason = null;
          fechadoDesde = null;
        }
        const abertos = indices.map((index) => valores[index]);
        const todosFechados = abertos.every((valor) => valor <= FECHADO);
        if (aberturaForcada) {
          if (abertos.some((valor) => valor > FECHADO)) {
            aberturaForcada = false;
            fechadoDesde = null;
          } else {
            for (const index of indices) valores[index] = 1;
          }
        } else if (todosFechados) {
          fechadoDesde = fechadoDesde === null ? agora : fechadoDesde;
          if (agora - fechadoDesde >= MAX_FECHADO_MS) {
            aberturaForcada = true;
            for (const index of indices) valores[index] = 1;
          }
        } else {
          fechadoDesde = null;
        }
      }
      return atualizarNativo();
    };

    instalado = {
      lapp: atual.lapp,
      core: atual.core,
      indices,
      nativeBlinkInterval: configurarPiscadaNativa(atual.lapp),
      fechamentoForcado: () => aberturaForcada,
    };
  }

  setInterval(tentarInstalar, 500);

  window.celiaOlhos = {
    requestBlink: (reason, durationMs) => pedirPiscada({ detail: { reason, durationMs } }),
    estado: () => {
      if (!instalado) return { instalado: false, blinkAtivo: performance.now() < blinkUntil, blinkReason, emocao: emocaoAtual, nativeBlinkInterval: intervaloPiscadaNativa() };
      const valores = instalado.indices.map((index) => instalado.core.parameters.values[index]);
      return {
        instalado: true, valores, fechamentoForcado: instalado.fechamentoForcado(),
        blinkAtivo: performance.now() < blinkUntil, blinkReason,
        emocao: emocaoAtual, nativeBlinkInterval: instalado.nativeBlinkInterval,
      };
    },
  };
})();
