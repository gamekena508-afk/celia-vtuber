/* Célia Lipsync V2 — adaptive envelope-to-viseme driver for direct Cubism.
 *
 * Owns mouth parameters only. Emotion/motion live elsewhere.
 * TTS already provides normalized volume slices; this layer derives a stable
 * mouth pose plus speech energy for the Motion Director.
 */
(function installCeliaLipSyncV2() {
  "use strict";

  const VERSION = "lipsync-v4.2-20260918-owner-cap";
  const MAX_OPEN = 0.62;
  const BASE_GATE = 0.015;
  const MAX_GATE = 0.050;
  const FORM_LIMIT = 0.36;
  const ATTACK = 0.60;
  const RELEASE = 0.22;
  const STOP_RELEASE = 0.34;

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  let active = null;
  let installedCore = null;
  let installedCubism = null;
  let mouthOpenIndex = -1;
  let mouthFormIndex = -1;
  let currentOpen = 0;
  let currentForm = 0;
  let noiseFloor = 0.008;
  let recentPeak = 0.45;
  let lastEnergyEmitAt = 0;
  let lastFrame = {
    version: VERSION,
    speechId: null,
    raw: 0,
    intensity: 0,
    slope: 0,
    gate: BASE_GATE,
    open: 0,
    form: 0,
    viseme: "SIL",
    running: false,
  };

  function modelCore() {
    try {
      const adapter = typeof window.getLAppAdapter === "function"
        ? window.getLAppAdapter()
        : null;
      const lapp = adapter && typeof adapter.getModel === "function"
        ? adapter.getModel()
        : null;
      const cubism = lapp && lapp._model;
      const core = cubism && typeof cubism.getModel === "function"
        ? cubism.getModel()
        : null;
      return { cubism, core };
    } catch (_) {
      return { cubism: null, core: null };
    }
  }

  function locateParameters(core) {
    const ids = core && core.parameters
      ? Array.from(core.parameters.ids || [])
      : [];
    mouthOpenIndex = ids.indexOf("ParamMouthOpenY");
    mouthFormIndex = ids.indexOf("ParamMouthForm");
    return mouthOpenIndex >= 0 || mouthFormIndex >= 0;
  }

  function setParameter(core, index, value) {
    if (!core || index < 0 || !core.parameters) return;
    const values = core.parameters.values;
    const mins = core.parameters.minimumValues || [];
    const maxs = core.parameters.maximumValues || [];
    values[index] = clamp(value, mins[index] ?? -1, maxs[index] ?? 1);
  }

  function envelopeAt(timeSec) {
    const volumes = active && Array.isArray(active.volumes) ? active.volumes : [];
    if (!volumes.length) return { value: 0, slope: 0 };
    const slice = Math.max(1, Number(active.sliceLengthMs) || 20) / 1000;
    const position = clamp(timeSec / slice, 0, volumes.length - 1);
    const index = Math.floor(position);
    const next = Math.min(volumes.length - 1, index + 1);
    const fraction = position - index;
    const current = Number(volumes[index]) || 0;
    const following = Number(volumes[next]) || 0;
    const value = current * (1 - fraction) + following * fraction;
    const before = Number(volumes[Math.max(0, index - 1)]) || 0;
    return {
      value: clamp(value, 0, 1),
      slope: clamp(following - before, -1, 1),
    };
  }

  function adaptiveGate(raw) {
    // O envelope vem do WAV do TTS, não do microfone: fala baixa não pode ser
    // aprendida como "ruído" e fechar a boca. Só atualize o piso em silêncio
    // realmente baixo e mantenha o gate conservador.
    if (raw < 0.06) {
      noiseFloor += (raw - noiseFloor) * 0.020;
    } else {
      noiseFloor *= 0.997;
    }
    noiseFloor = clamp(noiseFloor, 0.002, 0.022);
    return clamp(BASE_GATE + noiseFloor, BASE_GATE, MAX_GATE);
  }

  function classifyViseme(intensity, slope) {
    if (intensity < 0.035) return "SIL";
    if (slope > 0.10) return "E";
    if (slope < -0.12 && intensity < 0.42) return "M";
    if (intensity > 0.76) return "A";
    if (intensity > 0.48) return "O";
    return "I";
  }

  function visemeForm(viseme, intensity, slope) {
    const forms = {
      SIL: 0.00,
      A: -0.05,
      E: 0.18,
      I: 0.11,
      O: -0.24,
      M: -0.08,
    };
    const base = forms[viseme] ?? 0;
    const articulation = clamp(slope * 0.16, -0.10, 0.10);
    return clamp((base + articulation) * (0.35 + intensity * 0.65), -FORM_LIMIT, FORM_LIMIT);
  }

  function visemeJawScale(viseme) {
    // A forma emocional continua pertencendo ao celia-face. A nuance da fala
    // vem da mandíbula: A/O abrem mais, I/E menos e M fecha rapidamente.
    const scales = {
      SIL: 0.00,
      M: 0.12,
      I: 0.72,
      E: 0.84,
      O: 0.96,
      A: 1.08,
    };
    return scales[viseme] ?? 0.80;
  }

  function emitSpeechEnergy(detail, force = false) {
    if (typeof window.dispatchEvent !== "function" || typeof window.CustomEvent !== "function") return;
    const now = Date.now();
    if (!force && now - lastEnergyEmitAt < 28) return;
    lastEnergyEmitAt = now;
    window.dispatchEvent(new window.CustomEvent("celia-speech-energy", { detail }));
  }

  function applyFrame() {
    const { core } = modelCore();
    if (!core || !core.parameters) return;
    if (core !== installedCore) {
      installedCore = core;
      locateParameters(core);
    }
    if (!active && currentOpen < 0.004 && Math.abs(currentForm) < 0.004) return;

    const sample = active
      ? envelopeAt(Number(active.audio.currentTime) || 0)
      : { value: 0, slope: 0 };
    const gate = adaptiveGate(sample.value);
    const normalized = clamp((sample.value - gate) / Math.max(0.01, 1 - gate), 0, 1);

    recentPeak = Math.max(normalized, recentPeak * 0.985);
    const autoGain = clamp(1.15 / Math.max(0.42, recentPeak), 0.94, 1.42);
    const intensity = active
      ? Math.pow(clamp(normalized * autoGain, 0, 1), 0.74)
      : 0;
    const viseme = classifyViseme(intensity, sample.slope);

    const baseOpen = intensity * (0.52 + intensity * 0.24);
    const voicedFloor = active && viseme !== "SIL" && viseme !== "M" && intensity > 0.07
      ? 0.055 + intensity * 0.035
      : 0;
    const targetOpen = active
      ? Math.min(MAX_OPEN, Math.max(voicedFloor, baseOpen * visemeJawScale(viseme)))
      : 0;
    const targetForm = active
      ? visemeForm(viseme, intensity, sample.slope)
      : 0;

    const closeRate = active && viseme === "M" ? 0.46 : (active ? RELEASE : STOP_RELEASE);
    const openRate = targetOpen > currentOpen
      ? clamp(ATTACK + Math.max(0, sample.slope) * 0.14, ATTACK, 0.76)
      : closeRate;
    const formRate = active ? (targetForm * currentForm < 0 ? 0.42 : 0.30) : STOP_RELEASE;

    currentOpen += (targetOpen - currentOpen) * openRate;
    currentForm += (targetForm - currentForm) * formRate;

    if (!active && currentOpen < 0.004) currentOpen = 0;
    if (!active && Math.abs(currentForm) < 0.004) currentForm = 0;

    setParameter(core, mouthOpenIndex, currentOpen);
    // Expression owns mouth shape; articulation must not erase its emotional base.
    // Opening is the only absolute mouth channel owned by lipsync.

    lastFrame = {
      version: VERSION,
      speechId: active ? active.speechId : null,
      raw: sample.value,
      intensity,
      slope: sample.slope,
      gate,
      noiseFloor,
      open: currentOpen,
      form: currentForm,
      viseme,
      running: Boolean(active),
    };

    emitSpeechEnergy({
      speechId: lastFrame.speechId,
      energy: intensity,
      slope: sample.slope,
      running: Boolean(active),
      viseme,
    });
  }

  function installCoreHook() {
    const { cubism, core } = modelCore();
    if (!cubism || !core || typeof cubism.update !== "function") return false;
    if (cubism === installedCubism && cubism.__celiaLipSyncV2Hook) {
      if (core !== installedCore) {
        installedCore = core;
        locateParameters(core);
      }
      return true;
    }

    installedCubism = cubism;
    installedCore = core;
    locateParameters(core);

    if (!cubism.__celiaLipSyncV2Hook) {
      const originalUpdate = cubism.update.bind(cubism);
      cubism.update = function celiaLipSyncV2Update() {
        // Commit mouth parameters before Cubism calculates drawable vertices.
        // Writing after update loses the pose to next-frame motion restoration.
        applyFrame();
        return originalUpdate(...arguments);
      };
      cubism.__celiaLipSyncV2Hook = VERSION;
    }
    return true;
  }

  function start(event) {
    const detail = event && event.detail;
    if (!detail || !detail.audio || !detail.speechId) return;
    active = {
      audio: detail.audio,
      speechId: String(detail.speechId),
      volumes: Array.isArray(detail.volumes) ? detail.volumes.map(v => clamp(Number(v) || 0, 0, 1)) : [],
      sliceLengthMs: Number(detail.sliceLengthMs) || 20,
    };
    // Cada WAV já chega normalizado. Não carregue um piso aprendido da fala
    // anterior para a primeira sílaba da próxima resposta.
    noiseFloor = 0.006;
    recentPeak = 0.45;
    installCoreHook();
    emitSpeechEnergy({
      speechId: active.speechId,
      energy: 0,
      slope: 0,
      running: true,
      viseme: "SIL",
    }, true);
  }

  function stop(event) {
    const speechId = event && event.detail && String(event.detail.speechId || "");
    if (active && speechId && active.speechId !== speechId) return;
    const stoppedId = active ? active.speechId : speechId || null;
    active = null;
    emitSpeechEnergy({
      speechId: stoppedId,
      energy: 0,
      slope: 0,
      running: false,
      viseme: "SIL",
    }, true);
  }

  window.addEventListener("celia-lipsync-start", start);
  window.addEventListener("celia-lipsync-stop", stop);

  // Keep retrying: a model swap creates a new Cubism instance.
  setInterval(installCoreHook, 500);

  window.celiaLipSync = {
    version: VERSION,
    state: () => ({
      ...lastFrame,
      maxOpen: MAX_OPEN,
      baseGate: BASE_GATE,
      formLimit: FORM_LIMIT,
    }),
    stop,
    install: installCoreHook,
  };
}());
