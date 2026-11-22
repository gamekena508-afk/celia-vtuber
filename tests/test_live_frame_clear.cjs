"use strict";
// Regression for frontend/celia-live-frame-clear.js using a fake WebGL context.
// Loads the real guard first (chaining), then the helper, in a vm sandbox.
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..", "frontend");
const guardSrc = fs.readFileSync(path.join(root, "celia-live-output-guard.js"), "utf8");
const clearSrc = fs.readFileSync(path.join(root, "celia-live-frame-clear.js"), "utf8");
const indexHtml = fs.readFileSync(path.join(root, "index.html"), "utf8");

const COLOR = 0x4000, DEPTH = 0x100, FB = 0x8CA6, DRAW_FB = 0x8CA6;

function makeGL() {
  const state = {clearColor: [0, 0, 0, 1], colorMask: [true, true, true, true], fbo: null, drawFbo: null};
  const log = [];
  const gl = {
    COLOR_BUFFER_BIT: COLOR, DEPTH_BUFFER_BIT: DEPTH, FRAMEBUFFER_BINDING: FB, DRAW_FRAMEBUFFER_BINDING: DRAW_FB,
    COLOR_CLEAR_VALUE: 0x0C22, COLOR_WRITEMASK: 0x0C23,
    _state: state, _log: log,
    getParameter(p) {
      if (p === 0x0C22) return state.clearColor.slice();
      if (p === 0x0C23) return state.colorMask.slice();
      if (p === DRAW_FB) return state.drawFbo;
      if (p === FB) return state.fbo;
      return null;
    },
    clearColor(r, g, b, a) { state.clearColor = [r, g, b, a]; log.push(["clearColor", r, g, b, a]); },
    colorMask(r, g, b, a) { state.colorMask = [r, g, b, a]; log.push(["colorMask", r, g, b, a]); },
    clear(mask) {
      log.push(["native-clear", mask, state.clearColor.slice(), state.colorMask.slice()]);
      if (gl._throwNext) { gl._throwNext = false; throw new Error("boom"); }
    },
  };
  return gl;
}

function setup({search = "?liveOutput=1", withGuard = true, withHelper = true, canvasId = "canvas"} = {}) {
  const gls = new Map();
  const nativeCalls = [];
  const HTMLCanvasElement = function () {};
  HTMLCanvasElement.prototype.getContext = function nativeGetContext(type, attrs) {
    nativeCalls.push({type, attrs: attrs ? Object.assign({}, attrs) : attrs, id: this.id});
    if (!gls.has(this)) gls.set(this, makeGL());
    return gls.get(this);
  };
  const win = {location: {search}};
  const sandbox = {window: win, navigator: {}, HTMLCanvasElement, URLSearchParams, DOMException: Error, WeakSet, Object, Set, Array, Error};
  win.window = win;
  const ctx = vm.createContext(sandbox);
  if (withGuard) vm.runInContext(guardSrc, ctx);
  if (withHelper) vm.runInContext(clearSrc, ctx);
  const canvas = new HTMLCanvasElement();
  canvas.id = canvasId;
  return {HTMLCanvasElement, canvas, nativeCalls, sandbox, ctx};
}

// 1) Transparent colour clear for the stage, state restored exactly afterwards.
{
  const t = setup();
  const gl = t.canvas.getContext("webgl2");
  gl._state.clearColor = [0.1, 0.2, 0.3, 0.4];
  gl._state.colorMask = [true, false, true, true];
  gl._log.length = 0;
  gl.clear(gl.DEPTH_BUFFER_BIT);
  const native = gl._log.find((e) => e[0] === "native-clear");
  assert.equal(native[1], COLOR | DEPTH, "native clear received colour|depth");
  assert.deepEqual(native[2], [0, 0, 0, 0], "colour cleared to transparent during the call");
  assert.deepEqual(native[3], [true, true, true, true], "colour writes forced on during the call");
  assert.deepEqual(gl._state.clearColor, [0.1, 0.2, 0.3, 0.4], "original clearColor restored");
  assert.deepEqual(gl._state.colorMask, [true, false, true, true], "original colour write mask restored");
}

// 2) Exception in the native clear still restores state.
{
  const t = setup();
  const gl = t.canvas.getContext("webgl2");
  gl._state.clearColor = [0, 0, 0, 1];
  gl._throwNext = true;
  assert.throws(() => gl.clear(gl.DEPTH_BUFFER_BIT), /boom/);
  assert.deepEqual(gl._state.clearColor, [0, 0, 0, 1], "clearColor restored after throw");
  assert.deepEqual(gl._state.colorMask, [true, true, true, true], "colorMask restored after throw");
}

// 3) Other masks pass through untouched (colour, colour|depth, depth|stencil).
for (const mask of [COLOR, COLOR | DEPTH, DEPTH | 0x400]) {
  const t = setup();
  const gl = t.canvas.getContext("webgl2");
  gl._state.clearColor = [0.5, 0.5, 0.5, 1];
  gl._log.length = 0;
  gl.clear(mask);
  assert.deepEqual(gl._log, [["native-clear", mask, [0.5, 0.5, 0.5, 1], [true, true, true, true]]], "mask " + mask + " untouched");
}

// 4) Depth-only clear into an offscreen framebuffer is never changed.
{
  const t = setup();
  const gl = t.canvas.getContext("webgl2");
  gl._state.clearColor = [0.5, 0.5, 0.5, 1];
  gl._state.drawFbo = {offscreen: true};
  gl._log.length = 0;
  gl.clear(gl.DEPTH_BUFFER_BIT);
  assert.deepEqual(gl._log, [["native-clear", DEPTH, [0.5, 0.5, 0.5, 1], [true, true, true, true]]], "offscreen draw FB untouched");
}

// 5) Read-side FRAMEBUFFER_BINDING alone does not disable the stage clear (draw is default).
{
  const t = setup();
  const gl = t.canvas.getContext("webgl2");
  gl._state.fbo = {readOnly: true};
  gl._state.drawFbo = null;
  gl._log.length = 0;
  gl.clear(gl.DEPTH_BUFFER_BIT);
  assert.equal(gl._log.find((e) => e[0] === "native-clear")[2][3], 0, "default draw FB still gets the transparent clear");
}

// 6) Other canvases and non-live contexts are never decorated.
{
  const t = setup({canvasId: "celia-3d-quadro"});
  const gl = t.canvas.getContext("webgl2");
  gl._state.clearColor = [0.5, 0.5, 0.5, 1];
  gl._log.length = 0;
  gl.clear(gl.DEPTH_BUFFER_BIT);
  assert.deepEqual(gl._log, [["native-clear", DEPTH, [0.5, 0.5, 0.5, 1], [true, true, true, true]]], "other canvas untouched");
}
{
  const t = setup({search: "?pet=1"});
  assert.equal(Object.prototype.hasOwnProperty.call(t.HTMLCanvasElement.prototype, "__celiaLiveFrameClear"), false, "non-live: helper not installed");
  const gl = t.canvas.getContext("webgl2");
  gl._state.clearColor = [0.5, 0.5, 0.5, 1];
  gl._log.length = 0;
  gl.clear(gl.DEPTH_BUFFER_BIT);
  assert.deepEqual(gl._log, [["native-clear", DEPTH, [0.5, 0.5, 0.5, 1], [true, true, true, true]]], "non-live clear untouched");
}

// 7) Guard chaining: preserveDrawingBuffer still forced, and the stage context is the same object.
{
  const t = setup();
  const gl1 = t.canvas.getContext("webgl2", {alpha: true});
  assert.equal(t.nativeCalls[0].attrs.preserveDrawingBuffer, true, "guard still forces preserveDrawingBuffer");
  assert.equal(t.nativeCalls[0].attrs.alpha, true, "caller attributes preserved");
}

// 8) Idempotence: repeated getContext and repeated install do not stack wrappers.
{
  const t = setup();
  const gl1 = t.canvas.getContext("webgl2");
  const clearOnce = gl1.clear;
  const gl2 = t.canvas.getContext("webgl2");
  assert.equal(gl1, gl2, "same context object");
  assert.equal(gl2.clear, clearOnce, "clear decorated only once");
  vm.runInContext(clearSrc, t.ctx); // second install: no-op
  const gl3 = t.canvas.getContext("webgl2");
  assert.equal(gl3.clear, clearOnce, "second install does not re-wrap");
  gl3._state.clearColor = [0.3, 0.3, 0.3, 1];
  gl3._log.length = 0;
  gl3.clear(gl3.DEPTH_BUFFER_BIT);
  assert.equal(gl3._log.filter((e) => e[0] === "native-clear").length, 1, "exactly one native clear per call");
  assert.deepEqual(gl3._state.clearColor, [0.3, 0.3, 0.3, 1], "state restored after repeated installs");
}

// 9) Script order in index.html: guard, then this helper, then the chooser.
{
  const pos = (n) => indexHtml.indexOf('<script src="./' + n);
  assert.ok(pos("celia-live-output-guard.js?v=") > 0, "guard tag present");
  assert.ok(pos("celia-live-frame-clear.js?v=") > pos("celia-live-output-guard.js?v="), "helper after guard");
  assert.ok(pos("celia-live-frame-clear.js?v=") < pos("celia-escolha.js"), "helper before chooser");
  assert.doesNotMatch(indexHtml, /celia-live-frame-clear[^>]*(async|defer)/, "helper is synchronous");
}

console.log("test_live_frame_clear: ok");
