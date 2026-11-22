"use strict";
// liveOutput=1 must refuse every physical device/screen capture before the native call.
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..", "frontend");
const micSrc = fs.readFileSync(path.join(root, "mic_toggle.js"), "utf8");
const guardSrc = fs.readFileSync(path.join(root, "celia-live-output-guard.js"), "utf8");

function load(search, {guard = true} = {}) {
  const calls = {gum: [], gdm: []};
  class FakeWS { constructor() {} addEventListener() {} send() {} }
  const fakeStream = {getAudioTracks: () => []};
  const mediaDevices = {
    getUserMedia: (c) => { calls.gum.push(c); return Promise.resolve(fakeStream); },
    getDisplayMedia: (c) => { calls.gdm.push(c); return Promise.resolve(fakeStream); },
  };
  const win = {location: {search}, WebSocket: FakeWS};
  const ctxCalls = [];
  class FakeCanvas {}
  FakeCanvas.prototype.getContext = function (type, attrs) { ctxCalls.push({type, attrs}); return {type}; };
  const sandbox = {
    HTMLCanvasElement: FakeCanvas,
    window: win, navigator: {mediaDevices}, document: {addEventListener() {}, createElement() { return {style: {}}; }, body: {appendChild() {}}},
    console: {log() {}, warn() {}}, URLSearchParams, DOMException, Promise, setTimeout, clearTimeout, JSON, Set,
  };
  win.window = win;
  const ctx = vm.createContext(sandbox);
  if (guard) vm.runInContext(guardSrc, ctx);   // loaded before celia-escolha.js and mic_toggle.js
  vm.runInContext(micSrc, ctx);
  return {calls, md: sandbox.navigator.mediaDevices, ctxCalls, canvas: new FakeCanvas()};
}

const physical = [
  {video: true}, {audio: true}, {video: true, audio: true},
  {video: {deviceId: {exact: "cam0"}, width: 1280}, audio: {echoCancellation: false}},
  {audio: {deviceId: "default"}}, {}, undefined,
];

(async () => {
  // Live output: nothing reaches native.
  {
    const {calls, md} = load("?pet=1&liveOutput=1");
    for (const c of physical) {
      await assert.rejects(() => md.getUserMedia(c), (e) => e.name === "NotAllowedError");
    }
    await assert.rejects(() => md.getDisplayMedia({video: true}), (e) => e.name === "NotAllowedError");
    await assert.rejects(() => md.getDisplayMedia(), (e) => e.name === "NotAllowedError");
    assert.equal(calls.gum.length, 0, "native getUserMedia must not run");
    assert.equal(calls.gdm.length, 0, "native getDisplayMedia must not run");
  }
  // Ordinary mode: getUserMedia still reaches native; getDisplayMedia untouched.
  {
    const {calls, md} = load("?pet=1");
    const s = await md.getUserMedia({audio: true});
    assert.ok(s);
    assert.equal(calls.gum.length, 1);
    assert.deepEqual(calls.gum[0], {audio: true});
    await md.getDisplayMedia({video: true});
    assert.equal(calls.gdm.length, 1);
  }
  // liveOutput must be exactly "1".
  {
    const {calls, md} = load("?liveOutput=0");
    await md.getUserMedia({video: true});
    assert.equal(calls.gum.length, 1);
  }
  // Canvas: liveOutput forces preserveDrawingBuffer for WebGL only, no device calls.
  {
    const {calls, ctxCalls, canvas} = load("?pet=1&liveOutput=1");
    canvas.getContext("webgl2", {alpha: false, preserveDrawingBuffer: false, antialias: false});
    canvas.getContext("webgl");
    canvas.getContext("experimental-webgl", null);
    canvas.getContext("2d", {willReadFrequently: true});
    canvas.getContext("2d");
    assert.equal(ctxCalls.length, 5);
    for (const c of ctxCalls.slice(0, 3)) assert.equal(c.attrs.preserveDrawingBuffer, true, c.type);
    assert.equal(ctxCalls[0].attrs.alpha, false, "other attributes preserved");
    assert.equal(ctxCalls[0].attrs.antialias, false);
    assert.deepEqual(ctxCalls[3].attrs, {willReadFrequently: true}, "2d untouched");
    assert.equal(ctxCalls[4].attrs, undefined, "2d without attrs untouched");
    assert.equal(calls.gum.length + calls.gdm.length, 0, "no device calls");
  }
  // Ordinary mode and liveOutput=0: getContext is not wrapped.
  for (const q of ["?pet=1", "?liveOutput=0"]) {
    const {ctxCalls, canvas} = load(q);
    const a = {preserveDrawingBuffer: false};
    canvas.getContext("webgl2", a);
    assert.equal(ctxCalls[0].attrs, a, "same attrs object");
    assert.equal(a.preserveDrawingBuffer, false);
  }
  // The guard is the only owner: mic_toggle alone no longer denies (control).
  {
    const {calls, md} = load("?pet=1&liveOutput=1", {guard: false});
    await md.getUserMedia({audio: true});
    assert.equal(calls.gum.length, 1, "without the guard nothing denies");
  }
  // Index order: guard before celia-escolha.js, which is before mic_toggle.js.
  {
    const idx = fs.readFileSync(path.join(root, "index.html"), "utf8");
    const pos = (n) => idx.indexOf('<script src="./' + n);
    assert.ok(pos("celia-live-output-guard.js?v=") > 0, "guard tag present");
    assert.ok(pos("celia-live-output-guard.js") < pos("celia-escolha.js"), "guard before chooser");
    assert.ok(pos("celia-escolha.js") < pos("mic_toggle.js"));
    assert.doesNotMatch(idx, /celia-live-output-guard[^>]*(async|defer)/);
    assert.doesNotMatch(micSrc, /liveOutput/, "no duplicated branch in mic_toggle");
  }
  // Studio wiring.
  const js = fs.readFileSync(path.join(root, "mobile", "studio.js"), "utf8");
  const html = fs.readFileSync(path.join(root, "mobile", "studio.html"), "utf8");
  assert.match(js, /searchParams\.set\("pet", "1"\)/);
  assert.match(js, /searchParams\.set\("liveOutput", "1"\)/);
  assert.match(html, /src="\/\?pet=1&liveOutput=1&/);
  console.log("test_live_studio_devices: ok");
})().catch((e) => { console.error(e); process.exit(1); });
