"use strict";

const assert = require("node:assert");
const path = require("node:path");

const posted = [];
let registeredName = null;
let RegisteredProcessor = null;

global.AudioWorkletProcessor = class AudioWorkletProcessor {
  constructor() {
    this.port = {
      postMessage(payload) {
        posted.push(payload);
      },
    };
  }
};

global.registerProcessor = (name, Processor) => {
  registeredName = name;
  RegisteredProcessor = Processor;
};

require(path.resolve(__dirname, "..", "frontend", "rms-vad-worklet.js"));

assert.equal(registeredName, "rms-vad-processor");
assert.ok(RegisteredProcessor);

const processor = new RegisteredProcessor({ processorOptions: { blockSize: 1024 } });
for (let i = 0; i < 7; i += 1) {
  processor.process([[new Float32Array(128).fill(0.03)]]);
  assert.equal(posted.length, 0, "must not emit before accumulating 1024 frames");
}
processor.process([[new Float32Array(128).fill(0.03)]]);

assert.equal(posted.length, 1);
assert.equal(posted[0].type, "audio-block");
assert.equal(posted[0].frames, 1024);
assert.equal(posted[0].chunk.length, 1024);
assert.ok(Math.abs(posted[0].rms - 0.03) < 1e-6);
console.log("rms-vad-worklet 128→1024 accumulation + RMS: OK");
