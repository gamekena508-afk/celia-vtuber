"use strict";

const assert = require("node:assert");
const path = require("node:path");

const timers = [];
const animationFrames = new Map();
let nextAnimationFrame = 1;
global.setTimeout = (callback) => {
  timers.push(callback);
  return callback;
};
global.clearTimeout = () => {};
global.requestAnimationFrame = (callback) => {
  const id = nextAnimationFrame++;
  animationFrames.set(id, callback);
  return id;
};
global.cancelAnimationFrame = (id) => animationFrames.delete(id);

function element() {
  const attributes = new Map();
  const classes = new Set();
  const item = {
    style: {},
    dataset: {},
    hidden: false,
    children: [],
    classList: {
      add: (...items) => items.forEach((item) => classes.add(item)),
      remove: (...items) => items.forEach((item) => classes.delete(item)),
      contains: (item) => classes.has(item),
    },
    setAttribute: (key, value) => attributes.set(key, String(value)),
    getAttribute: (key) => attributes.get(key) || null,
    removeAttribute: (key) => attributes.delete(key),
    appendChild(child) { this.children.push(child); return child; },
  };
  Object.defineProperty(item, "src", {
    get: () => attributes.get("src") || "",
    set: (value) => attributes.set("src", String(value)),
  });
  return item;
}

const byId = new Map();
const head = element();
head.appendChild = (child) => { byId.set(child.id, child); return child; };
const body = element();
body.appendChild = (child) => { byId.set(child.id, child); body.children.push(child); return child; };
const canvas = element();
canvas.id = "canvas";
canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1920, height: 1080 });
byId.set("canvas", canvas);
const wrapper = element();
wrapper.id = "live2d-internal-wrapper";
wrapper.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1920, height: 1080 });
byId.set("live2d-internal-wrapper", wrapper);
global.document = {
  head,
  body,
  createElement: element,
  getElementById: (id) => byId.get(id) || null,
};

class FakeWebSocket {}

const windowListeners = new Map();
let pose = { scale: 0.8, x: 0, y: 0 };
const core = {
  getDrawableCount: () => 1,
  getDrawableVertexPositions: () => new Float32Array([
    -0.40, -0.96,
     0.40, -0.96,
     0.40,  0.98,
    -0.40,  0.98,
  ]),
};
const matrix = {
  transformX: (x) => pose.scale * x + pose.x,
  transformY: (y) => pose.scale * y + pose.y,
  getScaleX: () => pose.scale,
  getScaleY: () => pose.scale,
};
const lappModel = { _model: core, _modelMatrix: matrix };
global.window = {
  WebSocket: FakeWebSocket,
  location: { href: "http://127.0.0.1:12393/?palco=1&pet=1", search: "?palco=1&pet=1" },
  innerWidth: 1920,
  innerHeight: 1080,
  getLAppAdapter: () => ({ getModel: () => lappModel }),
  addEventListener(type, listener) {
    const items = windowListeners.get(type) || [];
    items.push(listener);
    windowListeners.set(type, items);
  },
  dispatchEvent(event) {
    for (const listener of windowListeners.get(event.type) || []) listener(event);
  },
};
require(path.resolve(__dirname, "..", "frontend", "celia-confusao.js"));
assert.strictEqual(window.WebSocket, FakeWebSocket, "confusion accessory must not wrap WebSocket");

window.dispatchEvent({
  type: "celia-emotion",
  detail: { emotion: "confused", source: "turn_detector", metadata: { turn_id: "turn-1" } },
});
let state = window.celiaConfusao.state();
assert.equal(state.visible, true);
assert.equal(state.tracking, true);
assert.match(state.source, /entrada\.gif$/);
assert.ok(state.anchor && state.anchor.left < state.anchor.centerX, "accessory must stay left of the Live2D anchor");
assert.ok(state.anchor.modelX < -0.30, "confusion mark anchor must stay on the left side of the head");
assert.ok(state.anchor.modelY > 0.70, "confusion mark must stay in the upper/head region");

const firstAnchor = state.anchor;
pose = { scale: 0.8, x: 0.35, y: 0.10 };
window.celiaConfusao.updateAnchor();
state = window.celiaConfusao.state();
assert.ok(state.anchor.centerX > firstAnchor.centerX, "anchor must follow horizontal matrix translation");
assert.ok(state.anchor.centerY < firstAnchor.centerY, "anchor must follow vertical matrix translation");

pose = { scale: 1.2, x: 0.35, y: 0.10 };
window.celiaConfusao.updateAnchor();
assert.ok(window.celiaConfusao.state().anchor.size > firstAnchor.size, "accessory size must follow Live2D matrix scale");

pose = { scale: 1.119999885559082, x: 1.4981483221054077, y: -1.101481318473816 };
window.celiaConfusao.updateAnchor();
state = window.celiaConfusao.state();
assert.ok(state.anchor.left >= 0, "saved Pet pose must not push the accessory left of the canvas");
assert.ok(state.anchor.left + state.anchor.size <= 1920, "saved Pet pose must keep the accessory on-screen horizontally");
assert.ok(state.anchor.top >= 0, "saved Pet pose must not push the accessory above the canvas");
assert.ok(state.anchor.top + state.anchor.size <= 1080, "saved Pet pose must keep the accessory on-screen vertically");

const childCount = body.children.length;
window.dispatchEvent({ type: "celia-emotion", detail: { emotion: "confused" } });
assert.equal(body.children.length, childCount, "repeated confused events must not duplicate the accessory");
assert.match(window.celiaConfusao.state().source, /entrada\.gif$/, "repeated confused must not restart the entry animation");

window.celiaConfusao.handleMessage(JSON.stringify({ type: "celia-face", emotion: "trol_speaking", source: "playback" }));
assert.equal(window.celiaConfusao.state().visible, true, "technical compatibility input must not erase confused");

wrapper.dataset.celiaDesktopPetDetached = "true";
wrapper.style.display = "none";
window.dispatchEvent({ type: "resize" });
state = window.celiaConfusao.state();
assert.equal(state.requested, true, "confused affect must survive renderer detachment");
assert.equal(state.visible, false, "main interface must not show a loose question mark while Live2D is detached");
assert.equal(state.tracking, false, "detached renderer must suspend accessory tracking");

delete wrapper.dataset.celiaDesktopPetDetached;
delete wrapper.style.display;
window.dispatchEvent({ type: "celia-browser-pet-mode", detail: { mode: "window" } });
state = window.celiaConfusao.state();
assert.equal(state.visible, true, "question mark must return with the avatar while confused");
assert.equal(state.tracking, true);

assert.match(
  require("node:fs").readFileSync(path.resolve(__dirname, "..", "frontend", "celia-confusao.js"), "utf8"),
  /pointer-events: none/
);

window.dispatchEvent({ type: "celia-emotion", detail: { emotion: "angry" } });
assert.equal(window.celiaConfusao.state().visible, false);
assert.equal(window.celiaConfusao.state().tracking, false);

window.celiaConfusao.handleMessage("not json");
assert.equal(window.celiaConfusao.state().visible, false);
console.log("celia confusion accessory: PASS");
