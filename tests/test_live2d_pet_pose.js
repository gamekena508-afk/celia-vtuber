"use strict";
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");

class FakeMatrix {
  constructor(scale = 1, x = 0, y = 0) { this.s = scale; this.x = x; this.y = y; }
  getScaleX() { return this.s; }
  getArray() { const a = new Float32Array(16); a[12] = this.x; a[13] = this.y; return a; }
  scale(x, y) { assert.strictEqual(x, y); this.s = x; }
  translateX(x) { this.x = x; }
  translateY(y) { this.y = y; }
}

const store = new Map();
const key = "celia-live2d-body:pet";
store.set(key, JSON.stringify({ version: 1, scale: 1.75, x: 0.6, y: -0.9 }));
let matrix = new FakeMatrix(1, 0.1, 0.2);
const observers = [];
let nextTimer = 1;
const timers = new Map();
global.location = { search: "?pet=1" };
global.localStorage = {
  getItem: (k) => store.has(k) ? store.get(k) : null,
  setItem: (k, v) => store.set(k, String(v)),
};
global.document = {
  body: {}, documentElement: { dataset: {} },
  getElementById: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
};
global.window = global;
global.window.getLAppAdapter = () => ({ getModel: () => ({ _modelMatrix: matrix }) });
global.MutationObserver = class {
  constructor(cb) { this.cb = cb; observers.push(this); }
  observe() {}
};
global.setTimeout = (fn) => { const id = nextTimer++; timers.set(id, fn); return id; };
global.clearTimeout = (id) => timers.delete(id);
global.requestAnimationFrame = (fn) => fn(0);
global.performance = { now: () => 0 };

const src = fs.readFileSync(path.join(__dirname, "../frontend/celia-live2d-body.js"), "utf8");
vm.runInThisContext(src, { filename: "celia-live2d-body.js" });
const body = global.celiaLive2DBody;
function close(a, b) { assert.ok(Math.abs(a - b) < 1e-5, `${a} != ${b}`); }
function assertPose(expected) {
  const p = body.getPose();
  close(p.scale, expected.scale); close(p.x, expected.x); close(p.y, expected.y);
}

assertPose({ scale: 1.75, x: 0.6, y: -0.9 });
body.scaleBy(0.12);
assertPose({ scale: 1.87, x: 0.6, y: -0.9 });
body.moveBy(0.25, -0.1);
assertPose({ scale: 1.87, x: 0.85, y: -1.0 });

for (const [id, fn] of [...timers]) { if (timers.has(id)) { timers.delete(id); fn(); } }
const saved = JSON.parse(store.get(key));
close(saved.scale, 1.87); close(saved.x, 0.85); close(saved.y, -1.0);

matrix = new FakeMatrix(1, 0, 0);
for (const observer of observers) observer.cb([]);
assertPose({ scale: 1.87, x: 0.85, y: -1.0 });
console.log("live2d pet pose absolute move/zoom + autosave + remount restore: OK");
