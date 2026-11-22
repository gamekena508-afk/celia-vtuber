(() => {
  "use strict";
  const el = (id) => document.getElementById(id);
  const video = el("celia-video"), offline = el("offline"), status = el("connection-status");
  const stage = el("mobile-stage"), settings = el("settings");
  const move = el("move-character");
  let socket = null, pc = null, reconnect = null, dragId = null, last = null;
  let queuedCandidates = [];

  function report(label, online = false) {
    status.textContent = label;
    offline.hidden = online;
    if (!online) offline.querySelector("p").textContent = label;
  }
  function send(payload) {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
  }
  function action(name, extra = {}) {
    send({type: "control", action: name, ...extra});
  }
  function closePeer() {
    if (pc) {pc.ontrack = null; pc.close(); pc = null;}
    video.srcObject = null;
    queuedCandidates = [];
    report("Aguardando transmissão do PC…");
  }
  function newPeer() {
    if (pc) pc.close();
    pc = new RTCPeerConnection({iceServers: []}); // LAN only: no external STUN/TURN
    // Keep candidates already received before the offer was processed.
    pc.onicecandidate =  ({candidate}) => {
      if (candidate) send({type: "signal", data: {type: "candidate", candidate: candidate.toJSON()}});
    };
    pc.ontrack = (event) => {
      video.srcObject = event.streams[0] || new MediaStream([event.track]);
      report("Célia ao vivo no PC", true);
      video.play().catch(() => { status.textContent = "Toque em Ativar áudio"; });
    };
    pc.onconnectionstatechange = () => {
      if (!pc) return;
      if (["failed", "disconnected", "closed"].includes(pc.connectionState)) {
        closePeer();
      }
    };
    return pc;
  }
  async function incomingSignal(data) {
    if (data.type === "offer" && typeof data.sdp === "string") {
      const peer = newPeer();
      await peer.setRemoteDescription({type: "offer", sdp: data.sdp});
      for (const candidate of queuedCandidates.splice(0)) await peer.addIceCandidate(candidate);
      await peer.setLocalDescription(await peer.createAnswer());
      send({type: "signal", data: {type: "answer", sdp: peer.localDescription.sdp}});
    } else if (data.type === "candidate" && data.candidate) {
      if (pc && pc.remoteDescription) await pc.addIceCandidate(data.candidate);
      else queuedCandidates.push(data.candidate);
    }
  }
  function connect() {
    clearTimeout(reconnect);
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(scheme + "//" + location.host + "/mobile/ws");
    socket.addEventListener("open", () => report("Conectado, aguardando o estúdio…"));
    socket.addEventListener("message", async ({data}) => {
      let msg; try {msg = JSON.parse(data);} catch (_) {return;}
      if (msg.type === "ready") report(msg.online ? "Conectando vídeo…" : "Estúdio offline — abra no PC");
      if (msg.type === "studio-online") report("Estúdio conectado, aguardando vídeo…");
      if (msg.type === "studio-offline") closePeer();
      if (msg.type === "stage-state" && msg.state) {
        for (const key of ["scene", "body", "model"]) {
          if (typeof msg.state[key] === "string" && el(key)) el(key).value = msg.state[key];
        }
      }
      if (msg.type === "signal" && msg.data) {
        try {await incomingSignal(msg.data);} catch (err) {
          console.warn("Falha de negociação de vídeo:", err); report("Falha no vídeo — tente reconectar");
        }
      }
    });
    socket.addEventListener("close", (e) => {
      closePeer();
      if (e.code === 4401 || e.code === 1008) {location.replace("/mobile/");return;}
      report("Conexão interrompida. Reconectando…");
      reconnect = setTimeout(connect, 2300);
    });
    socket.addEventListener("error", () => { if (socket) socket.close(); });
  }

  // Two fingers over the scene pinch to zoom; one-finger dragging is exclusively
  // owned by the ✥ handle, so accidental swipes never relocate the character.
  const touchPoints = new Map();
  let previousPinch = null;
  function pinchSize() {
    if (touchPoints.size !== 2) return null;
    const pts = Array.from(touchPoints.values());
    return Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
  }
  stage.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch" || e.target.closest(".controls") || e.target.id === "show-controls") return;
    touchPoints.set(e.pointerId, {x: e.clientX, y: e.clientY});
    stage.setPointerCapture(e.pointerId);
    previousPinch = pinchSize();
  });
  stage.addEventListener("pointermove", (e) => {
    if (!touchPoints.has(e.pointerId)) return;
    touchPoints.set(e.pointerId, {x: e.clientX, y: e.clientY});
    const current = pinchSize();
    if (previousPinch && current && previousPinch > 1) {
      const step = Math.max(-0.2, Math.min(0.2, (current - previousPinch) / previousPinch));
      if (Math.abs(step) > .003) action("zoom", {step});
    }
    previousPinch = current;
  });
  for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
    stage.addEventListener(name, (e) => {
      touchPoints.delete(e.pointerId);
      previousPinch = pinchSize();
    });
  }
  el("smaller").addEventListener("click", () => action("zoom", {step: -0.12}));
  el("larger").addEventListener("click", () => action("zoom", {step: 0.12}));
  el("center").addEventListener("click", () => action("center"));
  for (const key of ["scene", "body", "model"]) {
    el(key).addEventListener("change", (e) => action(key, {value: e.target.value}));
  }
  // Only the central handle owns drag. Caption and other touch targets never move the avatar.
  move.addEventListener("pointerdown", (e) => {
    if (dragId !== null) return;
    e.preventDefault();
    dragId = e.pointerId; last = {x: e.clientX, y: e.clientY};
    move.setPointerCapture(e.pointerId);
  });
  move.addEventListener("pointermove", (e) => {
    if (dragId !== e.pointerId || !last) return;
    e.preventDefault();
    const dx = (e.clientX - last.x) / Math.max(1, stage.clientWidth);
    const dy = (e.clientY - last.y) / Math.max(1, stage.clientHeight);
    last = {x: e.clientX, y: e.clientY};
    if (dx || dy) action("move", {dx, dy});
  });
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
    move.addEventListener(type, (e) => {
      if (dragId === e.pointerId) {dragId = null; last = null;}
    });
  }
  el("settings-toggle").addEventListener("click", () => {settings.hidden = !settings.hidden;});
  el("fullscreen").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen();
        try {if (screen.orientation && screen.orientation.lock) await screen.orientation.lock("portrait");} catch (_) { /* Android/browser may forbid orientation lock */ }
      }
    } catch (_) {report("Seu navegador bloqueou tela cheia; use adicionar à tela inicial.");}
  });
  el("unmute").addEventListener("click", async () => {
    video.muted = false;
    try {await video.play(); el("unmute").textContent = "Áudio ativado"; }
    catch (_) { el("unmute").textContent = "Toque novamente para áudio"; }
  });
  el("hide-controls").addEventListener("click", () => {
    settings.hidden = true;
    document.querySelectorAll(".controls").forEach(node => node.hidden = true);
    el("show-controls").hidden = false;
  });
  el("show-controls").addEventListener("click", () => {
    document.querySelectorAll(".controls").forEach(node => {node.hidden = false;});
    settings.hidden = true; el("show-controls").hidden = true;
  });
  el("logout").addEventListener("click", async () => {
    try {await fetch("/mobile/logout", {method: "POST", credentials: "same-origin"});} finally {location.replace("/mobile/");}
  });
  connect();
})();
