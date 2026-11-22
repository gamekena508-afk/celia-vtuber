(function installExternalPlaybackBridge() {
  "use strict";

  const NativeWebSocket = window.WebSocket;
  const NativeAudio = window.Audio;
  let playerSequence = 0;
  function nextPlayerId() {
    playerSequence += 1;
    return `player-${Date.now()}-${playerSequence}`;
  }
  const pendingByDataUrl = new Map();
  const pendingBySpeechId = new Map();
  const activeBySpeechId = new Map();
  const cancelledSpeechIds = new Set();
  const playedAudioFingerprints = new Set();
  const AUDIO_DEDUPE_TTL_MS = 5 * 60 * 1000;
  const HEARTBEAT_INTERVAL_MS = 10 * 1000;
  const HEARTBEAT_TIMEOUT_MS = 30 * 1000;

  function tagPlaybackClient(url) {
    try {
      const raw = String(url || "");
      const baseHref = window.location && window.location.href
        ? window.location.href
        : "http://127.0.0.1/";
      const parsed = new URL(raw, baseHref);
      if (parsed.pathname !== "/client-ws") return url;
      const pageSearch = window.location && typeof window.location.search === "string"
        ? new URLSearchParams(window.location.search)
        : new URLSearchParams();
      parsed.searchParams.set(
        "playback_client",
        // celiaScene=1: a cena da bancada do modo Live; com o Live ligado só ela toca a voz.
        pageSearch.get("pet") === "1" ? "pet" : pageSearch.get("celiaScene") === "1" ? "scene" : "browser",
      );
      return parsed.toString();
    } catch (_) {
      return url;
    }
  }

  // Speech identity, not VAD/UI state or waveform identity, owns cancellation.
  const speechStates = new Map();
  const traceEvents = [];
  let uiState = "loading";

  // ── Faixa única de voz (docs/FILA_DE_FALA.md) ───────────────────────────
  // Uma fala só começa quando nenhuma outra está audível; quem chega enquanto
  // isso espera, na ordem de chegada. O bundle tem a sua própria fila, mas
  // `audioTaskQueue.clearQueue()` (chamado em todo conversation-chain-start)
  // zera `running` com o áudio anterior ainda tocando: sem esta faixa a fala
  // seguinte toca por cima (medido em 25/09: pico de 2 e 3 vozes).
  const config = {
    claimTimeoutMs: 5000, // recebeu a voz e não tocou: solta a faixa
    humanHoldMs: 6000,    // humano em voo: quanto esperar pelo turno dele
  };
  const laneWaiters = [];  // {id, resolve, deferred} em ordem de chegada
  const ownerWaiters = []; // o dono voltando a pedir passagem no play()
  let laneOwner = "";      // speech_id com a voz: do "liberado" até o terminal
  let laneClaimTimer = null;
  let humanHoldUntil = 0;
  let humanHoldTimer = null;
  function unrefTimer(timer) {
    if (timer && typeof timer.unref === "function") timer.unref();
    return timer;
  }
  function trace(event, speechId, reason = "") {
    const row = {event, speech_id: speechId || "", reason, timestamp: new Date().toISOString()};
    traceEvents.push(row);
    if (traceEvents.length > 2000) traceEvents.shift();
    console.debug("[audio-lifecycle]", JSON.stringify(row));
    const pending = pendingBySpeechId.get(speechId) || activeBySpeechId.get(speechId);
    try { if (pending) sendNative(pending.socket, {type: "audio-lifecycle", ...row}); } catch (_) {}
  }
  function isCancelled(id) { return Boolean(id && cancelledSpeechIds.has(id)); }
  function setState(state) { uiState = state; pumpLane(); }
  function queueSpeech(id) {
    if (!id) return true; // Legacy text-only payloads have no speech identity.
    if (isCancelled(id)) { trace("AUDIO_DROPPED", id, "explicitly_cancelled"); return false; }
    if (speechStates.has(id)) { trace("AUDIO_DROPPED", id, "duplicate_speech_event"); return false; }
    speechStates.set(id, "queued");
    const cleanup = () => {
      if (["queued", "started"].includes(speechStates.get(id))) { setTimeout(cleanup, AUDIO_DEDUPE_TTL_MS); return; }
      speechStates.delete(id); pendingBySpeechId.delete(id);
    };
    setTimeout(cleanup, AUDIO_DEDUPE_TTL_MS);
    trace("AUDIO_QUEUED", id);
    return true;
  }
  // Por que a fala ainda não pode começar ("" = pode).
  function holdReason() {
    if (uiState === "listening") return "listening";
    if (humanHoldUntil > Date.now()) return "human_in_flight";
    return "";
  }
  function deferIfHeld(waiter) {
    const reason = holdReason();
    if (!reason) return false;
    if (waiter.deferred !== reason) { waiter.deferred = reason; trace("AUDIO_DEFERRED", waiter.id, reason); }
    armHoldWake();
    return true;
  }
  function armHoldWake() {
    clearTimeout(humanHoldTimer);
    const ms = humanHoldUntil - Date.now();
    if (ms <= 0) return;
    humanHoldTimer = unrefTimer(setTimeout(() => { humanHoldTimer = null; pumpLane(); }, ms + 5));
  }
  function armClaimTimer(id) {
    clearTimeout(laneClaimTimer);
    laneClaimTimer = unrefTimer(setTimeout(() => {
      if (laneOwner !== id || speechStates.get(id) === "started") return;
      if (ownerWaiters.some(waiter => waiter.id === id)) { armClaimTimer(id); return; }   // segurado de propósito
      cancelPlayback(id, "lane_claim_timeout", true);
    }, config.claimTimeoutMs));
  }
  function grantLane(id) {
    laneOwner = id;
    armClaimTimer(id);
  }
  function releaseLane(id) {
    if (!id || laneOwner !== id) return;
    laneOwner = "";
    clearTimeout(laneClaimTimer);
    pumpLane();
  }
  // Único lugar que entrega a voz: o dono espera só o humano; a fila espera o dono.
  function pumpLane() {
    while (ownerWaiters.length) {
      if (isCancelled(ownerWaiters[0].id)) { ownerWaiters.shift().resolve(false); continue; }
      if (deferIfHeld(ownerWaiters[0])) return;
      ownerWaiters.shift().resolve(true);
    }
    while (!laneOwner && laneWaiters.length) {
      const head = laneWaiters[0];
      if (isCancelled(head.id)) { laneWaiters.shift(); head.resolve(false); continue; }
      if (deferIfHeld(head)) return;
      laneWaiters.shift();
      grantLane(head.id);
      head.resolve(true);
    }
  }
  function waitForPlayable(id) {
    return new Promise(resolve => {
      const waiter = {id, resolve, deferred: ""};
      if (laneOwner === id && speechStates.get(id) === "started") { resolve(true); return; }   // já toca
      if (laneOwner === id) ownerWaiters.push(waiter);   // é dono, falta o humano calar
      else {
        if (laneOwner && !isCancelled(id)) { waiter.deferred = "lane_busy"; trace("AUDIO_DEFERRED", id, "lane_busy"); }
        laneWaiters.push(waiter);
      }
      pumpLane();
    });
  }
  // Turno novo aceito: o que ainda não começou é de um turno velho. O bundle o
  // apagaria em silêncio (clearQueue); aqui vira cancelamento com motivo e o
  // servidor é avisado. Quem já toca termina.
  function retireUnstarted(reason) {
    for (const [id, state] of [...speechStates]) {
      if (state === "queued") cancelPlayback(id, reason, true);
    }
  }
  function holdForHuman(payload) {
    if (payload.type === "speech.started") {
      const profile = String(payload.profile || "");
      if (!profile || profile === "roteiro") return;    // servidor antigo ou gravação: nunca segura
    } else if (humanHoldUntil <= Date.now()) {
      return;                                           // parcial só prolonga uma espera já aberta
    }
    humanHoldUntil = Date.now() + config.humanHoldMs;
    armHoldWake();
  }
  function cancelAll() {
    const ids = new Set([...pendingBySpeechId.keys(), ...activeBySpeechId.keys(),
      ...[...speechStates].filter(([, state]) => state === "queued").map(([id]) => id)]);
    for (const id of ids) cancelPlayback(id);
  }
  function snapshot() {
    return {owner: laneOwner, waiters: [...ownerWaiters, ...laneWaiters].map(w => w.id), hold: holdReason(),
      holdMsLeft: Math.max(0, humanHoldUntil - Date.now()), states: Object.fromEntries(speechStates)};
  }
  window.celiaPlayback = {setState, queueSpeech, waitForPlayable, isCancelled, cancelAll,
    trace, events: traceEvents, getState: () => uiState, snapshot, config};

  function fingerprint(dataUrl) {
    let hash = 2166136261;
    for (let index = 0; index < dataUrl.length; index += 1) {
      hash ^= dataUrl.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return `${dataUrl.length}:${hash >>> 0}`;
  }

  function rememberPlayback(dataUrl) {
    const audioFingerprint = fingerprint(dataUrl);
    if (playedAudioFingerprints.has(audioFingerprint)) {
      return false;
    }

    playedAudioFingerprints.add(audioFingerprint);
    const timer = setTimeout(() => {
      playedAudioFingerprints.delete(audioFingerprint);
    }, AUDIO_DEDUPE_TTL_MS);
    if (timer && typeof timer.unref === "function") {
      timer.unref();
    }
    return true;
  }

  function emitSyntheticEnded(audio) {
    if (typeof audio.dispatchEvent === "function" && typeof window.Event === "function") {
      audio.dispatchEvent(new window.Event("ended"));
      return;
    }
    if (typeof audio.emit === "function") {
      audio.emit("ended");
    }
  }

  function createSilentDuplicateAudio(src) {
    const audio = new NativeAudio();
    audio.src = src;
    let completed = false;
    audio.play = () => {
      if (!completed) {
        completed = true;
        queueMicrotask(() => emitSyntheticEnded(audio));
      }
      return Promise.resolve();
    };
    return audio;
  }

  function releaseQueueOnPlaybackFailure(audio, onFailure, options = {}) {
    const listenForMediaError = options.listenForMediaError !== false;
    let released = false;
    const releaseOnce = () => {
      if (released) {
        return;
      }
      released = true;
      if (onFailure) {
        onFailure();
      }
      queueMicrotask(() => emitSyntheticEnded(audio));
    };

    if (listenForMediaError) {
      audio.addEventListener("error", releaseOnce, { once: true });
    }
    const nativePlay = audio.play.bind(audio);
    audio.play = (...args) => {
      const playResult = nativePlay(...args);
      if (playResult && typeof playResult.catch === "function") {
        playResult.catch(releaseOnce);
      }
      return playResult;
    };
  }

  function observeIncomingMessage(socket, event) {
    if (typeof event.data !== "string") {
      return;
    }

    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (_error) {
      return;
    }

    if (payload.type === "server-ping") {
      sendHeartbeat(socket);
      return;
    }

    if (payload.type === "playback.cancel") {
      cancelPlayback(payload.speech_id);
      return;
    }

    // Humano em voo: o servidor rebroadcasta o início da fala dele (com o perfil)
    // e parciais de ASR enquanto ele fala.
    if (payload.type === "speech.started" || payload.type === "asr-partial") {
      holdForHuman(payload);
      return;
    }

    // Turno aceito (o bundle vai chamar clearQueue logo em seguida).
    if (payload.type === "control" && payload.text === "conversation-chain-start") {
      humanHoldUntil = 0;
      retireUnstarted("new_turn");
      pumpLane();
      return;
    }

    if (
      payload.type !== "audio" ||
      !payload.speech_id ||
      !payload.audio
    ) {
      return;
    }

    const dataUrl = `data:audio/wav;base64,${payload.audio}`;
    const pending = {
      socket,
      speechId: payload.speech_id,
      turnId: String(payload.turn_id || ""),
      phraseIndex: Number.isFinite(Number(payload.phrase_index)) ? Number(payload.phrase_index) : null,
      external: payload.external_playback === true,
      volumes: Array.isArray(payload.volumes) ? payload.volumes : [],
      sliceLengthMs: Number(payload.slice_length) || 20,
    };
    pendingByDataUrl.set(dataUrl, pending);
    pendingBySpeechId.set(payload.speech_id, pending);
    trace("AUDIO_RECEIVED", payload.speech_id);
  }

  function sendPlaybackComplete(pending, status) {
    const socket = pending.socket;
    if (!socket || socket.readyState !== NativeWebSocket.OPEN) {
      return;
    }

    socket.send(
      JSON.stringify({
        type: "external-playback-complete",
        speech_id: pending.speechId,
        status,
      }),
    );
  }

  function sendPlaybackCancelled(pending) {
    const socket = pending.socket;
    if (!socket || socket.readyState !== NativeWebSocket.OPEN) {
      return;
    }
    socket.send(JSON.stringify({
      type: "frontend-playback-cancelled",
      speech_id: pending.speechId,
      player_id: pending.playerId || "",
    }));
  }

  function sendNative(socket, payload) {
    if (!socket || socket.readyState !== NativeWebSocket.OPEN) return false;
    NativeWebSocket.prototype.send.call(socket, JSON.stringify(payload));
    return true;
  }

  function armPlaybackStartedAck(audio, pending, playback) {
    const nativePlay = audio.play.bind(audio);
    let started = false;
    let ackSent = false;
    audio.play = (...args) => {
      const result = waitForPlayable(pending.speechId).then(allowed => {
        if (!allowed) throw new DOMException("Speech cancelled", "AbortError");
        return nativePlay(...args);
      });
      if (result && typeof result.then === "function") {
        result.then(() => {
          // Real playback is the single clock source for mouth + subtitles.
          // Constructing an Audio object is not proof that sound started.
          if (!started) {
            if (isCancelled(pending.speechId)) return;
            started = true;
            speechStates.set(pending.speechId, "started");
            trace("AUDIO_STARTED", pending.speechId);
            playback.started = true;
            emitLipSyncEvent("celia-lipsync-start", {
              audio,
              speechId: pending.speechId,
              playerId: playback.playerId,
              volumes: pending.volumes || [],
              sliceLengthMs: pending.sliceLengthMs || 20,
            });
          }
          if (ackSent || pending.socket?.readyState !== NativeWebSocket.OPEN) return;
          ackSent = true;
          // Bypass ExternalPlaybackWebSocket.send() deliberately: app bundles
          // built before this bridge used to emit this ACK from canplaythrough,
          // before play() actually succeeded. Only this post-play promise path
          // is allowed to put the correlated ACK on the wire.
          sendNative(pending.socket, {
            type: "audio-playback-started",
            speech_id: pending.speechId,
            player_id: playback.playerId,
            turn_id: pending.turnId || "",
            phrase_index: pending.phraseIndex,
          });
        }).catch(() => {});
      }
      return result;
    };
  }

  function emitLipSyncEvent(type, detail) {
    if (typeof window.CustomEvent !== "function") return;
    window.dispatchEvent(new window.CustomEvent(type, { detail }));
  }

  // `notifyIfIdle`: cancelamento decidido AQUI (turno novo) de fala que nunca
  // tocou; o servidor não sabe, então recebe o mesmo aviso de uma fala cortada.
  function cancelPlayback(speechId, reason = "explicit_cancel", notifyIfIdle = false) {
    if (!speechId) return;
    if (cancelledSpeechIds.has(speechId)) return;
    cancelledSpeechIds.add(speechId);
    speechStates.set(speechId, "cancelled");
    trace("AUDIO_CANCELLED", speechId, reason);
    setTimeout(() => cancelledSpeechIds.delete(speechId), AUDIO_DEDUPE_TTL_MS);
    for (const [dataUrl, pending] of pendingByDataUrl.entries()) {
      if (pending.speechId === speechId) pendingByDataUrl.delete(dataUrl);
    }
    for (const list of [laneWaiters, ownerWaiters]) {
      for (let index = list.length - 1; index >= 0; index -= 1) {
        if (list[index].id === speechId) list.splice(index, 1)[0].resolve(false);
      }
    }
    releaseLane(speechId);
    pumpLane();
    const active = activeBySpeechId.get(speechId);
    if (!active) {
      const idle = pendingBySpeechId.get(speechId);
      if (notifyIfIdle && idle) sendPlaybackCancelled(idle);
      return;
    }
    activeBySpeechId.delete(speechId);
    emitLipSyncEvent("celia-lipsync-stop", { speechId });
    try {
      active.audio.pause();
      active.audio.currentTime = 0;
      active.audio.removeAttribute("src");
      active.audio.load();
    } catch (_error) {
      // The cancellation acknowledgement still retires stale server state.
    }
    sendPlaybackCancelled(active);
    queueMicrotask(() => emitSyntheticEnded(active.audio));
  }

  function stopSocketLiveness(socket) {
    if (!socket.__externalPlaybackHeartbeatTimer) {
      return;
    }

    clearInterval(socket.__externalPlaybackHeartbeatTimer);
    socket.__externalPlaybackHeartbeatTimer = null;
  }

  function closeStaleSocket(socket) {
    stopSocketLiveness(socket);
    if (typeof socket.close === "function") {
      socket.close();
    }
  }

  function sendHeartbeat(socket) {
    if (socket.readyState !== NativeWebSocket.OPEN) {
      stopSocketLiveness(socket);
      return;
    }

    if (Date.now() - socket.__externalPlaybackLastAliveAt >= HEARTBEAT_TIMEOUT_MS) {
      closeStaleSocket(socket);
      return;
    }

    try {
      socket.send(JSON.stringify({ type: "heartbeat" }));
    } catch (_error) {
      closeStaleSocket(socket);
    }
  }

  function startSocketLiveness(socket) {
    if (socket.__externalPlaybackHeartbeatTimer) {
      return;
    }

    socket.__externalPlaybackLastAliveAt = Date.now();
    socket.__externalPlaybackHeartbeatTimer = setInterval(
      () => sendHeartbeat(socket),
      HEARTBEAT_INTERVAL_MS,
    );
    if (
      socket.__externalPlaybackHeartbeatTimer &&
      typeof socket.__externalPlaybackHeartbeatTimer.unref === "function"
    ) {
      socket.__externalPlaybackHeartbeatTimer.unref();
    }
    sendHeartbeat(socket);
  }

  class ExternalPlaybackWebSocket extends NativeWebSocket {
    constructor(...args) {
      if (args.length && typeof args[0] === "string") {
        args[0] = tagPlaybackClient(args[0]);
      }
      super(...args);
      this.__externalPlaybackHeartbeatTimer = null;
      this.__externalPlaybackLastAliveAt = Date.now();
      this.__prematurePlaybackAcksSuppressed = 0;
      this.addEventListener("open", () => {
        startSocketLiveness(this);
      });
      this.addEventListener("message", (event) => {
        this.__externalPlaybackLastAliveAt = Date.now();
        observeIncomingMessage(this, event);
      });
      this.addEventListener("close", () => {
        stopSocketLiveness(this);
      });
    }

    send(payload) {
      if (typeof payload === "string") {
        try {
          const message = JSON.parse(payload);
          // Queue-level completion remains for the conversation waiter. It must
          // not masquerade as completion of a particular media instance.
          if (message?.type === "audio-playback-started" && message.speech_id) {
            const active = activeBySpeechId.get(String(message.speech_id));
            if (active && active.socket === this) {
              // The legacy React bundle emits this in canplaythrough. That event
              // only means the media is ready, not that Audio.play() succeeded.
              // armPlaybackStartedAck() sends the one authoritative ACK later.
              this.__prematurePlaybackAcksSuppressed += 1;
              return;
            }
          }
        } catch (_error) {
          // Non-JSON application payloads keep the native WebSocket behavior.
        }
      }
      return super.send(payload);
    }
  }

  function ExternalPlaybackAudio(src, speechId) {
    const audio = new NativeAudio(src);
    if (typeof src !== "string") {
      return audio;
    }

    const pending = (speechId && pendingBySpeechId.get(speechId)) || pendingByDataUrl.get(src);
    if (pending) {
      pendingByDataUrl.delete(src);
      if (cancelledSpeechIds.has(pending.speechId)) {
        return createSilentDuplicateAudio(src);
      }
      if (!pending.external && !rememberPlayback(`speech:${pending.speechId}`)) {
        return createSilentDuplicateAudio(src);
      }
      const playback = { playerId: nextPlayerId(), started: false };
      activeBySpeechId.set(pending.speechId, { ...pending, audio, playerId: playback.playerId, playback });
      armPlaybackStartedAck(audio, pending, playback);
      const clearActive = () => {
        if (activeBySpeechId.get(pending.speechId)?.audio === audio) {
          activeBySpeechId.delete(pending.speechId);
        }
      };
      let finished = false;
      let completed = false;
      let mediaRetryCount = 0;

      const completeOnce = (status) => {
        if (!pending.external || completed) return;
        completed = true;
        sendPlaybackComplete(pending, status);
      };
      const finishLipSync = (status = "ended") => {
        if (finished) return;
        finished = true;
        emitLipSyncEvent("celia-lipsync-stop", { speechId: pending.speechId });
        if (playback.started) {
          sendNative(pending.socket, {
            type: "audio-playback-finished", speech_id: pending.speechId,
            player_id: playback.playerId, status,
          });
        }
      };
      const mediaFailureDetail = (phase) => {
        const mediaError = audio.error;
        const code = mediaError && Number.isFinite(Number(mediaError.code))
          ? Number(mediaError.code) : 0;
        const networkState = Number.isFinite(Number(audio.networkState))
          ? Number(audio.networkState) : -1;
        const readyState = Number.isFinite(Number(audio.readyState))
          ? Number(audio.readyState) : -1;
        return phase + ":code=" + code
          + ",network=" + networkState
          + ",ready=" + readyState
          + ",src_bytes=" + String(src).length;
      };
      const markTerminalFailure = (reason) => {
        if (isCancelled(pending.speechId)) return;
        speechStates.set(pending.speechId, "failed");
        trace("AUDIO_DROPPED", pending.speechId, reason);
        queueMicrotask(() => emitSyntheticEnded(audio));
      };

      audio.addEventListener("ended", () => {
        const failed = speechStates.get(pending.speechId) === "failed";
        const cancelled = isCancelled(pending.speechId);
        if (!cancelled && !failed) {
          speechStates.set(pending.speechId, "finished");
          trace("AUDIO_FINISHED", pending.speechId);
        }
        pendingBySpeechId.delete(pending.speechId);
        clearActive();
        finishLipSync(cancelled ? "cancelled" : failed ? "error" : "ended");
        completeOnce(failed ? "error" : "ended");
        releaseLane(pending.speechId);   // por último: o "finished" chega ao servidor antes do próximo "started"
      }, { once: true });

      audio.addEventListener("error", () => {
        if (isCancelled(pending.speechId)) return;
        if (mediaRetryCount === 0) {
          mediaRetryCount = 1;
          trace("AUDIO_RETRY", pending.speechId, mediaFailureDetail("media_error"));
          try {
            audio.pause();
            audio.src = src;
            audio.load();
            const retry = audio.play();
            if (retry && typeof retry.catch === "function") retry.catch(() => {});
          } catch (_error) {
            markTerminalFailure(mediaFailureDetail("media_retry_throw"));
          }
          return;
        }
        markTerminalFailure(mediaFailureDetail("media_error_retry_failed"));
      });

      if (!pending.external) {
        releaseQueueOnPlaybackFailure(audio, () => {
          if (isCancelled(pending.speechId)) return;
          speechStates.set(pending.speechId, "failed");
          trace("AUDIO_DROPPED", pending.speechId, "play_failed");
        }, { listenForMediaError: false });
        return audio;
      }

      releaseQueueOnPlaybackFailure(audio, () => {
        if (!isCancelled(pending.speechId)) {
          speechStates.set(pending.speechId, "failed");
          trace("AUDIO_DROPPED", pending.speechId, "play_failed");
        }
      }, { listenForMediaError: false });
      return audio;
    }

    if (!rememberPlayback(src)) {
      return createSilentDuplicateAudio(src);
    }

    releaseQueueOnPlaybackFailure(audio);
    return audio;
  }

  ExternalPlaybackAudio.prototype = NativeAudio.prototype;
  Object.setPrototypeOf(ExternalPlaybackAudio, NativeAudio);
  window.WebSocket = ExternalPlaybackWebSocket;
  window.Audio = ExternalPlaybackAudio;
})();
