"use strict";

const assert = require("node:assert");
const path = require("node:path");

class FakeWebSocket {
  static OPEN = 1;
  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.OPEN;
    this.listeners = new Map();
    this.sent = [];
  }
  addEventListener(type, cb) {
    const list = this.listeners.get(type) || [];
    list.push(cb); this.listeners.set(type, list);
  }
  emit(type, event = {}) { for (const cb of this.listeners.get(type) || []) cb(event); }
  send(payload) {
    const msg = JSON.parse(payload);
    if (msg.type !== "audio-lifecycle") this.sent.push(msg);
  }
  close() {}
}

class FakeAudio {
  constructor(src) {
    this.src = src;
    this.listeners = new Map();
    this.pauseCount = 0;
    this.loadCount = 0;
    this.playCount = 0;
    this.error = null;
    this.networkState = 2;
    this.readyState = 4;
  }
  addEventListener(type, cb) {
    const list = this.listeners.get(type) || [];
    list.push(cb); this.listeners.set(type, list);
  }
  emit(type) { for (const cb of [...(this.listeners.get(type) || [])]) cb(); }
  dispatchEvent(event) { this.emit(event.type); }
  play() { this.playCount += 1; return Promise.resolve(); }
  pause() { this.pauseCount += 1; }
  load() { this.loadCount += 1; }
  removeAttribute(name) { if (name === "src") this.src = ""; }
}

global.setInterval = () => ({ unref() {} });
global.clearInterval = () => {};
global.setTimeout = (cb) => ({ unref() {}, cb });
global.clearTimeout = () => {};
global.Event = class Event { constructor(type) { this.type = type; } };
global.window = {
  WebSocket: FakeWebSocket,
  Audio: FakeAudio,
  Event: global.Event,
  location: { href: "http://127.0.0.1:12393/?pet=1", search: "?pet=1" },
  CustomEvent: class CustomEvent { constructor(type, init={}) { this.type=type; this.detail=init.detail; } },
  dispatchEvent() {},
};

require(path.resolve(__dirname, "..", "frontend", "external-playback-bridge.js"));

function deliver(socket, id, b64) {
  socket.emit("message", { data: JSON.stringify({ type: "audio", speech_id: id, audio: b64 }) });
  return new window.Audio(`data:audio/wav;base64,${b64}`);
}

async function drain() {
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

(async () => {
  const socket = new window.WebSocket("ws://127.0.0.1:12393/client-ws");

  const recovered = deliver(socket, "retry-recovers", "UkVDT1ZFUlM=");
  await recovered.play();
  recovered.error = { code: 3 };
  recovered.networkState = 2;
  recovered.readyState = 0;
  recovered.emit("error");
  await drain();
  assert.equal(recovered.loadCount, 1, "first media error must reload the same payload once");
  assert.equal(recovered.playCount, 2, "first media error must retry playback once");
  assert.equal(
    window.celiaPlayback.events.filter((e) => e.event === "AUDIO_RETRY" && e.speech_id === "retry-recovers").length,
    1,
  );
  assert.equal(
    window.celiaPlayback.events.filter((e) => e.event === "AUDIO_DROPPED" && e.speech_id === "retry-recovers").length,
    0,
    "recoverable media error must not drop the speech",
  );
  recovered.emit("ended");
  assert.equal(
    window.celiaPlayback.events.filter((e) => e.event === "AUDIO_FINISHED" && e.speech_id === "retry-recovers").length,
    1,
  );

  const failed = deliver(socket, "retry-fails", "RkFJTFM=");
  await failed.play();
  failed.error = { code: 4 };
  failed.networkState = 3;
  failed.readyState = 0;
  failed.emit("error");
  await drain();
  failed.emit("error");
  await drain();
  const retryEvents = window.celiaPlayback.events.filter((e) => e.event === "AUDIO_RETRY" && e.speech_id === "retry-fails");
  const dropEvents = window.celiaPlayback.events.filter((e) => e.event === "AUDIO_DROPPED" && e.speech_id === "retry-fails");
  assert.equal(retryEvents.length, 1, "terminal failure still gets only one retry");
  assert.equal(dropEvents.length, 1, "second media error must retire the speech exactly once");
  assert.match(dropEvents[0].reason, /media_error_retry_failed:code=4,network=3,ready=0/);
  assert.equal(failed.loadCount, 1);
  assert.equal(failed.playCount, 2);

  console.log("media error retry: PASS");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
