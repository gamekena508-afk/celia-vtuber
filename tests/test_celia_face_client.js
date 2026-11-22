"use strict";

const assert = require("node:assert");
const path = require("node:path");

let now = 0;
const intervals = [];
global.performance = { now: () => now };
global.setInterval = (callback) => {
  intervals.push(callback);
  return { unref() {} };
};

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;

  constructor(url) {
    this.url = url;
    this._readyState = FakeWebSocket.CONNECTING;
    this.listeners = new Map();
    this.sent = [];
  }

  get readyState() {
    return this._readyState;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  emit(type, event = {}) {
    for (const listener of this.listeners.get(type) || []) listener(event);
  }

  open() {
    this._readyState = FakeWebSocket.OPEN;
    this.emit("open");
  }

  send(payload) {
    if (this._readyState !== FakeWebSocket.OPEN) throw new Error("native socket is not open");
    this.sent.push(payload);
  }
}

const windowListeners = new Map();
global.window = {
  WebSocket: FakeWebSocket,
  location: { href: "http://127.0.0.1:12393/" },
  addEventListener(type, listener) {
    const group = windowListeners.get(type) || []; group.push(listener); windowListeners.set(type, group);
  },
  dispatchEvent(event) { for (const listener of windowListeners.get(event.type) || []) listener(event); },
};
require(path.resolve(__dirname, "..", "frontend", "celia-face.js"));

const clientSocket = new window.WebSocket("ws://127.0.0.1:12393/client-ws");
assert.equal(clientSocket.readyState, FakeWebSocket.OPEN);
clientSocket.send("first");
clientSocket.send("second");
assert.deepEqual(clientSocket.sent, []);
clientSocket.open();
assert.deepEqual(clientSocket.sent, ["first", "second"]);

const otherSocket = new window.WebSocket("ws://127.0.0.1:12393/other");
assert.equal(otherSocket.readyState, FakeWebSocket.CONNECTING);

const eyeParameters = {
  ids: ["ParamEyeLOpen", "ParamEyeROpen"],
  values: new Float32Array([0, 0]),
};
const fakeCore = { parameters: eyeParameters };
const fakeCubism = { getModel: () => fakeCore, update() {} };
const nativeBlinkIntervals = [];
const fakeLapp = {
  _model: fakeCubism,
  _eyeBlink: { setBlinkingInterval(value) { nativeBlinkIntervals.push(value); } },
};
global.window.getLAppAdapter = () => ({ getModel: () => fakeLapp });
require(path.resolve(__dirname, "..", "frontend", "celia-olhos.js"));
for (const callback of intervals) callback();
assert.equal(nativeBlinkIntervals.at(-1), 5.6);
window.dispatchEvent({ type: "celia-emotion", detail: { emotion: "angry" } });
assert.equal(nativeBlinkIntervals.at(-1), 7.2);
assert.equal(window.celiaOlhos.estado().nativeBlinkInterval, 7.2);

fakeCubism.update();
now = 400;
fakeCubism.update();
assert.deepEqual(Array.from(eyeParameters.values), [1, 1]);

eyeParameters.values[0] = 0;
eyeParameters.values[1] = 0;
now = 500;
fakeCubism.update();
assert.deepEqual(Array.from(eyeParameters.values), [1, 1]);

// Motion requests a blink, but celia-olhos.js remains the only eyelid owner.
eyeParameters.values[0] = 1; eyeParameters.values[1] = 1;
window.dispatchEvent({ type: "celia-blink-request", detail: { reason: "gaze-saccade", durationMs: 100 } });
now = 520;
fakeCubism.update();
assert(Array.from(eyeParameters.values).every((value) => value < 0.1));
assert.equal(window.celiaOlhos.estado().blinkAtivo, true);
now = 650;
fakeCubism.update();
assert.deepEqual(Array.from(eyeParameters.values), [1, 1]);
assert.equal(window.celiaOlhos.estado().blinkAtivo, false);

console.log("celia face lifecycle and eyes: PASS; gaze blink ownership PASS");
