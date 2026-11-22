/*
 * rms_vad.js — RMS puro substitui Silero VAD
 *
 * O que faz:
 *  1. Bloqueia todos os mic-audio-data do Silero (WebSocket shim).
 *  2. Captura microfone via getUserMedia shim → AudioWorklet.
 *  3. Mantém ring buffer de PREROLL_MS de áudio nativo.
 *  4. Quando RMS > threshold: inicia utterance (com pre-roll).
 *  5. Depois de aberta, preserva áudio contínuo; RMS só decide o endpoint.
 *  6. 480 ms de silêncio vira soft-end; retomada antes do hard-end mantém a mesma fala.
 *  7. Hard-end configurável fecha a fala; limite máximo impede utterance infinita.
 *  8. Emite BroadcastChannel('rms_vad') para VU Meter no dashboard.
 */
(function installRMSVad() {
  "use strict";

  const PREROLL_MS    = 360;
  const TARGET_RATE   = 16000;
  const BLOCK_SIZE    = 1024;
  const SOFT_SILENCE_MS = 480;
  const LEGACY_HARD_SILENCE_MS = 950;
  const DEFAULT_HARD_SILENCE_MS = 650;
  const HARD_SILENCE_KEY = "rms_vad_hard_silence_ms";
  const ENDPOINT_PROFILE_KEY = "rms_vad_endpoint_profile";
  const ENDPOINT_PROFILE = "20261001-lowlatency-v2";
  const storedHardSilence = parseInt(localStorage.getItem(HARD_SILENCE_KEY) ?? "", 10);
  let hardSilenceMs = Number.isFinite(storedHardSilence)
    && storedHardSilence >= SOFT_SILENCE_MS
    && storedHardSilence <= 3000
    ? storedHardSilence : DEFAULT_HARD_SILENCE_MS;
  if (
    localStorage.getItem(ENDPOINT_PROFILE_KEY) !== ENDPOINT_PROFILE
    || storedHardSilence === LEGACY_HARD_SILENCE_MS
  ) {
    if (!Number.isFinite(storedHardSilence) || storedHardSilence === LEGACY_HARD_SILENCE_MS) {
      hardSilenceMs = DEFAULT_HARD_SILENCE_MS;
      localStorage.setItem(HARD_SILENCE_KEY, String(hardSilenceMs));
    }
    localStorage.setItem(ENDPOINT_PROFILE_KEY, ENDPOINT_PROFILE);
  }
  const MAX_UTTERANCE_MS = 10000;
  const TRANSPORT_MS  = 140;
  const ACTIVATION_FRAMES = 2;
  const VAD_SOURCE = "rms_endpoint_v2";
  const CLIENT_HINT = typeof location !== "undefined"
    ? (new URLSearchParams(location.search).get("playback_client") || "browser")
    : "test";
  // Evidência mínima de voz REAL. O pre-roll nunca conta para este piso.
  // Dois frames de ativação a 48 kHz somam só ~42,7 ms; eram suficientes para
  // produzir WAVs de ~403 ms (360 ms de pre-roll + transiente) e alucinações.
  const MIN_VOICED_MS = 180;
  const LEVEL_EMIT_MS = 50;
  const THRESHOLD_KEY = "rms_vad_threshold";
  const LEGACY_THRESHOLD = 0.021735;
  const DEFAULT_THRESHOLD = 0.02238705; // +3% menos sensível que 0.021735
  const VAD_CONFIG = Object.freeze({
    prerollMs: PREROLL_MS,
    blockSize: BLOCK_SIZE,
    softSilenceMs: SOFT_SILENCE_MS,
    maxUtteranceMs: MAX_UTTERANCE_MS,
    transportMs: TRANSPORT_MS,
    activationFrames: ACTIVATION_FRAMES,
    minVoicedMs: MIN_VOICED_MS,
  });

  const storedThreshold = parseFloat(localStorage.getItem(THRESHOLD_KEY) ?? "NaN");
  let threshold = Number.isFinite(storedThreshold) ? storedThreshold : DEFAULT_THRESHOLD;
  if (!Number.isFinite(storedThreshold) || Math.abs(storedThreshold - LEGACY_THRESHOLD) < 1e-9) {
    threshold = DEFAULT_THRESHOLD;
    localStorage.setItem(THRESHOLD_KEY, String(threshold));
  }
  let currentRms    = 0;
  let isVoiceActive = false;
  let nativeRate    = 48000;
  let audioEngine   = "uninitialized";
  let audioContext  = null;
  let audioSetupPromise = null;
  let lastWorkletBlockAt = 0;
  let workletBlockIntervalMs = 0;
  let maxWorkletJitterMs = 0;

  // Ring buffer (áudio nativo, taxa original)
  let ringBuf  = null;
  let ringPos  = 0;
  let ringSize = 0;

  // The browser streams one utterance to the existing websocket. It does not
  // create a second ASR client or a parallel conversation path.
  let utteranceId = null;
  let transportBuf = [];
  let transportSamples = 0;
  let speakingNow  = false;
  let softSilenceTimer = null;
  let hardSilenceTimer = null;
  let maxUtteranceTimer = null;
  let pendingSoftEnd = false;
  let candidateActiveFrames = 0;
  let candidateVoicedNativeSamples = 0;
  let voicedSamples = 0;
  let prerollSamples = 0;
  let utteranceSamples = 0;

  // Mapa ws → origSend (para bypassar o shim ao enviar)
  const wsOrigSends = new Map();

  // BroadcastChannel → dashboard
  const bc = typeof BroadcastChannel !== "undefined"
    ? new BroadcastChannel("rms_vad") : null;
  const traceBc = typeof BroadcastChannel !== "undefined"
    ? new BroadcastChannel("rms_vad_trace") : null;
  const traceHistory = [];
  let lastVoiceAt = 0;
  let utteranceStartedAt = 0;

  function trace(event, detail = {}) {
    const record = {
      event,
      t: performance.now(),
      wall_time: new Date().toISOString(),
      ...detail,
    };
    traceHistory.push(record);
    if (traceHistory.length > 100) traceHistory.shift();
    traceBc?.postMessage(record);
    document.dispatchEvent(new CustomEvent("rms-vad-trace", { detail: record }));
    return record;
  }

  // ── VU Meter ─────────────────────────────────────────────────────────────
  function emitLevel() {
    const capturing = audioContext?.state === "running" && lastWorkletBlockAt > 0
      && performance.now() - lastWorkletBlockAt < 500;
    const payload = { rms: capturing ? currentRms : 0, active: capturing && isVoiceActive, threshold, capturing };
    document.dispatchEvent(new CustomEvent("rms-level", { detail: payload }));
    bc?.postMessage(payload);
  }
  setInterval(emitLevel, LEVEL_EMIT_MS);

  // ── Resample nativeRate → 16 kHz ─────────────────────────────────────────
  function resampleTo16k(src, srcRate) {
    if (srcRate === TARGET_RATE) return src;
    const ratio  = srcRate / TARGET_RATE;
    const outLen = Math.floor(src.length / ratio);
    const out    = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const base = Math.floor(i * ratio);
      const next = Math.min(Math.floor((i + 1) * ratio), src.length);
      let sum = 0;
      for (let j = base; j < next; j++) sum += src[j];
      out[i] = sum / (next - base);
    }
    return out;
  }

  // ── Pre-roll: desembrulha ring buffer ─────────────────────────────────────
  function extractPreroll() {
    if (!ringBuf || ringSize === 0) return new Float32Array(0);
    const filled = Math.min(ringPos, ringSize);
    const raw    = new Float32Array(filled);
    const start  = ringPos >= ringSize ? (ringPos % ringSize) : 0;
    for (let i = 0; i < filled; i++) raw[i] = ringBuf[(start + i) % ringSize];
    return resampleTo16k(raw, nativeRate);
  }

  // ── Envia utterance via WS original (bypassa o shim) ─────────────────────
  function rmsDirectSend(payload) {
    for (const [ws, origSend] of wsOrigSends) {
      if (ws.readyState === WebSocket.OPEN) {
        origSend(JSON.stringify(payload));
        return true;
      }
    }
    console.warn("[rms_vad] Nenhum WS aberto para enviar utterance");
    return false;
  }

  function makeUtteranceId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return `utterance-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function flushTransport() {
    if (!utteranceId || transportSamples === 0) return;
    const combined = new Float32Array(transportSamples);
    let offset = 0;
    for (const chunk of transportBuf) {
      combined.set(chunk, offset);
      offset += chunk.length;
    }
    transportBuf = [];
    transportSamples = 0;
    rmsDirectSend({
      type: "mic-audio-data",
      utterance_id: utteranceId,
      vad_source: VAD_SOURCE,
      client_hint: CLIENT_HINT,
      audio: Array.from(combined),
    });
  }

  function queueTransport(chunk) {
    if (!chunk?.length) return;
    transportBuf.push(chunk);
    transportSamples += chunk.length;
    utteranceSamples += chunk.length;
    if (transportSamples >= Math.round((TRANSPORT_MS / 1000) * TARGET_RATE)) {
      flushTransport();
    }
  }

  function beginUtterance(preroll, confirmedVoicedNativeSamples) {
    utteranceId = makeUtteranceId();
    transportBuf = [];
    transportSamples = 0;
    utteranceSamples = 0;
    prerollSamples = preroll?.length || 0;
    voicedSamples = Math.floor(
      (confirmedVoicedNativeSamples * TARGET_RATE) / nativeRate,
    );
    utteranceStartedAt = performance.now();
    trace("speech_started", {
      utterance_id: utteranceId,
      preroll_ms: PREROLL_MS,
      confirmed_voiced_ms: (confirmedVoicedNativeSamples / nativeRate) * 1000,
    });
    rmsDirectSend({
      type: "speech.started",
      utterance_id: utteranceId,
      vad_source: VAD_SOURCE,
      client_hint: CLIENT_HINT,
    });
    queueTransport(preroll);
    if (maxUtteranceTimer) clearTimeout(maxUtteranceTimer);
    maxUtteranceTimer = setTimeout(
      () => endUtterance("max_duration"),
      MAX_UTTERANCE_MS,
    );
  }

  function clearEndpointTimers() {
    if (softSilenceTimer) clearTimeout(softSilenceTimer);
    if (hardSilenceTimer) clearTimeout(hardSilenceTimer);
    softSilenceTimer = null;
    hardSilenceTimer = null;
    pendingSoftEnd = false;
  }

  function resetMicrophoneCapture() {
    clearEndpointTimers();
    if (maxUtteranceTimer) clearTimeout(maxUtteranceTimer);
    maxUtteranceTimer = null;
    utteranceId = null;
    transportBuf = [];
    transportSamples = utteranceSamples = voicedSamples = prerollSamples = 0;
    candidateActiveFrames = candidateVoicedNativeSamples = 0;
    speakingNow = isVoiceActive = false;
    currentRms = ringPos = 0;
    lastVoiceAt = 0;
    utteranceStartedAt = 0;
    if (ringBuf) ringBuf.fill(0);
  }

  function armEndpointTimers() {
    clearEndpointTimers();
    softSilenceTimer = setTimeout(() => {
      pendingSoftEnd = true;
      softSilenceTimer = null;
    }, SOFT_SILENCE_MS);
    hardSilenceTimer = setTimeout(
      () => endUtterance("hard_silence"),
      hardSilenceMs,
    );
  }

  function endUtterance(endpointReason = "hard_silence") {
    const endpointAt = performance.now();
    const endpointDelayMs = lastVoiceAt > 0 ? Math.max(0, endpointAt - lastVoiceAt) : null;
    const utteranceOpenMs = utteranceStartedAt > 0 ? Math.max(0, endpointAt - utteranceStartedAt) : null;
    speakingNow  = false;
    candidateActiveFrames = 0;
    candidateVoicedNativeSamples = 0;
    clearEndpointTimers();
    if (maxUtteranceTimer) clearTimeout(maxUtteranceTimer);
    maxUtteranceTimer = null;
    if (!utteranceId) return;
    flushTransport();
    rmsDirectSend({
      type: "mic-audio-end",
      utterance_id: utteranceId,
      vad_source: VAD_SOURCE,
      client_hint: CLIENT_HINT,
      endpoint_reason: endpointReason,
      soft_silence_ms: SOFT_SILENCE_MS,
      hard_silence_ms: hardSilenceMs,
      voiced_samples: voicedSamples,
      preroll_samples: prerollSamples,
      captured_samples: utteranceSamples,
      endpoint_delay_ms: endpointDelayMs,
      client_utterance_open_ms: utteranceOpenMs,
      audio_engine: audioEngine,
    });
    trace("speech_ended", {
      utterance_id: utteranceId,
      endpoint_reason: endpointReason,
      endpoint_delay_ms: endpointDelayMs,
      utterance_open_ms: utteranceOpenMs,
    });
    utteranceId = null;
    utteranceStartedAt = 0;
    voicedSamples = 0;
    prerollSamples = 0;
    utteranceSamples = 0;
  }

  // ── WebSocket shim — BLOQUEIA mic-audio-data do Silero ───────────────────
  const OrigWS = window.WebSocket;

  function PatchedWS(...args) {
    const ws       = new OrigWS(...args);
    const origSend = ws.send.bind(ws);

    wsOrigSends.set(ws, origSend);
    ws.addEventListener("close", () => wsOrigSends.delete(ws));

    ws.send = function (data) {
      if (typeof data === "string") {
        try {
          const msg = JSON.parse(data);
          if (msg.type === "mic-audio-data" || msg.type === "mic-audio-end") {
            // Silero tentou enviar → descarta
            return;
          }
        } catch (_) {}
      }
      return origSend(data);
    };

    return ws;
  }

  PatchedWS.prototype = OrigWS.prototype;
  Object.defineProperty(PatchedWS, "prototype", { value: OrigWS.prototype });
  for (const k of Object.keys(OrigWS)) PatchedWS[k] = OrigWS[k];
  PatchedWS.CONNECTING = OrigWS.CONNECTING;
  PatchedWS.OPEN       = OrigWS.OPEN;
  PatchedWS.CLOSING    = OrigWS.CLOSING;
  PatchedWS.CLOSED     = OrigWS.CLOSED;
  window.WebSocket     = PatchedWS;

  // ── getUserMedia shim ─────────────────────────────────────────────────────
  if (!navigator.mediaDevices?.getUserMedia) {
    console.warn("[rms_vad] getUserMedia indisponível — abortando.");
    return;
  }

  const origGUM = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async function (constraints) {
    let stream;
    try {
      stream = await origGUM(constraints);
    } catch (err) {
      // Alguns navegadores deixam uma seleção antiga de microfone persistida
      // ou rejeitam as restrições opcionais do VAD com NotFoundError. Tente
      // novamente sem o mapeamento antigo antes de propagar o erro.
      if (err?.name !== "NotFoundError" || !constraints?.audio) throw err;
      console.warn("[rms_vad] dispositivo/restrição antiga não encontrada; tentando entrada padrão");
      try {
        stream = await origGUM({ audio: true });
      } catch (fallbackErr) {
        const inputs = (await navigator.mediaDevices.enumerateDevices())
          .filter((device) => device.kind === "audioinput" && device.deviceId);
        if (inputs.length === 0) throw fallbackErr;
        stream = await origGUM({
          audio: { deviceId: { exact: inputs[0].deviceId } },
        });
      }
    }
    if (constraints && constraints.audio && !audioSetupPromise) {
      audioSetupPromise = setupAudioAnalysis(stream).catch((err) => {
        audioSetupPromise = null;
        console.warn("[rms_vad] setupAudioAnalysis falhou:", err);
        throw err;
      });
    }
    if (constraints && constraints.audio && audioSetupPromise) {
      await audioSetupPromise;
    }
    return stream;
  };

  // ── Análise de áudio + VAD RMS ────────────────────────────────────────────
  function handleAudioBlock(chunk, rms) {
    if (!(chunk instanceof Float32Array) || chunk.length === 0) return;
    if (window.__micToggle && !window.__micToggle.state()) {
      resetMicrophoneCapture();
      return;
    }

    const now = performance.now();
    if (lastWorkletBlockAt > 0) {
      workletBlockIntervalMs = now - lastWorkletBlockAt;
      const expected = (BLOCK_SIZE / nativeRate) * 1000;
      maxWorkletJitterMs = Math.max(
        maxWorkletJitterMs,
        Math.abs(workletBlockIntervalMs - expected),
      );
    }
    lastWorkletBlockAt = now;

    for (let i = 0; i < chunk.length; i++) {
      ringBuf[ringPos % ringSize] = chunk[i];
      ringPos++;
    }

    currentRms = Number.isFinite(rms) ? rms : 0;
    isVoiceActive = currentRms > threshold;

    if (isVoiceActive) {
      lastVoiceAt = now;
      if (!speakingNow) {
        candidateActiveFrames += 1;
        candidateVoicedNativeSamples += chunk.length;
        if (candidateActiveFrames < ACTIVATION_FRAMES) return;

        speakingNow = true;
        beginUtterance(extractPreroll(), candidateVoicedNativeSamples);
        candidateActiveFrames = 0;
        candidateVoicedNativeSamples = 0;
      } else {
        const chunk16k = resampleTo16k(chunk, nativeRate);
        voicedSamples += chunk16k.length;
        queueTransport(chunk16k);
      }

      // 480 ms continua sendo o soft endpoint; o hard endpoint foi reduzido
      // para 650 ms para diminuir a sensação de microfone "arrastado".
      armEndpointTimers();
    } else if (speakingNow) {
      // Depois de abrir a utterance, preserva silêncio e pausas no áudio.
      queueTransport(resampleTo16k(chunk, nativeRate));
    } else {
      candidateActiveFrames = 0;
      candidateVoicedNativeSamples = 0;
    }
  }

  async function setupAudioAnalysis(stream) {
    const ctx = new AudioContext({ latencyHint: "interactive" });
    nativeRate = ctx.sampleRate;
    ringSize = Math.round((PREROLL_MS / 1000) * nativeRate);
    ringBuf = new Float32Array(ringSize);
    ringPos = 0;

    if (!ctx.audioWorklet || typeof AudioWorkletNode === "undefined") {
      throw new Error("AudioWorklet indisponível; não ativando o caminho antigo de alta latência");
    }

    await ctx.audioWorklet.addModule("./rms-vad-worklet.js?v=20261001b");
    const src = ctx.createMediaStreamSource(stream);
    const proc = new AudioWorkletNode(ctx, "rms-vad-processor", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      processorOptions: { blockSize: BLOCK_SIZE },
    });
    const muteGain = ctx.createGain();
    muteGain.gain.value = 0;

    proc.port.onmessage = (event) => {
      if (event.data?.type !== "audio-block") return;
      handleAudioBlock(event.data.chunk, Number(event.data.rms));
    };

    src.connect(proc);
    proc.connect(muteGain);
    muteGain.connect(ctx.destination);
    // Public microphone audio belongs only to mobile/studio.js -> audioDest.
    // Keep the zero-gain worklet destination alive for ASR, without local monitoring.
    if (ctx.state === "suspended") await ctx.resume();

    audioContext = ctx;
    audioEngine = "audio-worklet";
    trace("audio_engine_ready", {
      engine: audioEngine,
      sample_rate: nativeRate,
      block_size: BLOCK_SIZE,
    });

    console.log(
      `[rms_vad] RMS puro ativo via AudioWorklet @ ${nativeRate} Hz | Silero bloqueado` +
      ` | bloco ${BLOCK_SIZE} | pre-roll ${PREROLL_MS}ms` +
      ` | soft/hard ${SOFT_SILENCE_MS}/${hardSilenceMs}ms` +
      ` | max ${MAX_UTTERANCE_MS}ms | confirmação ${ACTIVATION_FRAMES} frames` +
      ` | threshold=${threshold.toFixed(4)}`
    );
  }

  // ── API pública ───────────────────────────────────────────────────────────
  window.__rmsVad = {
    getLevel:     () => currentRms,
    isActive:     () => isVoiceActive,
    isSpeaking:   () => speakingNow,
    getThreshold: () => threshold,
    setThreshold: (v) => {
      threshold = parseFloat(v);
      localStorage.setItem(THRESHOLD_KEY, String(threshold));
      console.log(`[rms_vad] threshold → ${threshold.toFixed(4)}`);
    },
    resetMicrophoneCapture,
    getConfig: () => ({ ...VAD_CONFIG, hardSilenceMs, targetRate: TARGET_RATE }),
    getDiagnostics: () => ({
      ...VAD_CONFIG,
      hardSilenceMs,
      threshold,
      nativeRate,
      ringSize,
      samplesWritten: ringPos,
      speakingNow,
      pendingSoftEnd,
      candidateActiveFrames,
      candidateVoicedNativeSamples,
      voicedSamples,
      prerollSamples,
      utteranceSamples,
      transportSamples,
      audioEngine,
      audioContextState: audioContext?.state || null,
      workletBlockIntervalMs,
      maxWorkletJitterMs,
      lastVoiceAt,
      lastTrace: traceHistory.at(-1) || null,
    }),
    getTrace: () => traceHistory.slice(),
    setHardSilenceMs: (value) => {
      const parsed = Math.round(Number(value));
      if (!Number.isFinite(parsed) || parsed < SOFT_SILENCE_MS || parsed > 3000) {
        throw new RangeError("hardSilenceMs deve ficar entre " + SOFT_SILENCE_MS + " e 3000 ms");
      }
      hardSilenceMs = parsed;
      localStorage.setItem(HARD_SILENCE_KEY, String(parsed));
      console.log("[rms_vad] hard silence → " + hardSilenceMs + "ms");
    },
    getPreroll: () => ({ prerollMs: PREROLL_MS, ringSize, samplesWritten: ringPos, nativeRate }),
  };

  // Canal reverso do dashboard
  if (typeof BroadcastChannel !== "undefined") {
    const ctrl = new BroadcastChannel("rms_vad_ctrl");
    ctrl.onmessage = (e) => {
      if (e.data?.cmd === "setThreshold") window.__rmsVad.setThreshold(e.data.value);
      if (e.data?.cmd === "setHardSilenceMs") window.__rmsVad.setHardSilenceMs(e.data.value);
    };
  }

  console.log("[rms_vad] Instalado. Silero bloqueado. RMS controla envio. threshold=" + threshold.toFixed(4));
})();
