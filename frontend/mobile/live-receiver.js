(() => {
  "use strict";
  // Receive-only transport for the TikTok LIVE Studio browser source.
  // No device APIs, no controls, no avatar rendering: only the publisher's A/V.
  const video = document.getElementById("celia-video");
  const overlay = document.getElementById("overlay");
  const statusEl = document.getElementById("status");
  const unmute = document.getElementById("unmute");
  const diagEl = document.getElementById("diag");
  const RECONNECT_MIN_MS = 1000, RECONNECT_MAX_MS = 8000, MEDIA_TIMEOUT_MS = 12000;
  const MAX_RECOVERIES = 6;          // forced fresh-viewer cycles without any proven video progress
  const DISCONNECT_GRACE_MS = 4000;  // "disconnected" is often transient in WebRTC
  const FRAME_STALL_MS = 10000;      // connected + no frame/time progress for this long => recover
  const ADVANCE_WINDOW_MS = 2500, MONITOR_MS = 1000;

  let socket = null, pc = null, reconnectTimer = null, mediaTimer = null, disconnectTimer = null;
  let delay = RECONNECT_MIN_MS, generation = 0, studioOnline = false, queued = [];
  let socketOpen = false, stopped = false;
  let recoveries = 0, pendingRecovery = false, monitor = null, exhausted = false;
  let lastFrames = 0, lastTime = 0, lastAdvanceAt = 0, advancedEver = false, advancing = false;
  // Compact, secret-free diagnostic state. Only enum-like values and sanitized exception
  // name/message are stored here: never SDP, candidates, addresses or credentials.
  const diag = {pc: "none", ice: "none", ws: "connecting", sig: "-", play: "idle",
    error: "", candErrors: 0, recovery: ""};
  // Inbound quality (numbers only, from getStats inbound-rtp). Answers "what actually arrives":
  // decoded size, fps, kbps, average QP (higher = more compressed), loss, freezes.
  const net = {w: 0, h: 0, fps: null, kbps: null, qp: null, lost: null, freeze: null, drop: null};
  let lastIn = null, statsBusy = false;
  const num = (x, d = 0) => (typeof x === "number" && Number.isFinite(x) ? x.toFixed(d) : "—");
  async function pollStats() {
    const peer = pc;
    if (!peer || peer.connectionState !== "connected" || statsBusy) return;
    statsBusy = true;
    try {
      const report = await peer.getStats();
      let v = null;
      for (const s of report.values()) if (s.type === "inbound-rtp" && (s.kind === "video" || s.mediaType === "video")) { v = s; break; }
      if (!v || peer !== pc) return;
      const now = performance.now();
      if (lastIn && lastIn.peer === peer) {
        const dt = (now - lastIn.at) / 1000, df = v.framesDecoded - lastIn.frames;
        if (dt > 0) {
          net.kbps = (v.bytesReceived - lastIn.bytes) * 8 / dt / 1000;
          net.fps = df / dt;
          net.qp = df > 0 && typeof v.qpSum === "number" ? (v.qpSum - lastIn.qp) / df : null;
        }
      }
      lastIn = {peer, at: now, bytes: v.bytesReceived, frames: v.framesDecoded, qp: v.qpSum || 0};
      net.w = v.frameWidth | 0; net.h = v.frameHeight | 0;
      net.lost = v.packetsLost; net.freeze = v.freezeCount; net.drop = v.framesDropped;
    } catch (_) { /* stats unavailable: keep the last numbers */ } finally { statsBusy = false; }
  }

  // Exception text may embed pieces of the SDP/candidate that failed to parse: cut at the first
  // SDP-looking token, keep only the first line, bound the length.
  const SENSITIVE = /(?:^|[\s:'"(])[a-z]=\S|candidate:|ufrag|ice-pwd|fingerprint|\b\d{1,3}(?:\.\d{1,3}){3}\b|[0-9a-f]{1,4}(?::[0-9a-f]{0,4}){3,}/i;
  function safeText(value, max = 120) {
    let text = String(value == null ? "" : value).split(/\r?\n/)[0];
    const hit = text.search(SENSITIVE);
    if (hit >= 0) text = text.slice(0, hit).trimEnd() + " [redigido]";
    return text.slice(0, max);
  }
  function errorLabel(err) {
    const name = safeText(err && err.name ? err.name : "Error", 40);
    const message = safeText(err && err.message ? err.message : "", 100);
    return message ? name + ": " + message : name;
  }

  function render() {
    if (!diagEl) return;
    const w = video.videoWidth | 0, h = video.videoHeight | 0;
    const fields = {
      pc: diag.pc, ice: diag.ice, ws: diag.ws, sig: diag.sig, play: diag.play,
      size: w + "x" + h, frames: String(lastFrames), advancing: advancing ? "1" : "0",
      recoveries: recoveries + "/" + MAX_RECOVERIES, recovery: diag.recovery,
      error: diag.error, candErrors: String(diag.candErrors)
    };
    for (const key of Object.keys(fields)) if (diagEl.dataset[key] !== fields[key]) diagEl.dataset[key] = fields[key];
    const text = "pc:" + fields.pc + " ice:" + fields.ice + " ws:" + fields.ws + " sig:" + fields.sig +
      " play:" + fields.play + "\nvideo:" + fields.size + " frames:" + fields.frames +
      " avançando:" + (advancing ? "sim" : "não") + " recup:" + fields.recoveries +
      "\nrede: " + (net.w ? net.w + "x" + net.h : "—") + " " + num(net.fps, 1) + "fps " + num(net.kbps) + "kbps QP:" + num(net.qp, 1) +
      " perda:" + num(net.lost) + " travadas:" + num(net.freeze) + " descartados:" + num(net.drop) +
      (diag.recovery ? " (" + diag.recovery + ")" : "") +
      (diag.candErrors ? " cand-err:" + diag.candErrors : "") +
      (diag.error ? "\nerro: " + diag.error : "");
    if (diagEl.textContent !== text) diagEl.textContent = text;
  }
  // After the recovery cap the failure stays on screen (routine messages from a still-open socket
  // must not erase it) until real video progress is proven or the user reloads the page.
  function show(text, isError = false, force = false) {
    if (exhausted && !force) return;
    statusEl.textContent = text;
    statusEl.dataset.error = isError ? "1" : "0";
  }
  function audioBlocked() { return !unmute.hidden || overlay.dataset.audioBlocked === "1"; }
  // "Live" only while the socket is open, the peer is connected AND the video is actually playing
  // with real dimensions (a "playing" element with 0x0 is the no-video symptom).
  function refreshLive() {
    const live = socketOpen && pc && pc.connectionState === "connected" &&
      !video.paused && !video.ended && video.readyState >= 2 && !!video.srcObject &&
      (video.videoWidth | 0) > 0;
    document.body.dataset.live = live ? "1" : "0";
    if (live) {
      unmute.hidden = true; delete overlay.dataset.audioBlocked;
    }
    render();
  }
  function resetFrameWatch() {
    lastFrames = 0; lastTime = 0; lastAdvanceAt = 0; advancedEver = false; advancing = false;
  }
  function closePeer() {
    generation++;
    clearTimeout(mediaTimer); clearTimeout(disconnectTimer);
    if (pc) {
      pc.ontrack = null; pc.onicecandidate = null; pc.onconnectionstatechange = null;
      pc.oniceconnectionstatechange = null;
      try { pc.close(); } catch (_) { /* already closed */ }
      pc = null;
    }
    diag.pc = "none"; diag.ice = "none";
    lastIn = null; net.w = net.h = 0; net.fps = net.kbps = net.qp = net.lost = net.freeze = net.drop = null;
    resetFrameWatch();
    video.pause();
    video.srcObject = null;
    unmute.hidden = true; delete overlay.dataset.audioBlocked;
    diag.play = "idle";
    refreshLive();
  }
  // Bounded renegotiation: tear the peer down and close the WebSocket so the existing reconnect
  // path (backoff 1..8 s) joins as a fresh viewer. Never used for an autoplay block.
  function recover(reason) {
    if (stopped) return false;
    const seen = "pc=" + diag.pc + "/ice=" + diag.ice;
    diag.recovery = safeText(reason, 40) + "[" + seen + "]";
    if (recoveries >= MAX_RECOVERIES) {
      exhausted = true;
      closePeer();
      show("Sem vídeo. Recuperação automática esgotada. Recarregue a página.", true, true);
      return false;
    }
    recoveries++;
    const ws = socket;
    closePeer();
    show("Renegociando vídeo (" + recoveries + "/" + MAX_RECOVERIES + ")…", true);
    if (ws) {
      pendingRecovery = true;
      try { ws.close(); } catch (_) { /* close handler or error path reconnects */ }
    }
    render();
    return true;
  }
  function send(payload) {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
  }
  async function tryPlay() {
    video.muted = false; // original TTS audio must be heard
    try {
      await video.play();
      diag.play = "ok";
    } catch (err) {
      if (!video.srcObject) return;
      const name = err && err.name ? safeText(err.name, 40) : "Error";
      if (name === "NotAllowedError") {
        // Autoplay policy: wait for a user gesture; this must NOT trigger renegotiation.
        diag.play = "bloqueado";
        unmute.hidden = false; overlay.dataset.audioBlocked = "1";
        show("Áudio bloqueado pelo navegador.");
      } else if (name === "AbortError") {
        diag.play = "interrompido"; // a newer load/pause superseded this play(); not a block
      } else {
        diag.play = "erro:" + name; // recoverable by the media timeout
      }
      refreshLive();
    }
  }
  function newPeer() {
    closePeer();
    const mine = generation;
    const peer = new RTCPeerConnection({iceServers: []}); // loopback/LAN only, no STUN/TURN
    pc = peer;
    diag.pc = peer.connectionState || "new"; diag.ice = peer.iceConnectionState || "new";
    peer.onicecandidate = ({candidate}) => {
      if (mine === generation && candidate) send({type: "signal", data: {type: "candidate", candidate: candidate.toJSON()}});
    };
    peer.ontrack = (event) => {
      if (mine !== generation) return;
      const stream = event.streams[0] || new MediaStream([event.track]);
      if (video.srcObject !== stream) video.srcObject = stream;
      event.track.addEventListener("ended", () => { if (mine === generation) refreshLive(); });
      tryPlay();
    };
    peer.onconnectionstatechange = () => {
      if (mine !== generation) return;
      const state = peer.connectionState;
      diag.pc = state;
      clearTimeout(disconnectTimer);
      if (state === "failed" || state === "closed") { recover("peer-" + state); return; }
      if (state === "disconnected") {
        disconnectTimer = setTimeout(() => {
          if (mine === generation && peer.connectionState === "disconnected") recover("peer-disconnected");
        }, DISCONNECT_GRACE_MS);
      }
      refreshLive();
    };
    peer.oniceconnectionstatechange = () => {
      if (mine !== generation) return;
      diag.ice = peer.iceConnectionState;
      if (diag.ice === "failed") { recover("ice-failed"); return; }
      render();
    };
    mediaTimer = setTimeout(() => {
      if (mine !== generation || audioBlocked()) return; // autoplay block: wait for the user
      if (document.body.dataset.live === "1" && advancedEver) return;
      show("Sem vídeo recebido do estúdio.", true);
      recover("media-timeout");
    }, MEDIA_TIMEOUT_MS);
    render();
    return peer;
  }
  async function incomingSignal(data) {
    if (data.type === "offer" && typeof data.sdp === "string") {
      const peer = newPeer();
      const mine = generation;
      diag.sig = "offer"; diag.error = "";
      show("Recebendo vídeo…");
      await peer.setRemoteDescription({type: "offer", sdp: data.sdp});
      if (mine !== generation) return;
      for (const candidate of queued.splice(0)) await peer.addIceCandidate(candidate).catch(noteCandidateError);
      const answer = await peer.createAnswer();
      await peer.setLocalDescription(answer);
      if (mine !== generation) return;
      send({type: "signal", data: {type: "answer", sdp: peer.localDescription.sdp}});
      diag.sig = "answer-enviada";
      render();
    } else if (data.type === "candidate" && data.candidate) {
      // Candidates may arrive before the offer: hold them until a remote description exists.
      if (pc && pc.remoteDescription) await pc.addIceCandidate(data.candidate).catch(noteCandidateError);
      else if (queued.length < 64) queued.push(data.candidate);
    }
  }
  function noteCandidateError(err) {
    diag.candErrors++;
    diag.error = "candidate " + errorLabel(err);
    render();
  }
  function connect() {
    clearTimeout(reconnectTimer);
    if (stopped) return;
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(scheme + "//" + location.host + "/live-output/ws");
    socket = ws;
    diag.ws = "connecting"; diag.sig = "-";
    ws.addEventListener("open", () => {
      if (socket !== ws) return;
      socketOpen = true; delay = RECONNECT_MIN_MS; pendingRecovery = false;
      diag.ws = "open";
      show("Conectado. Aguardando o estúdio…");
      render();
    });
    ws.addEventListener("message", async ({data}) => {
      if (socket !== ws) return;
      let msg; try { msg = JSON.parse(data); } catch (_) { return; }
      if (!msg || typeof msg !== "object") return;
      if (msg.type === "ready") {
        studioOnline = !!msg.online;
        if (!studioOnline) { closePeer(); queued = []; }
        show(studioOnline ? "Aguardando vídeo do estúdio…" : "Estúdio offline. Aguardando…");
      } else if (msg.type === "studio-online") {
        studioOnline = true; show("Estúdio conectado. Aguardando vídeo…");
      } else if (msg.type === "studio-offline") {
        studioOnline = false; closePeer(); queued = [];
        show("Estúdio offline. Aguardando…");
      } else if (msg.type === "signal" && msg.data && typeof msg.data === "object") {
        try { await incomingSignal(msg.data); } catch (err) {
          if (socket !== ws) return;
          diag.error = errorLabel(err); diag.sig = "erro";
          console.warn("Falha na negociação de vídeo:", diag.error); // name/message only, never SDP
          show("Falha na negociação do vídeo. Renegociando…", true);
          recover("negotiation-failed");
        }
      }
      render();
    });
    ws.addEventListener("close", (e) => {
      if (socket !== ws) return;
      socket = null; socketOpen = false; studioOnline = false; queued = [];
      diag.ws = "fechado:" + (Number.isFinite(e && e.code) ? e.code : "?");
      closePeer();
      if (e.code === 4403) { stopped = true; show("Acesso negado ao receptor (somente local).", true, true); return; }
      if (pendingRecovery) show("Renegociando vídeo (" + recoveries + "/" + MAX_RECOVERIES + ")…", true);
      else show(e.code === 4409 ? "Receptor já em uso. Tentando novamente…" : "Conexão perdida. Reconectando…", e.code === 4409);
      reconnectTimer = setTimeout(connect, delay);
      delay = Math.min(RECONNECT_MAX_MS, delay * 2);
    });
    ws.addEventListener("error", () => {
      if (socket === ws) diag.ws = "erro";
      try { ws.close(); } catch (_) { /* ignore */ }
    });
  }

  function frameCount() {
    if (!video.srcObject) return 0;
    try {
      if (typeof video.getVideoPlaybackQuality === "function") {
        const q = video.getVideoPlaybackQuality();
        if (q && Number.isFinite(q.totalVideoFrames)) return q.totalVideoFrames;
      }
    } catch (_) { /* fall through */ }
    return Number.isFinite(video.webkitDecodedFrameCount) ? video.webkitDecodedFrameCount : 0;
  }
  // Progress = decoded frames OR playback clock moved; either one avoids false stalls when a
  // hidden/off-screen element does not report presented frames.
  function tick() {
    const now = Date.now();
    const frames = frameCount(), time = Number(video.currentTime) || 0;
    if (frames > lastFrames || time > lastTime + 0.01) { lastAdvanceAt = now; advancedEver = true; }
    lastFrames = frames; lastTime = time;
    pollStats();
    advancing = lastAdvanceAt > 0 && now - lastAdvanceAt <= ADVANCE_WINDOW_MS;
    if (advancing && document.body.dataset.live === "1" && (recoveries || exhausted)) { recoveries = 0; exhausted = false; }
    if (pc && pc.connectionState === "connected" && !audioBlocked() && advancedEver &&
        now - lastAdvanceAt > FRAME_STALL_MS) {
      recover("frames-stalled");
      return;
    }
    render();
  }

  for (const name of ["playing", "pause", "waiting", "stalled", "ended", "emptied", "loadedmetadata", "resize"]) {
    video.addEventListener(name, refreshLive);
  }
  video.addEventListener("playing", () => { unmute.hidden = true; diag.play = "ok"; });
  unmute.addEventListener("click", async () => {
    video.muted = false;
    try { await video.play(); } catch (_) { unmute.textContent = "Toque novamente para ativar o áudio"; }
    refreshLive();
  });
  window.addEventListener("pagehide", () => {
    stopped = true; clearInterval(monitor); clearTimeout(reconnectTimer);
    closePeer(); if (socket) socket.close();
  });
  try {
    if (new URLSearchParams(location.search).get("diag") === "1") document.body.dataset.diag = "1";
  } catch (_) { /* optional flag */ }
  monitor = setInterval(tick, MONITOR_MS);
  render();
  connect();
})();
