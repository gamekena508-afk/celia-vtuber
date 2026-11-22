"use strict";
// Focused tests for frontend/mobile/live-receiver.js diagnostics + bounded recovery.
// Run: node --test tests/test_live_receiver_recovery.cjs
// The receiver runs in a vm with a fake DOM, WebSocket, RTCPeerConnection and a manual clock.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SOURCE_PATH = path.join(__dirname, "..", "frontend", "mobile", "live-receiver.js");
const SOURCE = fs.readFileSync(SOURCE_PATH, "utf8");
const flush = () => new Promise((resolve) => setImmediate(resolve));

function makeHarness({playRejects = null} = {}) {
  const clock = {now: 1000, seq: 0, timers: new Map()};
  const addTimer = (fn, ms, repeat) => {
    const id = ++clock.seq;
    clock.timers.set(id, {fn, at: clock.now + ms, ms, repeat});
    return id;
  };
  const ctx = {
    setTimeout: (fn, ms) => addTimer(fn, ms || 0, false),
    setInterval: (fn, ms) => addTimer(fn, ms, true),
    clearTimeout: (id) => clock.timers.delete(id),
    clearInterval: (id) => clock.timers.delete(id),
    Date: {now: () => clock.now},
  };
  async function advance(ms) {
    const end = clock.now + ms;
    for (;;) {
      let nextId = null, next = null;
      for (const [id, t] of clock.timers) if (t.at <= end && (!next || t.at < next.at)) { nextId = id; next = t; }
      if (!next) break;
      clock.now = next.at;
      if (next.repeat) next.at += next.ms; else clock.timers.delete(nextId);
      next.fn();
      await flush();
    }
    clock.now = end;
    await flush();
  }

  const listeners = (obj) => {
    obj._l = {};
    obj.addEventListener = (n, f) => { (obj._l[n] = obj._l[n] || []).push(f); };
    obj.fire = (n, ev) => (obj._l[n] || []).slice().map((f) => f(ev));
  };
  const el = () => { const e = {dataset: {}, textContent: "", hidden: false}; listeners(e); return e; };
  const video = el();
  Object.assign(video, {paused: true, ended: false, readyState: 0, videoWidth: 0, videoHeight: 0,
    currentTime: 0, srcObject: null, muted: true, frames: 0});
  video.play = async () => {
    if (playRejects) { const e = new Error("blocked"); e.name = playRejects; throw e; }
    video.paused = false; video.readyState = 4; video.fire("playing");
  };
  video.pause = () => { video.paused = true; };
  video.getVideoPlaybackQuality = () => ({totalVideoFrames: video.frames});
  const overlay = el(), statusEl = el(), unmute = el(), diagEl = el();
  unmute.hidden = true;
  const body = {dataset: {live: "0"}};

  const sockets = [];
  class FakeWebSocket {
    constructor(url) { this.url = url; this.readyState = 0; this.sent = []; this.closed = false; listeners(this); sockets.push(this); }
    send(data) { this.sent.push(data); }
    close(code = 1000) { this.closed = true; this.readyState = 3; this.fire("close", {code}); }
    open() { this.readyState = 1; this.fire("open"); }
    async deliver(obj) { await Promise.all(this.fire("message", {data: JSON.stringify(obj)})); await flush(); }
  }
  FakeWebSocket.OPEN = 1;

  const peers = [];
  class FakeRTCPeerConnection {
    constructor(cfg) {
      this.cfg = cfg; this.connectionState = "new"; this.iceConnectionState = "new";
      this.remoteDescription = null; this.localDescription = null; this.closed = false;
      this.failRemote = FakeRTCPeerConnection.failRemote;
      this.added = [];
      peers.push(this);
    }
    async setRemoteDescription(d) { if (this.failRemote) throw this.failRemote; this.remoteDescription = d; }
    async createAnswer() { return {type: "answer", sdp: "v=0\r\nanswer"}; }
    async setLocalDescription(d) { this.localDescription = d; }
    async addIceCandidate(c) { this.added.push(c); }
    close() { this.closed = true; }
    setState(conn, ice) {
      if (ice) { this.iceConnectionState = ice; if (this.oniceconnectionstatechange) this.oniceconnectionstatechange(); }
      if (conn) { this.connectionState = conn; if (this.onconnectionstatechange) this.onconnectionstatechange(); }
    }
    deliverTrack(width = 640, height = 360) {
      const stream = {id: "s"};
      video.videoWidth = width; video.videoHeight = height; // metadata is known once the stream attaches
      this.ontrack({streams: [stream], track: {addEventListener() {}}});
    }
  }
  FakeRTCPeerConnection.failRemote = null;

  const warnings = [];
  const document = {
    body,
    getElementById: (id) => ({"celia-video": video, overlay, status: statusEl, unmute, diag: diagEl}[id] || null),
  };
  const win = {}; listeners(win);
  Object.assign(ctx, {
    document, window: win, WebSocket: FakeWebSocket, RTCPeerConnection: FakeRTCPeerConnection,
    MediaStream: function () {}, URLSearchParams,
    location: {protocol: "http:", host: "127.0.0.1:12393", search: ""},
    console: {warn: (...a) => warnings.push(a.map(String).join(" ")), log() {}, error() {}},
  });
  vm.createContext(ctx);
  vm.runInContext(SOURCE, ctx, {filename: SOURCE_PATH});

  async function negotiate({width = 640, height = 360} = {}) {
    const ws = sockets[sockets.length - 1];
    if (ws.readyState !== 1) ws.open();
    await ws.deliver({type: "ready", online: true});
    await ws.deliver({type: "signal", data: {type: "offer", sdp: "v=0\r\no=- 1 1 IN IP4 10.0.2.15\r\n"}});
    const pcn = peers[peers.length - 1];
    pcn.setState("connected", "connected");
    pcn.deliverTrack(width, height);
    await flush();
    return {ws, pc: pcn};
  }
  return {ctx, clock, advance, video, body, overlay, statusEl, unmute, diagEl, sockets, peers,
    warnings, FakeRTCPeerConnection, negotiate, flush};
}

test("diagnostic DOM exposes peer/ice/size/frames and no signaling payloads", async () => {
  const h = makeHarness();
  await h.negotiate();
  h.video.frames = 5; h.video.currentTime = 0.2;
  await h.advance(1000);
  const d = h.diagEl.dataset;
  assert.equal(d.pc, "connected");
  assert.equal(d.ice, "connected");
  assert.equal(d.ws, "open");
  assert.equal(d.sig, "answer-enviada");
  assert.equal(d.size, "640x360");
  assert.equal(d.frames, "5");
  assert.equal(d.advancing, "1");
  assert.equal(h.body.dataset.live, "1");
  const dump = JSON.stringify(d) + h.diagEl.textContent;
  assert.doesNotMatch(dump, /10\.0\.2\.15|v=0|candidate:/);
});

test("video element with 0x0 dimensions is not reported as live (the no-video symptom)", async () => {
  const h = makeHarness();
  await h.negotiate({width: 0, height: 0});
  h.video.fire("loadedmetadata");
  assert.equal(h.body.dataset.live, "0");
  assert.equal(h.diagEl.dataset.size, "0x0");
});

test("negotiation failure records name/message only, redacts SDP, and recycles the WebSocket", async () => {
  const h = makeHarness();
  const err = new Error("Failed to set remote offer sdp: Invalid line m=video 9 UDP/TLS candidate:1 ufrag abcd 192.168.1.50");
  err.name = "InvalidAccessError";
  h.FakeRTCPeerConnection.failRemote = err;
  const ws = h.sockets[0]; ws.open();
  await ws.deliver({type: "ready", online: true});
  await ws.deliver({type: "signal", data: {type: "offer", sdp: "v=0\r\na=ice-ufrag:SECRETUFRAG\r\n"}});
  assert.ok(ws.closed, "WebSocket closed to force a fresh viewer");
  const dump = h.diagEl.textContent + JSON.stringify(h.diagEl.dataset) + h.warnings.join("|");
  assert.match(dump, /InvalidAccessError/);
  assert.match(dump, /Failed to set remote offer sdp/);
  assert.doesNotMatch(dump, /SECRETUFRAG|192\.168|m=video|candidate:1|ufrag abcd|v=0/);
  assert.equal(h.sockets.length, 1);
  await h.advance(1000);
  assert.equal(h.sockets.length, 2, "existing reconnect path created a new viewer");
});

test("failed peer closes the WebSocket and reconnects fresh; transient disconnected does not", async () => {
  const h = makeHarness();
  let {ws, pc} = await h.negotiate();
  pc.setState("disconnected");
  await h.advance(2000);
  pc.setState("connected");           // transient: recovered inside the grace window
  for (let i = 0; i < 10; i++) { h.video.frames += 25; h.video.currentTime += 1; await h.advance(1000); }
  assert.equal(ws.closed, false, "transient disconnect must not recycle the socket");

  // Browsers do not guarantee the order of iceconnectionstatechange vs connectionstatechange, and
  // the harness fires ICE first: the receiver must recover on whichever "failed" signal arrives
  // first, exactly once (the teardown detaches the other handler), with a non-empty reason.
  pc.setState("failed", "failed");
  assert.ok(ws.closed);
  assert.match(h.diagEl.dataset.recovery, /^(?:peer|ice)-failed\[pc=connected\/ice=failed\]$/);
  assert.equal(h.diagEl.dataset.recoveries, "1/6");
  await h.advance(1000);
  assert.equal(h.sockets.length, 2);
  assert.equal(h.peers[0].closed, true);
});

test("peer-failed arriving before the ICE signal is recovered once with the peer reason", async () => {
  const h = makeHarness();
  const {ws, pc} = await h.negotiate();
  pc.setState("failed");             // connectionState first, ICE never reports
  assert.ok(ws.closed);
  assert.match(h.diagEl.dataset.recovery, /^peer-failed\[pc=failed\/ice=connected\]$/);
  assert.equal(h.diagEl.dataset.recoveries, "1/6");
  pc.setState(null, "failed");       // late ICE event from the torn-down peer must be ignored
  assert.equal(h.diagEl.dataset.recoveries, "1/6");
  await h.advance(1000);
  assert.equal(h.sockets.length, 2);
});

test("persistent disconnected recovers after the grace period", async () => {
  const h = makeHarness();
  const {ws, pc} = await h.negotiate();
  pc.setState("disconnected");
  await h.advance(3000);
  assert.equal(ws.closed, false);
  await h.advance(1500);
  assert.ok(ws.closed);
  assert.match(h.diagEl.dataset.recovery, /peer-disconnected/);
});

test("media timeout without video recycles the socket; autoplay block never does", async () => {
  const noVideo = makeHarness();
  const a = noVideo.sockets[0]; a.open();
  await a.deliver({type: "signal", data: {type: "offer", sdp: "x"}});
  noVideo.peers[0].setState("connected", "connected"); // connected but no track/video ever
  await noVideo.advance(13000);
  assert.ok(a.closed, "no video within the media timeout => fresh viewer");
  assert.match(noVideo.diagEl.dataset.recovery, /media-timeout/);

  const blocked = makeHarness({playRejects: "NotAllowedError"});
  const {ws} = await blocked.negotiate();
  assert.equal(blocked.diagEl.dataset.play, "bloqueado");
  assert.equal(blocked.unmute.hidden, false);
  await blocked.advance(120000);
  assert.equal(ws.closed, false, "autoplay block must not trigger reconnect");
  assert.equal(blocked.sockets.length, 1);
  assert.equal(blocked.diagEl.dataset.recoveries, "0/6");
});

test("non-autoplay play() errors are recoverable and recorded as such", async () => {
  const h = makeHarness({playRejects: "NotSupportedError"});
  const {ws} = await h.negotiate();
  assert.equal(h.diagEl.dataset.play, "erro:NotSupportedError");
  assert.equal(h.unmute.hidden, true);
  await h.advance(13000);
  assert.ok(ws.closed);
});

test("recovery is bounded: a permanently failing viewer stops after MAX recoveries", async () => {
  const h = makeHarness();
  h.FakeRTCPeerConnection.failRemote = Object.assign(new Error("boom"), {name: "OperationError"});
  for (let i = 0; i < 40; i++) {
    const ws = h.sockets[h.sockets.length - 1];
    if (ws.readyState !== 1 && !ws.closed) ws.open();
    if (!ws.closed) {
      await ws.deliver({type: "signal", data: {type: "offer", sdp: "x"}});
    }
    await h.advance(9000);
  }
  // 1 initial viewer + at most 6 forced fresh viewers.
  assert.ok(h.sockets.length <= 7, "sockets=" + h.sockets.length);
  assert.equal(h.diagEl.dataset.recoveries, "6/6");
  assert.match(h.statusEl.textContent, /esgotada/);
  const ws = h.sockets[h.sockets.length - 1];
  assert.equal(ws.closed, false, "after exhaustion the socket is left alone");
});

test("after exhaustion the failure stays visible, a reload is possible, and proven progress clears it", async () => {
  const h = makeHarness();
  h.FakeRTCPeerConnection.failRemote = Object.assign(new Error("boom"), {name: "OperationError"});
  for (let i = 0; i < 20 && !/esgotada/.test(h.statusEl.textContent); i++) {
    const ws = h.sockets[h.sockets.length - 1];
    if (ws.readyState !== 1 && !ws.closed) ws.open();
    if (!ws.closed) await ws.deliver({type: "signal", data: {type: "offer", sdp: "x"}});
    await h.advance(9000);
  }
  assert.match(h.statusEl.textContent, /esgotada/);
  assert.match(h.statusEl.textContent, /Recarregue a página/, "tells the user how to recover");
  assert.equal(h.statusEl.dataset.error, "1");
  assert.equal(h.body.dataset.live, "0", "diagnostic overlay stays visible while not live");
  const failure = h.statusEl.textContent;
  const ws = h.sockets[h.sockets.length - 1];
  const count = h.sockets.length;

  // Routine informational messages from a still-open socket must not erase the failure.
  await ws.deliver({type: "ready", online: true});
  await ws.deliver({type: "studio-online"});
  await ws.deliver({type: "studio-offline"});
  assert.equal(h.statusEl.textContent, failure);
  assert.equal(h.statusEl.dataset.error, "1");
  assert.equal(h.diagEl.dataset.recoveries, "6/6");
  // A plain connection loss keeps the existing reconnect path (not a forced cycle) and the text.
  ws.close(1006);
  assert.equal(h.statusEl.textContent, failure);
  await h.advance(8000);
  assert.equal(h.sockets.length, count + 1);
  assert.equal(h.diagEl.dataset.recoveries, "6/6", "no extra forced cycles after the cap");

  // A terminal access denial is a different failure and may replace the text.
  h.sockets[h.sockets.length - 1].close(4403);
  assert.match(h.statusEl.textContent, /Acesso negado/);

  // The user can always reload: pagehide tears everything down and schedules nothing,
  // and a fresh page instance starts with a full budget.
  const last = makeHarness();
  last.ctx.window.fire("pagehide");
  await last.advance(30000);
  assert.equal(last.sockets.length, 1, "no reconnect after pagehide");
  assert.equal(makeHarness().diagEl.dataset.recoveries, "0/6");
  assert.doesNotMatch(SOURCE, /beforeunload|onbeforeunload/);
});

test("proven video progress after exhaustion clears the failed state", async () => {
  const h = makeHarness();
  h.FakeRTCPeerConnection.failRemote = Object.assign(new Error("boom"), {name: "OperationError"});
  for (let i = 0; i < 20 && !/esgotada/.test(h.statusEl.textContent); i++) {
    const ws = h.sockets[h.sockets.length - 1];
    if (ws.readyState !== 1 && !ws.closed) ws.open();
    if (!ws.closed) await ws.deliver({type: "signal", data: {type: "offer", sdp: "x"}});
    await h.advance(9000);
  }
  assert.match(h.statusEl.textContent, /esgotada/);
  h.FakeRTCPeerConnection.failRemote = null;        // studio is healthy again and re-offers
  const ws = h.sockets[h.sockets.length - 1];
  await ws.deliver({type: "signal", data: {type: "offer", sdp: "v=0\r\n"}});
  const pcn = h.peers[h.peers.length - 1];
  pcn.setState("connected", "connected"); pcn.deliverTrack();
  await h.flush();
  for (let i = 0; i < 4; i++) { h.video.frames += 25; h.video.currentTime += 1; await h.advance(1000); }
  assert.equal(h.body.dataset.live, "1");
  assert.equal(h.diagEl.dataset.recoveries, "0/6");
  await ws.deliver({type: "ready", online: true});  // failure flag is cleared: normal messages show again
  assert.doesNotMatch(h.statusEl.textContent, /esgotada/);
});

test("stalled frames recover; advancing frames do not; progress resets the budget", async () => {
  const healthy = makeHarness();
  const ok = await healthy.negotiate();
  for (let i = 0; i < 30; i++) {
    healthy.video.frames += 25; healthy.video.currentTime += 1;
    await healthy.advance(1000);
  }
  assert.equal(ok.ws.closed, false);
  assert.equal(healthy.diagEl.dataset.advancing, "1");

  const stalled = makeHarness();
  const st = await stalled.negotiate();
  stalled.video.frames = 10; stalled.video.currentTime = 1;
  await stalled.advance(1000);
  assert.equal(stalled.diagEl.dataset.advancing, "1");
  await stalled.advance(12000);   // frozen: neither frames nor clock move
  assert.ok(st.ws.closed);
  assert.match(stalled.diagEl.dataset.recovery, /frames-stalled/);

  // after a fresh viewer produces real progress the budget is restored
  await stalled.advance(1000);
  const next = await stalled.negotiate();
  for (let i = 0; i < 4; i++) { stalled.video.frames += 25; stalled.video.currentTime += 1; await stalled.advance(1000); }
  assert.equal(stalled.diagEl.dataset.recoveries, "0/6");
  assert.equal(next.ws.closed, false);
});

test("receiver stays receive-only: no device APIs, no outgoing media, original audio", () => {
  assert.doesNotMatch(SOURCE, /getUserMedia|getDisplayMedia|enumerateDevices|addTrack|addTransceiver|createDataChannel/);
  assert.match(SOURCE, /video\.muted = false/);
  assert.doesNotMatch(SOURCE, /SpeechSynthesis|speechSynthesis/);
});
