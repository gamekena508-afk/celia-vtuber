"use strict";
// Browser-free behavioral proof of the approved - / hold-to-drag / + controls.
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const elements = new Map();
function node(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id, hidden: false, value: "", textContent: "", clientWidth: 360, clientHeight: 640,
      handlers: {}, addEventListener(type, cb) {this.handlers[type] = cb;},
      setPointerCapture(pointerId) {this.captured = pointerId;},
      querySelector() {return node("form-button");},
      play: async () => {}, querySelectorAll: () => [],
    });
  }
  return elements.get(id);
}
const sent = [];
class Socket {
  static OPEN = 1;
  constructor() {this.readyState = 1;this.handlers = {};}
  send(text) {sent.push(JSON.parse(text));}
  addEventListener(type, fn) {this.handlers[type] = fn;}
  close() {}
}
const document = {
  getElementById: node,
  querySelectorAll: () => [],
  documentElement: {requestFullscreen: async () => {}},
};
const context = vm.createContext({
  document, location: {protocol: "http:", host: "127.0.0.1:12393", replace() {}},
  WebSocket: Socket, setTimeout() {}, clearTimeout() {}, console,
});
const file = path.resolve(__dirname, "../frontend/mobile/app.js");
vm.runInContext(fs.readFileSync(file, "utf8"), context);
const fire = (id, type, event = {}) => node(id).handlers[type]({preventDefault() {}, ...event});

fire("smaller", "click");
fire("larger", "click");
assert.deepEqual(sent.map(m => m.action), ["zoom", "zoom"]);
assert.equal(sent[0].step, -0.12);
assert.equal(sent[1].step, 0.12);

const handle = node("move-character");
fire("move-character", "pointerdown", {pointerId: 12, clientX: 110, clientY: 80});
assert.equal(handle.captured, 12);
fire("move-character", "pointermove", {pointerId: 12, clientX: 146, clientY: 144});
assert.equal(sent.length, 3);
assert.equal(sent[2].action, "move");
assert.ok(Math.abs(sent[2].dx - .1) < 1e-10);
assert.ok(Math.abs(sent[2].dy - .1) < 1e-10);
fire("move-character", "pointerup", {pointerId: 12});
fire("move-character", "pointermove", {pointerId: 12, clientX: 300, clientY: 300});
assert.equal(sent.length, 3, "releasing the icon stops drag");
fire("center", "click");
assert.equal(sent[3].action, "center");
const screen = {id: "celia-video", closest: () => null};
fire("mobile-stage", "pointerdown", {pointerType: "touch", pointerId: 21, clientX: 100, clientY: 100, target: screen});
fire("mobile-stage", "pointerdown", {pointerType: "touch", pointerId: 22, clientX: 200, clientY: 100, target: screen});
fire("mobile-stage", "pointermove", {pointerType: "touch", pointerId: 22, clientX: 220, clientY: 100, target: screen});
assert.equal(sent[4].action, "zoom", "two-finger gesture changes scale");
assert.ok(Math.abs(sent[4].step - .2) < 1e-10);
fire("mobile-stage", "pointerup", {pointerId: 22});
console.log("Mobile drag handle, zoom and pinch: behavioral assertions passed.");
