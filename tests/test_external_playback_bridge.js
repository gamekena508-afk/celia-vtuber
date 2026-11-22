"use strict";

const assert = require("node:assert");
const path = require("node:path");

let nowMs = 0;
Date.now = () => nowMs;

const intervals = [];
global.setInterval = (callback, delay) => {
  const timer = {
    callback,
    cleared: false,
    delay,
    unref() {},
  };
  intervals.push(timer);
  return timer;
};
global.clearInterval = (timer) => {
  timer.cleared = true;
};
global.setTimeout = (_callback, delay) => ({
  cleared: false,
  delay,
  unref() {},
});

function advanceTime(ms) {
  nowMs += ms;
  for (const timer of intervals) {
    if (!timer.cleared) {
      timer.callback();
    }
  }
}

class FakeWebSocket {
  static OPEN = 1;
  static CLOSED = 3;

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.OPEN;
    this.listeners = new Map();
    this.sent = [];
    this.closeCount = 0;
    this.failSend = false;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  emit(type, event = {}) {
    for (const listener of this.listeners.get(type) || []) {
      listener(event);
    }
  }

  send(payload) {
    if (this.failSend) {
      throw new Error("send failed");
    }
    const message = JSON.parse(payload);
    if (message.type === "audio-lifecycle") return; // Lifecycle is asserted in the dedicated suite.
    this.sent.push(message);
  }

  close() {
    if (this.readyState === FakeWebSocket.CLOSED) {
      return;
    }
    this.readyState = FakeWebSocket.CLOSED;
    this.closeCount += 1;
    this.emit("close");
  }
}

class FakeAudio {
  static played = [];

  constructor(src) {
    this.src = src;
    this.listeners = new Map();
    this.pauseCount = 0;
    this.loadCount = 0;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  emit(type) {
    for (const listener of this.listeners.get(type) || []) {
      listener();
    }
  }

  play() {
    FakeAudio.played.push(this.src);
    if (
      this.src.endsWith("RkFJTA==") ||
      this.src.endsWith("UkVHVUxBUkZBSUw=")
    ) {
      return Promise.reject(new Error("autoplay denied"));
    }
    return Promise.resolve();
  }

  pause() {
    this.pauseCount += 1;
  }

  removeAttribute(name) {
    if (name === "src") this.src = "";
  }

  load() {
    this.loadCount += 1;
  }
}

const browserEvents = [];
global.window = {
  WebSocket: FakeWebSocket,
  Audio: FakeAudio,
  location: {
    href: "http://127.0.0.1:12393/?pet=1",
    search: "?pet=1",
  },
  CustomEvent: class CustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  },
  dispatchEvent(event) {
    browserEvents.push(event);
  },
};

require(path.resolve(__dirname, "..", "frontend", "external-playback-bridge.js"));

const petTaggedSocket = new window.WebSocket("ws://127.0.0.1:12393/client-ws");
assert.match(petTaggedSocket.url, /playback_client=pet/);

const socket = new window.WebSocket("ws://test");
const liveSocket = new window.WebSocket("ws://live");
liveSocket.emit("open");
assert.deepEqual(liveSocket.sent, [{ type: "heartbeat" }]);
advanceTime(10 * 1000);
assert.deepEqual(liveSocket.sent[1], { type: "heartbeat" });
liveSocket.emit("message", { data: JSON.stringify({ type: "heartbeat-ack" }) });
advanceTime(29 * 1000);
assert.equal(liveSocket.closeCount, 0);

const staleSocket = new window.WebSocket("ws://stale");
staleSocket.emit("open");
advanceTime(30 * 1000);
assert.equal(staleSocket.closeCount, 1);

const failingSocket = new window.WebSocket("ws://send-fail");
failingSocket.failSend = true;
failingSocket.emit("open");
assert.equal(failingSocket.closeCount, 1);

const expectedUrl = "data:audio/wav;base64,Q09ERVg=";
socket.emit("message", {
  data: JSON.stringify({
    type: "audio",
    audio: "Q09ERVg=",
    speech_id: "speech-codex",
    external_playback: true,
  }),
});

const auxiliaryAudio = new window.Audio("data:audio/wav;base64,T1RIRVI=");
auxiliaryAudio.emit("ended");
assert.deepEqual(socket.sent, []);

const codexAudio = new window.Audio(expectedUrl);
codexAudio.emit("ended");
assert.deepEqual(socket.sent, [
  {
    type: "external-playback-complete",
    speech_id: "speech-codex",
    status: "ended",
  },
]);

codexAudio.emit("ended");
assert.equal(socket.sent.length, 1);

socket.emit("message", {
  data: JSON.stringify({
    type: "audio",
    audio: "Q09ERVg=",
    speech_id: "speech-codex-repeat",
    external_playback: true,
  }),
});
const repeatedCodexAudio = new window.Audio(expectedUrl);
repeatedCodexAudio.emit("ended");
assert.deepEqual(socket.sent[1], {
  type: "external-playback-complete",
  speech_id: "speech-codex-repeat",
  status: "ended",
});

socket.emit("message", {
  data: JSON.stringify({
    type: "audio",
    audio: "RkFJTA==",
    speech_id: "speech-failed-play",
    external_playback: true,
  }),
});
const failedAudio = new window.Audio("data:audio/wav;base64,RkFJTA==");
failedAudio.play().catch(() => {});

const sharedUrl = "data:audio/wav;base64,U0hBUkVE";
const firstSharedAudio = new window.Audio(sharedUrl);
const duplicateSharedAudio = new window.Audio(sharedUrl);
let duplicateEnded = false;
duplicateSharedAudio.addEventListener("ended", () => {
  duplicateEnded = true;
});
firstSharedAudio.play();
duplicateSharedAudio.play();

const regularFailedAudio = new window.Audio(
  "data:audio/wav;base64,UkVHVUxBUkZBSUw=",
);
let regularFailureEnded = false;
regularFailedAudio.addEventListener("ended", () => {
  regularFailureEnded = true;
});
regularFailedAudio.play().catch(() => {});

setImmediate(() => {
  assert.deepEqual(socket.sent[2], {
    type: "external-playback-complete",
    speech_id: "speech-failed-play",
    status: "error",
  });
  assert.equal(
    FakeAudio.played.filter((src) => src === sharedUrl).length,
    1,
  );
  assert.equal(duplicateEnded, true);
  assert.equal(regularFailureEnded, true);

  const normalUrl = "data:audio/wav;base64,Q0FOQ0VM";
  socket.emit("message", {
    data: JSON.stringify({
      type: "audio",
      audio: "Q0FOQ0VM",
      speech_id: "speech-cancellable",
    }),
  });
  const cancellable = new window.Audio(normalUrl);
  cancellable.play();
  socket.emit("message", {
    data: JSON.stringify({ type: "playback.cancel", speech_id: "speech-cancellable" }),
  });
  assert.equal(cancellable.pauseCount, 1);
  assert.equal(cancellable.loadCount, 1);
  assert.equal(socket.sent[3].type, "frontend-playback-cancelled");
  assert.equal(socket.sent[3].speech_id, "speech-cancellable");
  assert.match(socket.sent[3].player_id, /^player-/);

  const subtitleUrl = "data:audio/wav;base64,U1VCVElUTEU=";
  socket.emit("message", {
    data: JSON.stringify({
      type: "audio",
      audio: "U1VCVElUTEU=",
      speech_id: "speech-subtitle",
      turn_id: "turn-subtitle",
      phrase_index: 2,
    }),
  });
  const subtitleAudio = new window.Audio(subtitleUrl);
  assert.equal(
    browserEvents.filter((event) => event.type === "celia-lipsync-start" && event.detail?.speechId === "speech-subtitle").length,
    0,
    "constructing Audio must not start lipsync",
  );
  const beforePrematureAck = socket.sent.length;
  socket.send(JSON.stringify({
    type: "audio-playback-started",
    speech_id: "speech-subtitle",
  }));
  assert.equal(
    socket.sent.length,
    beforePrematureAck,
    "legacy canplaythrough ACK must be suppressed until play() resolves",
  );
  assert.equal(socket.__prematurePlaybackAcksSuppressed, 1);
  subtitleAudio.play().then(() => {
    const startedAck = socket.sent.at(-1);
    assert.equal(startedAck.type, "audio-playback-started");
    assert.equal(startedAck.speech_id, "speech-subtitle");
    assert.equal(startedAck.turn_id, "turn-subtitle");
    assert.equal(startedAck.phrase_index, 2);
    assert.match(startedAck.player_id, /^player-/);
    assert.equal(
      browserEvents.filter((event) => event.type === "celia-lipsync-start" && event.detail?.speechId === "speech-subtitle").length,
      1,
      "successful playback starts lipsync exactly once",
    );
    return subtitleAudio.play();
  }).then(() => {
    assert.equal(
      browserEvents.filter((event) => event.type === "celia-lipsync-start" && event.detail?.speechId === "speech-subtitle").length,
      1,
      "repeated play() must not duplicate lipsync start",
    );
    const startedAck = socket.sent.findLast((message) => message.type === "audio-playback-started" && message.speech_id === "speech-subtitle");
    subtitleAudio.emit("ended");
    const finishedAck = socket.sent.findLast((message) => message.type === "audio-playback-finished" && message.speech_id === "speech-subtitle");
    assert.equal(finishedAck.player_id, startedAck.player_id);
    assert.equal(finishedAck.status, "ended");
    assert.equal(
      browserEvents.filter((event) => event.type === "celia-lipsync-stop" && event.detail?.speechId === "speech-subtitle").length,
      1,
      "playback end must close lipsync exactly once",
    );
    console.log("external-playback-bridge correlation: PASS");
  });
});
