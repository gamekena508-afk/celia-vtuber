/* Ctrl+M controls the shared server microphone state (browser + Electron Pet).
 * Tracks stay muted until the server confirms state. Existing app WebSockets
 * carry synchronization; no extra connection, storage or polling is needed.
 */
(function installMicToggle() {
  "use strict";

  // Live-output capture denial and the WebGL buffer flag live in
  // celia-live-output-guard.js, loaded before celia-escolha.js.

  if (
    !navigator.mediaDevices ||
    typeof navigator.mediaDevices.getUserMedia !== "function"
  ) {
    console.warn("[mic_toggle] navigator.mediaDevices.getUserMedia indisponível");
    return;
  }

  // Desktop Pet is a display surface. Only the normal Célia window may
  // open the physical microphone; this remains true with an older backend.
  const renderOnly = (() => {
    try { const q = new URLSearchParams(window.location.search);
      return q.get("pet") === "1" || q.get("celiaScene") === "1" || q.get("liveOutput") === "1";
    } catch (_) { return false; }
  })();
  const audioTracks = new Set();
  let micEnabled = false;
  let windowAllowed = true;
  let manualEnabled = false;
  let revision = -1;
  let requested = null;
  const sockets = new Set();
  const MIC_MESSAGES = new Set(["speech.started", "mic-audio-data", "mic-audio-end"]);
  const NativeWebSocket = window.WebSocket;
  class SharedMicWebSocket extends NativeWebSocket {
    constructor(...args) {
      super(...args);
      if (!String(args[0] || "").includes("/client-ws")) return;
      sockets.add(this);
      this.addEventListener("open", () => {
        // A reconnect may belong to a restarted server; its revisions start over.
        revision = -1;
        super.send(JSON.stringify({type: "microphone-state-get"}));
        if (requested !== null) super.send(JSON.stringify({type: "microphone-state-set", enabled: requested}));
      });
      this.addEventListener("message", event => {
        let data;
        try { data = JSON.parse(event.data); } catch (_) { return; }
        if (data.type !== "microphone-state" || typeof data.enabled !== "boolean"
            || !Number.isInteger(data.revision) || data.revision < revision) return;
        revision = data.revision;
        // While our command is in flight, an initial snapshot must not reopen
        // a mic we have just muted.
        if (requested !== null && data.enabled !== requested) return;
        requested = null;
        windowAllowed = data.window_allowed !== false;
        manualEnabled = data.enabled;
        const effective = manualEnabled && windowAllowed && !renderOnly;
        if (effective !== micEnabled) applyMic(effective);
      });
      this.addEventListener("close", () => {
        sockets.delete(this);
        if (![...sockets].some(s => s.readyState === 1)) {
          applyMic(false);
          showBanner("🔇 MIC SEM CONEXÃO", "#a82424");
        }
      });
    }
    send(payload) {
      if (!micEnabled && typeof payload === "string") {
        try { if (MIC_MESSAGES.has(JSON.parse(payload).type)) return; } catch (_) {}
      }
      return super.send(payload);
    }
  }
  window.WebSocket = SharedMicWebSocket;
  // Refresh source ownership even when no local audio is being sent.
  if (typeof setInterval === "function") setInterval(() => {
    for (const socket of sockets) {
      if (socket.readyState === 1) socket.send(JSON.stringify({type:"microphone-state-get"}));
    }
  }, 1000);

  function rememberStream(stream) {
    if (!stream || typeof stream.getAudioTracks !== "function") {
      return;
    }
    for (const track of stream.getAudioTracks()) {
      audioTracks.add(track);
      // Sincroniza o estado inicial da track com o estado atual do toggle
      try {
        track.enabled = micEnabled;
      } catch (_e) {}
      track.addEventListener("ended", () => audioTracks.delete(track));
    }
  }

  const NativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(
    navigator.mediaDevices,
  );
  // Keep the app/VAD attached to a stable logical stream, while physically
  // releasing the microphone when Discord, another surface or mute owns it.
  let logical = null, physical = null, physicalNode = null, opening = null;
  let captureError = null, generation = 0, retryAt = 0;
  function releasePhysical() {
    generation++;
    if (physicalNode) { try { physicalNode.disconnect(); } catch (_) {} }
    if (physical) for (const track of physical.getTracks()) track.stop();
    physical = null; physicalNode = null;
  }
  async function syncPhysical() {
    if (renderOnly || !micEnabled || !logical) { releasePhysical(); return; }
    if (physical || opening || Date.now() < retryAt) return;
    const ticket = generation;
    const target = logical;
    opening = (async () => {
      let stream;
      try {
        try { stream = await NativeGetUserMedia(target.constraints); }
        catch (err) {
          if (err?.name !== "NotFoundError" && err?.name !== "OverconstrainedError") throw err;
          stream = await NativeGetUserMedia({audio:true});
        }
        if (!micEnabled || generation !== ticket || logical !== target) {
          stream.getTracks().forEach(t=>t.stop()); return;
        }
        physicalNode = target.ctx.createMediaStreamSource(stream);
        physicalNode.connect(target.dest);
        physical = stream;
        captureError = null;
        for (const track of stream.getAudioTracks()) track.addEventListener("ended", () => {
          if (physical === stream) { releasePhysical(); retryAt=0; syncPhysical(); }
        });
        if (target.ctx.state === "suspended") target.ctx.resume().catch(()=>{});
      } catch (err) {
        if (stream) stream.getTracks().forEach(t=>t.stop());
        captureError = String(err?.name || err);
        retryAt = Date.now() + 5000;
        console.warn("[mic_toggle] captura física:", captureError);
      }
    })();
    try { await opening; } finally {
      opening = null;
      if (micEnabled && logical && !physical && generation !== ticket) syncPhysical();
    }
  }
  navigator.mediaDevices.getUserMedia = async function patchedGetUserMedia(constraints) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx && constraints?.audio && !constraints.video) {
      if (!logical || logical.dest.stream.getAudioTracks().every(t=>t.readyState==="ended")) {
        releasePhysical();
        if (logical) logical.ctx.close().catch(()=>{});
        const ctx = new Ctx({latencyHint:"interactive"});
        logical = {ctx, dest:ctx.createMediaStreamDestination(), constraints};
        rememberStream(logical.dest.stream);
      }
      syncPhysical();
      return logical.dest.stream;
    }
    if (renderOnly && constraints?.audio) {
      throw new DOMException("Microphone unavailable on display-only Célia surface", "NotAllowedError");
    }
    const stream = await NativeGetUserMedia(constraints);
    rememberStream(stream);
    return stream;
  };
  if (typeof setInterval === "function") setInterval(()=> {
    if (micEnabled && logical && !physical) syncPhysical();
  },1000);
  if (typeof window.addEventListener === "function") window.addEventListener("pagehide",releasePhysical);

  // Também pega streams já existentes (caso o app tenha chamado antes deste shim)
  // Não dá pra resgatar streams antigos, mas pelo menos novos serão pegos.

  let banner = null;
  let bannerTimer = null;

  function showBanner(text, color) {
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "mic-toggle-banner";
      banner.style.cssText = [
        "position:fixed",
        "top:20px",
        "left:50%",
        "transform:translateX(-50%)",
        "padding:10px 22px",
        "font-family:system-ui,-apple-system,sans-serif",
        "font-size:16px",
        "font-weight:bold",
        "color:#fff",
        "border-radius:8px",
        "z-index:999999",
        "pointer-events:none",
        "box-shadow:0 4px 14px rgba(0,0,0,0.35)",
        "transition:opacity 0.25s",
      ].join(";");
      document.body.appendChild(banner);
    }
    banner.style.background = color;
    banner.textContent = text;
    banner.style.opacity = "1";
    if (bannerTimer) {
      clearTimeout(bannerTimer);
    }
    bannerTimer = setTimeout(() => {
      if (banner) {
        banner.style.opacity = "0";
      }
    }, 1500);
  }

  function applyMic(enabled) {
    micEnabled = enabled;
    if (!enabled) window.__rmsVad?.resetMicrophoneCapture?.();
    let touched = 0;
    for (const track of audioTracks) {
      try {
        track.enabled = enabled;
        touched += 1;
      } catch (_e) {}
    }
    if (enabled) syncPhysical(); else releasePhysical();
    const label = enabled ? "🎤 MIC LIGADO" : "🔇 MIC DESLIGADO";
    const color = enabled ? "#1f8a4c" : "#a82424";
    console.log(`[mic_toggle] ${label} (${touched} track(s))`);
    showBanner(label, color);
  }

  function setMic(enabled) {
    requested = Boolean(enabled);
    // Muting is immediate; reopening requires server confirmation.
    applyMic(false);
    const socket = [...sockets].find(s => s.readyState === 1);
    if (socket) {
      socket.send(JSON.stringify({type: "microphone-state-set", enabled: requested}));
      showBanner("🎤 SINCRONIZANDO MICROFONE…", "#756000");
    } else {
      showBanner("🔇 MIC SEM CONEXÃO", "#a82424");
    }
  }

  function toggleMic() {
    setMic(!(requested === null ? manualEnabled : requested));
  }

  document.addEventListener(
    "keydown",
    (event) => {
      // Ctrl+M (ou Cmd+M no Mac). Ignora se o foco está em campo de texto.
      if (!event.ctrlKey && !event.metaKey) {
        return;
      }
      if (event.key !== "m" && event.key !== "M") {
        return;
      }
      const target = event.target;
      const tag = target && target.tagName ? target.tagName.toUpperCase() : "";
      if (tag === "INPUT" || tag === "TEXTAREA" || (target && target.isContentEditable)) {
        return;
      }
      if (event.repeat) return;
      event.preventDefault();
      event.stopPropagation();
      toggleMic();
    },
    true,
  );

  // Expor para console / outros scripts
  window.__micToggle = {
    on: () => setMic(true),
    off: () => setMic(false),
    toggle: toggleMic,
    state: () => micEnabled,
    manualState: () => manualEnabled,
    windowAllowed: () => windowAllowed,
    tracks: () => audioTracks,
    captureStatus: () => ({authorized:micEnabled, physical:Boolean(physical), error:captureError, renderOnly}),
  };

  console.log(
    "[mic_toggle] Ctrl+M instalado. Use window.__micToggle.{on,off,toggle,state}() no console.",
  );
})();
