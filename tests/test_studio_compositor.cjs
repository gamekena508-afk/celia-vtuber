"use strict";
// Studio compositor: destination math vs the 2D transform, mode selection and fallback.
// Loads the real studio.js in a vm with a DOM stub. No WebGL draw is executed here: the GPU
// path is measured by Max in the real A/B, not by this file.
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "..", "frontend", "mobile", "studio.js"), "utf8");
const W = 720, H = 1280;

function makeCtx() {
  return new Proxy({}, {
    get: (t, k) => (k === "measureText" ? () => ({width: 0}) : (typeof k === "string" ? () => {} : undefined)),
  });
}
function load({search = "", webglNull = false} = {}) {
  const els = {};
  const created = [];
  const ctx2d = makeCtx();
  function makeCanvas(id) {
    const el = {
      id, width: W, height: H, value: "", textContent: "", style: {}, listeners: {}, files: [],
      attrs: {"aria-label": "Prévia da transmissão vertical"},
      getAttribute(n) { return this.attrs[n] ?? null; },
      setAttribute(n, v) { this.attrs[n] = String(v); },
      addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
      replaceWith(next) { this.replaced = next; },
      getContext(type) { el.contextRequests = (el.contextRequests || []).concat([type]); if (type === "2d") return ctx2d; return null; },
      classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }},
      setAttributeNS() {}, appendChild() {}, click() {}, remove() {},
    };
    return el;
  }
  els.composite = makeCanvas("composite");
  if (webglNull) els.composite.getContext = function (type) { this.contextRequests = (this.contextRequests || []).concat([type]); return type === "2d" ? ctx2d : null; };
  const document = {
    getElementById: (id) => els[id] || (els[id] = makeCanvas(id)),
    createElement: (tag) => { const c = makeCanvas(null); c.tag = tag; created.push(c); return c; },
    addEventListener() {}, body: makeCanvas("body"), readyState: "complete",
  };
  const store = {};
  const localStorage = {getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
  class FakeWS { constructor(url) { this.url = url; } addEventListener() {} send() {} close() {} }
  FakeWS.OPEN = 1;
  const win = {location: {origin: "http://127.0.0.1:12393", protocol: "http:", host: "127.0.0.1:12393", hostname: "127.0.0.1", search}, addEventListener() {}};
  const sandbox = {
    window: win, document, localStorage, WebSocket: FakeWS, Image: class { set src(v) { this._s = v; } },
    Blob: class {}, URL, URLSearchParams, location: win.location, navigator: {},
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, requestAnimationFrame() {},
    fetch: () => Promise.reject(new Error("offline test")), console: {log() {}, warn() {}, error() {}},
    Number, Math, JSON, Set, Map, WeakSet, Promise, Object, Array, String, Error, Float32Array,
  };
  vm.createContext(sandbox);
  vm.runInContext(js, sandbox);
  return {api: win.celiaMobileStudioCompositor, els, created};
}

// 1) Default: 2D on the original visible canvas, no replacement, label "2d".
{
  const t = load({search: "?pet=1"});
  assert.equal(t.api.label(), "2d");
  assert.equal(t.els.composite.replaced, undefined, "default keeps the visible canvas");
  assert.deepEqual(t.els.composite.contextRequests, ["2d"], "default asks only for 2d");
}

// 2) ?compositor=webgl where webgl2 is unavailable: visible canvas replaced by a fresh one with the
//    same id/size/label, 2D context taken from the replacement, and the fallback is visible in the label.
{
  const t = load({search: "?compositor=webgl", webglNull: true});
  const old = t.els.composite;
  assert.deepEqual(old.contextRequests, ["webgl2"], "webgl2 was requested first on the visible canvas");
  assert.ok(old.replaced, "visible canvas replaced before 2D");
  const next = old.replaced;
  assert.equal(next.id, "composite");
  assert.equal(next.width, W); assert.equal(next.height, H);
  assert.equal(next.getAttribute("aria-label"), "Prévia da transmissão vertical");
  assert.match(t.api.label(), /^2d \(fallback: /, "fallback is named in the label, not silent");
  assert.equal(t.api.error(), "");
}

// 3) Destination math matches the 2D transform corner by corner (no squish, same placement).
function ref2D(sw, sh, p, a, b) {
  const base = Math.min(W / sw, H / sh);
  const cx = W * (0.5 + p.x), cy = H * (0.5 + p.y);
  return [cx + (a - sw / 2) * base * p.zoom, cy + (b - sh / 2) * base * p.zoom];
}
{
  const t = load();
  const cases = [
    [720, 1280, {x: 0, y: 0, zoom: 1}],
    [720, 1280, {x: 0.1, y: -0.2, zoom: 1.5}],
    [1280, 720, {x: -0.3, y: 0.05, zoom: 0.8}],
    [720, 1280, {x: 0, y: 0.257, zoom: 1.479}],
  ];
  for (const [sw, sh, p] of cases) {
    const r = t.api.layerRect(sw, sh, p, W, H);
    const tl = ref2D(sw, sh, p, 0, 0), br = ref2D(sw, sh, p, sw, sh);
    assert.ok(Math.abs(r[0] - tl[0]) < 1e-9 && Math.abs(r[1] - tl[1]) < 1e-9, "top-left matches 2D for " + JSON.stringify(p));
    assert.ok(Math.abs(r[0] + r[2] - br[0]) < 1e-9 && Math.abs(r[1] + r[3] - br[1]) < 1e-9, "bottom-right matches 2D for " + JSON.stringify(p));
    assert.ok(Math.abs(r[2] / r[3] - sw / sh) < 1e-9, "aspect preserved (no squish) for " + sw + "x" + sh);
  }
}
{
  // cover(): factor = max(W/sw, H/sh), centred; the rect must cover the whole output.
  const t = load();
  const [x, y, w, h] = t.api.coverRect(1100, 800, W, H);
  assert.ok(x <= 0 && y <= 0 && x + w >= W - 1e-9 && y + h >= H - 1e-9, "cover rect covers the frame");
  assert.ok(Math.abs(w / h - 1100 / 800) < 1e-9, "cover keeps the image aspect");
}
{
  const t = load();
  // vm arrays come from another realm: compare serialized values, not prototypes.
  assert.equal(JSON.stringify(Array.from(t.api.parseHex("#221135"), (v) => Math.round(v * 255))), JSON.stringify([0x22, 0x11, 0x35]));
  assert.equal(JSON.stringify(Array.from(t.api.parseHex("#000000"))), JSON.stringify([0, 0, 0]));
}

console.log("test_studio_compositor: ok");
