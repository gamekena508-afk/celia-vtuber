"use strict";
const assert = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");

let now = 0;
let seed = 424242;
const ids = [
  "AngleX", "AngleY", "AngleZ", "ParamBodyAngleX", "Param93", "Param94",
  "ParamBodyAngleZ2", "ParamBodyAngleZ3", "ParamBodyAngleX3", "Param154",
  "ParamBreath", "Param310", "ParamEyeBallX", "ParamEyeBallY",
  "ParamEyeLOpen", "ParamEyeROpen", "Param283", "Param284", "Param285", "Param286",
  "Param107", "Param108", "Param109", "Param110", "Param158", "Param159", "Param160",
  "Param194", "Param195", "Param196", "Param163", "Param164", "Param189", "Param190",
];
const values = new Float32Array(ids.length);
const minimumValues = new Float32Array(ids.length).fill(-40);
const maximumValues = new Float32Array(ids.length).fill(40);
const defaultValues = new Float32Array(ids.length);
for (const id of ["ParamEyeLOpen", "ParamEyeROpen"]) {
  minimumValues[ids.indexOf(id)] = 0; maximumValues[ids.indexOf(id)] = 1.5; values[ids.indexOf(id)] = 1;
}
minimumValues[ids.indexOf("Param310")] = 0; maximumValues[ids.indexOf("Param310")] = 1;
const core = { parameters: { ids, values, minimumValues, maximumValues, defaultValues } };
const bodyY = [];
const cubism = { getModel: () => core, update: () => bodyY.push(values[ids.indexOf("Param93")]) };
const lapp = { _model: cubism };
const listeners = new Map();
class FakeCustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail || {}; } }
const win = {
  getLAppAdapter: () => ({ getModel: () => lapp }), CustomEvent: FakeCustomEvent,
  addEventListener(type, listener) { const group = listeners.get(type) || []; group.push(listener); listeners.set(type, group); },
  dispatchEvent(event) { for (const listener of listeners.get(event.type) || []) listener(event); },
};
const seededMath = Object.create(Math);
seededMath.random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296);
class FakeDate extends Date { static now() { return now; } }
vm.runInNewContext(fs.readFileSync("frontend/celia-motion-director.js", "utf8"), {
  window: win, console: { log() {}, warn() {} }, setInterval() { return 1; }, clearInterval() {},
  Date: FakeDate, performance: { now: () => now }, Math: seededMath, Number, Boolean, String, Array, Object, JSON,
});
const director = win.celiaMotionDirector;
for (let frame = 0; frame < 1800; frame++) {
  now += 1000 / 30;
  if (frame === 60) director.setEmotion("happy", { source: "jerk-regression" });
  if (frame === 90) win.dispatchEvent(new FakeCustomEvent("celia-speech-energy", { detail: { energy: .70, slope: .28, running: true } }));
  if (frame > 90 && frame < 240 && frame % 3 === 0) win.dispatchEvent(new FakeCustomEvent("celia-speech-energy", { detail: { energy: .52 + .18 * Math.sin(frame * .19), slope: .18 * Math.sin(frame * .27), running: true } }));
  if (frame === 240) win.dispatchEvent(new FakeCustomEvent("celia-lipsync-stop"));
  if (frame === 360) director.setEmotion("confused", { source: "jerk-regression" });
  if (frame === 510) win.dispatchEvent(new FakeCustomEvent("celia-speech-energy", { detail: { energy: .78, slope: .34, running: true } }));
  if (frame > 510 && frame < 690 && frame % 3 === 0) win.dispatchEvent(new FakeCustomEvent("celia-speech-energy", { detail: { energy: .58 + .15 * Math.sin(frame * .17), slope: .20 * Math.sin(frame * .31), running: true } }));
  if (frame === 690) win.dispatchEvent(new FakeCustomEvent("celia-lipsync-stop"));
  if (frame === 900) director.setEmotion("neutral", { source: "jerk-regression" });
  cubism.update();
}
const dt = 1 / 30;
let previousVelocity = 0, previousAcceleration = 0;
const jerks = [];
for (let i = 1; i < bodyY.length; i++) {
  const velocity = (bodyY[i] - bodyY[i - 1]) / dt;
  const acceleration = (velocity - previousVelocity) / dt;
  const jerk = Math.abs((acceleration - previousAcceleration) / dt);
  previousVelocity = velocity; previousAcceleration = acceleration;
  if (i > 30) jerks.push(jerk);
}
jerks.sort((a, b) => a - b);
const q = (p) => jerks[Math.floor((jerks.length - 1) * p)];
const maxJerk = jerks.at(-1);
const p99Jerk = q(.99);
// Historical V11 baseline at commit 435af6a in this exact scenario:
// max=142.97, p99=31.39. Keep both materially below that regression point.
assert(maxJerk < 60, `bodyY max jerk regressed: ${maxJerk}`);
assert(p99Jerk < 30, `bodyY p99 jerk regressed: ${p99Jerk}`);
console.log(`bodyY jerk regression: PASS; max=${maxJerk.toFixed(2)} p99=${p99Jerk.toFixed(2)}`);
