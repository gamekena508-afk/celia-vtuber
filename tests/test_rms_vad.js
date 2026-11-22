"use strict";

const assert = require("node:assert");
const path = require("node:path");

const timers = [];
global.setInterval = () => ({ unref() {} });
global.setTimeout = (callback, delay) => {
  const timer = { callback, delay, cleared: false };
  timers.push(timer);
  return timer;
};
global.clearTimeout = (timer) => {
  timer.cleared = true;
};
global.CustomEvent = class CustomEvent {
  constructor(type, options) {
    this.type = type;
    this.detail = options?.detail;
  }
};
global.document = { dispatchEvent() {} };

class FakeWebSocket {
  static OPEN = 1;
  static CLOSED = 3;

  constructor() {
    this.readyState = FakeWebSocket.OPEN;
    this.listeners = new Map();
    this.sent = [];
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  send(payload) {
    this.sent.push(JSON.parse(payload));
  }
}

let processor = null;

class FakeAudioWorkletNode {
  constructor() {
    this.port = { onmessage: null };
    processor = this;
  }

  connect() {}
}

class FakeAudioContext {
  constructor() {
    this.sampleRate = 48000;
    this.destination = {};
    this.state = "running";
    this.audioWorklet = { addModule: async () => {} };
  }

  createMediaStreamSource() {
    return { connect() {} };
  }

  createGain() {
    return { gain: { value: 1 }, connect() {} };
  }

  async resume() {
    this.state = "running";
  }
}

global.WebSocket = FakeWebSocket;
global.AudioContext = FakeAudioContext;
global.AudioWorkletNode = FakeAudioWorkletNode;
global.BroadcastChannel = undefined;
global.window = {
  WebSocket: FakeWebSocket,
  AudioContext: FakeAudioContext,
};
Object.defineProperty(global, "navigator", {
  configurable: true,
  value: {
    mediaDevices: {
      getUserMedia: async () => ({ id: "test-stream" }),
      enumerateDevices: async () => [],
    },
  },
});
global.localStorage = {
  getItem() { return null; },
  setItem() {},
};

require(path.resolve(__dirname, "..", "frontend", "rms_vad.js"));

function processFrame(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
  processor.port.onmessage({
    data: {
      type: "audio-block",
      chunk: samples,
      rms: Math.sqrt(sum / samples.length),
    },
  });
}

(async () => {
  const socket = new window.WebSocket("ws://test");
  await navigator.mediaDevices.getUserMedia({ audio: true });
  assert.ok(processor, "the RMS AudioWorkletNode must be installed");

  const quiet = new Float32Array(1024);
  const voice = new Float32Array(1024).fill(0.03);

  // A lone impulse must not begin a websocket utterance.
  processFrame(voice);
  processFrame(quiet);
  assert.equal(socket.sent.length, 0);

  // Fill the 360 ms ring with silence, then deliver two confirmed voice blocks.
  for (let i = 0; i < 18; i += 1) processFrame(quiet);
  processFrame(voice);
  processFrame(voice);

  const config = window.__rmsVad.getConfig();
  assert.deepEqual(config, {
    prerollMs: 360,
    blockSize: 1024,
    softSilenceMs: 480,
    maxUtteranceMs: 10000,
    transportMs: 140,
    activationFrames: 2,
    minVoicedMs: 180,
    hardSilenceMs: 650,
    targetRate: 16000,
  });
  assert.equal(socket.sent[0].type, "speech.started");
  assert.equal(socket.sent[1].type, "mic-audio-data");

  // The two confirmation blocks are in pre-roll once. A duplicated trigger
  // block would produce about 1,024 non-zero 16 kHz samples instead of 683.
  const activeSamples = socket.sent[1].audio.filter((value) => value > 0.01);
  assert.equal(activeSamples.length, Math.ceil((2 * 1024) / 3));
  assert.equal(window.__rmsVad.getDiagnostics().candidateActiveFrames, 0);

  // Depois de aberta a utterance, blocos abaixo do threshold precisam continuar
  // na linha do tempo. Antes deste fix eles eram simplesmente apagados.
  for (let i = 0; i < 7; i += 1) processFrame(quiet);
  const dataMessages = socket.sent.filter((msg) => msg.type === "mic-audio-data");
  assert.ok(dataMessages.length >= 2, "quiet frames inside speech must be transported");
  assert.ok(
    dataMessages.slice(1).some((msg) => msg.audio.every((value) => value === 0)),
    "the preserved low-RMS region must reach the backend as silence, not disappear",
  );

  // 480 ms é só soft endpoint: não pode fechar a fala.
  const softTimer = timers.filter(
    (timer) => !timer.cleared && timer.delay === config.softSilenceMs,
  ).at(-1);
  assert.ok(softTimer, "soft endpoint timer must exist");
  softTimer.callback();
  assert.equal(window.__rmsVad.getDiagnostics().pendingSoftEnd, true);
  assert.equal(
    socket.sent.filter((msg) => msg.type === "mic-audio-end").length,
    0,
    "soft endpoint must not finalize the utterance",
  );

  // Retomada antes do hard endpoint continua no mesmo utterance_id.
  const startedId = socket.sent.find((msg) => msg.type === "speech.started").utterance_id;
  processFrame(voice);
  assert.equal(window.__rmsVad.getDiagnostics().pendingSoftEnd, false);
  assert.equal(
    socket.sent.filter((msg) => msg.type === "speech.started").length,
    1,
    "resume before hard endpoint must not create a new utterance",
  );

  // O hard endpoint fecha a fala e preserva metadados separados de pre-roll/voz.
  const hardTimer = timers.filter(
    (timer) => !timer.cleared && timer.delay === config.hardSilenceMs,
  ).at(-1);
  assert.ok(hardTimer, "hard endpoint timer must exist");
  hardTimer.callback();
  const end = socket.sent.find((msg) => msg.type === "mic-audio-end");
  assert.ok(end, "utterance must emit mic-audio-end");
  assert.equal(end.utterance_id, startedId);
  assert.equal(end.endpoint_reason, "hard_silence");
  assert.equal(end.vad_source, "rms_endpoint_v2");
  assert.ok(end.preroll_samples >= 5700);
  assert.ok(end.voiced_samples > 0);
  assert.ok(end.captured_samples > end.preroll_samples);

  // Um comando curto sem retomada continua válido: não depende de duração mínima
  // para fechar, apenas do hard silence.
  processFrame(voice);
  processFrame(voice);
  const shortHardTimer = timers.filter(
    (timer) => !timer.cleared && timer.delay === config.hardSilenceMs,
  ).at(-1);
  assert.ok(shortHardTimer, "short command must arm hard endpoint");
  shortHardTimer.callback();
  const endsAfterShort = socket.sent.filter((msg) => msg.type === "mic-audio-end");
  assert.equal(endsAfterShort.length, 2);
  assert.equal(endsAfterShort.at(-1).endpoint_reason, "hard_silence");

  // Monólogo sem pausa suficiente é cortado pelo limite máximo e pode reabrir
  // no próximo frame sem misturar a utterance anterior.
  processFrame(voice);
  processFrame(voice);
  const maxTimer = timers.filter(
    (timer) => !timer.cleared && timer.delay === config.maxUtteranceMs,
  ).at(-1);
  assert.ok(maxTimer, "max utterance timer must exist");
  maxTimer.callback();
  const endsAfterMax = socket.sent.filter((msg) => msg.type === "mic-audio-end");
  assert.equal(endsAfterMax.length, 3);
  assert.equal(endsAfterMax.at(-1).endpoint_reason, "max_duration");

  // Mute cancels pending capture and erases pre-roll, even with non-zero frames.
  processFrame(voice);
  processFrame(voice);
  assert.equal(window.__rmsVad.getDiagnostics().speakingNow, true);
  const sentBeforeMute = socket.sent.length;
  window.__micToggle = {state: () => false};
  window.__rmsVad.resetMicrophoneCapture();
  processFrame(voice);
  const muted = window.__rmsVad.getDiagnostics();
  assert.equal(muted.speakingNow, false);
  assert.equal(muted.samplesWritten, 0);
  assert.equal(muted.transportSamples, 0);
  assert.equal(socket.sent.length, sentBeforeMute, "mute cannot flush pending speech");
  window.__micToggle = {state: () => true};
  processFrame(voice);
  processFrame(voice);
  assert.equal(window.__rmsVad.getDiagnostics().speakingNow, true);
  assert.equal(socket.sent.filter(m => m.type === "speech.started").length,
               5, "unmute must begin a fresh utterance");
  console.log("rms_vad continuous utterance + soft/hard/max endpointing: OK");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
