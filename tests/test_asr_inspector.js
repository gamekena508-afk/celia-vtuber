"use strict";

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

class FakeWebSocket {
  static OPEN = 1;
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.listeners = new Map();
  }
  addEventListener(type, callback) {
    const list = this.listeners.get(type) || [];
    list.push(callback);
    this.listeners.set(type, list);
  }
}

global.URLSearchParams = URLSearchParams;
global.document = {
  body: null,
  head: { appendChild() {} },
  getElementById() { return null; },
  createElement() { throw new Error("DOM rendering should be lazy in this test"); },
};
global.window = {
  location: { search: "", href: "http://127.0.0.1:12393/" },
  WebSocket: FakeWebSocket,
};
global.WebSocket = FakeWebSocket;

const script = path.resolve(__dirname, "..", "frontend", "celia-asr-inspector.js");
require(script);

assert.ok(window.celiaAsrInspector, "ASR inspector API must be installed");

window.celiaAsrInspector.handlePayload({
  type: "user-input-transcription",
  source: "microphone",
  utterance_id: "u-normalized",
  raw_text: "Séria, olha isso.",
  corrected_text: "Célia, olha isso.",
  asr_decision: "CORRECT",
  asr_refinement_status: "KEEP",
  asr_normalization_rules: ["celia_vocative"],
  asr_rejected: false,
  asr_uncertain: false,
});

let history = window.celiaAsrInspector.history();
assert.equal(history.length, 1);
assert.equal(history[0].raw, "Séria, olha isso.");
assert.equal(history[0].corrected, "Célia, olha isso.");
assert.equal(history[0].decision, "CORRECT");

window.celiaAsrInspector.handlePayload({
  type: "asr-trace",
  source: "microphone",
  utterance_id: "u-normalized",
  raw_text: "Séria, olha isso.",
  corrected_text: "Célia, olha isso.",
  asr_event: "SUBMITTED_TO_BRAIN",
  asr_route_status: "SUBMITTED_TO_BRAIN",
  asr_submitted_to_brain: true,
  asr_acoustic: { rms: 0.041, voiced_ms: 420 },
  asr_rejected: false,
});

history = window.celiaAsrInspector.history();
assert.equal(history.length, 1, "routing traces must update the same utterance card");
assert.equal(history[0].route, "SUBMITTED_TO_BRAIN");
assert.equal(history[0].submitted, true);
assert.equal(history[0].acoustic.voiced_ms, 420);

window.celiaAsrInspector.handlePayload({
  type: "user-input-transcription",
  source: "microphone",
  raw_text: "Legenda por Sônia Ruberti",
  corrected_text: "",
  asr_decision: "REJECT",
  asr_refinement_status: "SKIPPED",
  asr_normalization_rules: ["hallucination_gate"],
  asr_rejected: true,
  asr_reject_reason: "known_whisper_hallucination_with_weak_audio",
});

history = window.celiaAsrInspector.history();
assert.equal(history[0].rejected, true);
assert.equal(history[0].raw, "Legenda por Sônia Ruberti");
assert.equal(history[0].corrected, "");

const source = fs.readFileSync(script, "utf8");
assert.ok(source.includes("#f7b955"), "raw ASR must have its own amber color");
assert.ok(source.includes("#67e8f9"), "corrected text must have its own cyan color");
assert.ok(source.includes("#fb7185"), "rejected hallucinations must be red");
assert.ok(source.includes("#c4b5fd"), "uncertain ASR must have a distinct color");

console.log("celia-asr-inspector dual raw/corrected payload: OK");

// The actual React store uses this upsert: trace updates cannot add another M.
const api = window.celiaAsrInspector;
let messages = api.upsertMessage([], "Célia, olha isso.", history.find(x => x.id === "u-normalized"));
const originalId = messages[0].id;
messages = api.upsertMessage(messages, "Célia, olha isso.", {...messages[0].asr, route: "SUBMITTED_TO_BRAIN"});
assert.equal(messages.length, 1);
assert.equal(messages[0].id, originalId);
messages = api.upsertMessage(messages, "Célia, olha isso.", {...messages[0].asr, id: "another-utterance"});
assert.equal(messages.length, 2, "identical speech with different IDs is distinct");
messages = api.upsertMessage(messages, "typed message");
assert.equal(messages.length, 3);
assert.equal(messages[2].asr, undefined);
assert.equal(api.handlePayload({type:"asr-trace",source:"microphone",raw_text:"",asr_event:"ACOUSTIC_ACCEPT"}), null);
assert.ok(!source.includes("position:fixed"));
assert.ok(!source.includes("document.body.append"));
console.log("Native ASR chat upsert and trace lifecycle: OK");
