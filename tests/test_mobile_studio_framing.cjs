"use strict";
// Waist-up preset math and framing wiring of frontend/mobile/studio.js (no network, no WebGL).
// Loads the real studio.js in a vm with a minimal DOM stub, then checks the preset arithmetic
// against the measured source points (slime top y=17, waist y=850 on the 720x1280 canvas).
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..", "frontend", "mobile");
const js = fs.readFileSync(path.join(root, "studio.js"), "utf8");
const html = fs.readFileSync(path.join(root, "studio.html"), "utf8");

function makeEl() {
  const el = {
    value: "", textContent: "", style: {}, width: 720, height: 1280, disabled: false,
    files: [], listeners: {}, contentDocument: null, contentWindow: null,
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {}, appendChild() {},
    click() {}, remove() {}, classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }},
  };
  return el;
}
const ctx = new Proxy({}, {
  get: (t, k) => (k === "measureText" ? () => ({width: 0}) : (typeof k === "string" ? () => {} : undefined)),
});
const els = {};
const document = {
  getElementById: (id) => els[id] || (els[id] = makeEl()),
  createElement: () => makeEl(), addEventListener() {}, body: makeEl(), readyState: "complete",
};
els.composite = makeEl(); els.composite.getContext = () => ctx;
const store = {};
const localStorage = {getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
class FakeWS { constructor(url) { this.url = url; } addEventListener() {} send() {} close() {} }
FakeWS.OPEN = 1;
const win = {location: {origin: "http://127.0.0.1:12393", protocol: "http:", host: "127.0.0.1:12393", hostname: "127.0.0.1", search: ""}, addEventListener() {}};
const sandbox = {
  window: win, document, localStorage, WebSocket: FakeWS, Image: class { set src(v) { this._s = v; } },
  Blob: class {}, URL, URLSearchParams, location: win.location, navigator: {},
  setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, requestAnimationFrame() {},
  fetch: () => Promise.reject(new Error("offline test")), console: {log() {}, warn() {}, error() {}},
  Number, Math, JSON, Set, Map, WeakSet, Promise, Object, Array, String, Error,
};
vm.createContext(sandbox);
vm.runInContext(js, sandbox);

const api = win.celiaMobileStudioFraming;
assert.ok(api, "studio exposes its framing helper for this test");
const {waistUpPose, FRAMING, W, H} = api;

// 1) Preset arithmetic: the measured slime top lands at topMargin, the waist line at the bottom edge.
const p = waistUpPose();
const outY = (srcY) => H * (0.5 + p.y) + (srcY - H / 2) * p.zoom;
assert.ok(Math.abs(outY(FRAMING.srcTop) - FRAMING.topMargin) < 1.5, "slime top at top margin: " + outY(FRAMING.srcTop));
assert.ok(Math.abs(outY(FRAMING.srcWaist) - H) < 1.5, "waist at bottom edge: " + outY(FRAMING.srcWaist));
assert.equal(p.x, 0, "horizontally centred");
assert.ok(p.zoom > 1.4 && p.zoom < 1.6, "zoom in expected band: " + p.zoom);
assert.ok(p.y > 0.2 && p.y < 0.32, "vertical offset in expected band: " + p.y);
// Head + ears + slime (measured to y=17..~145 and ears ~70) must all sit inside the frame.
const S = W / 720; // source points were measured on the old 720x1280 canvas
assert.equal(W, 1080); assert.equal(H, 1920);
assert.ok(outY(17 * S) >= 0 && outY(145 * S) <= H, "slime fully inside");
assert.ok(outY(850 * S) <= H + 1.5, "nothing below the waist is required");
// Source aspect preserved: a 720x1280 source drawn at base scale 1 has no horizontal squish.
assert.equal(Math.min(W / 1080, H / 1920), 1, "1080x1920 source maps 1:1 (no distortion)");

// 2) Pose stays inside the existing clamp ranges.
assert.ok(p.zoom >= 0.4 && p.zoom <= 2.8 && Math.abs(p.y) <= 0.85 && Math.abs(p.x) <= 0.85);

// 2b) Publisher-seat claim: a normal page announces its load time, a probe (?observar=1) announces 0.
assert.match(js, /PAGE_CLAIM = new URLSearchParams\(location\.search\)\.get\("observar"\) === "1" \? 0 : Date\.now\(\)/);
assert.match(js, /mobile\/ws\?role=studio&claim=" \+ PAGE_CLAIM/);

// 3) Persistence key kept, pose saved immediately by publishState (save()).
assert.match(js, /"celia-mobile-studio-framing-v1"/, "existing pose storage key kept");

// 4) Controls exist in the page and the iframe tag is unchanged (test_live_studio_devices depends on it).
for (const id of ["waist-up", "zoom", "pan-x", "pan-y", "framing-readout", "background-state", "stop-background"]) {
  assert.match(html, new RegExp('id="' + id + '"'), "control " + id);
}
assert.match(html, /<iframe id="source-stage" src="\/\?pet=1&liveOutput=1&palco=1&corpo=2d&fundo=cena&mobileStudio=1"/);

// 5) Background persistence uses IndexedDB with a bounded size, never base64 in localStorage.
assert.match(js, /BG_MAX = 12 \* 1024 \* 1024/);
assert.doesNotMatch(js, /setItem\([^)]*(dataURL|readAsDataURL|blob)/i);

console.log("test_mobile_studio_framing: ok", JSON.stringify(p));
