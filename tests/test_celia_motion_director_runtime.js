"use strict";

const fs = require("fs");
const vm = require("vm");

let now = 1000;
const listeners = new Map();
const intervals = [];

class FakeDate extends Date {
  static now() { return now; }
}

class FakeCustomEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail || {};
  }
}

function addListener(type, listener) {
  const group = listeners.get(type) || [];
  group.push(listener);
  listeners.set(type, group);
}

const ids = [
  "AngleX", "AngleY", "AngleZ",
  "ParamBodyAngleX", "Param93", "Param94",
  "ParamBodyAngleZ2", "ParamBodyAngleZ3", "ParamBodyAngleX3",
  "Param154", "ParamBreath", "Param310", "Param290",
  "ParamEyeBallX", "ParamEyeBallY", "ParamEyeLOpen", "ParamEyeROpen",
  "Param283", "Param284", "Param285", "Param286",
  "Param107", "Param108", "Param109", "Param110",
  "Param155", "Param156", "Param157",
  "Param158", "Param159", "Param160", "Param194", "Param195", "Param196",
  "Param163", "Param164", "Param189", "Param190",
  "Param178", "Param179", "Param180", "Param181",
  "Param184", "Param185", "Param186", "Param187",
  "ParamMouthOpenY", "ParamMouthForm",
];
const values = new Float32Array(ids.length);
const minimumValues = new Float32Array(ids.length).fill(-40);
const maximumValues = new Float32Array(ids.length).fill(40);
for (const id of ["ParamEyeLOpen", "ParamEyeROpen"]) {
  minimumValues[ids.indexOf(id)] = 0;
  maximumValues[ids.indexOf(id)] = 1.5;
  values[ids.indexOf(id)] = 1;
}
minimumValues[ids.indexOf("Param310")] = 0;
maximumValues[ids.indexOf("Param310")] = 1;
minimumValues[ids.indexOf("ParamMouthOpenY")] = 0;
maximumValues[ids.indexOf("ParamMouthOpenY")] = 1;
values[ids.indexOf("ParamMouthOpenY")] = 0.22;
values[ids.indexOf("ParamMouthForm")] = 0.11;

const core = { parameters: { ids, values, minimumValues, maximumValues, defaultValues: new Float32Array(ids.length) } };
const flushedFrames = [];
const cubism = {
  getModel: () => core,
  update: () => {
    flushedFrames.push({
      AngleY: values[ids.indexOf("AngleY")],
      BodyY: values[ids.indexOf("Param93")],
      Shoulder: values[ids.indexOf("ParamBodyAngleZ3")],
    });
  },
};
const lapp = { _model: cubism };
const adapter = { getModel: () => lapp };

const windowObject = {
  getLAppAdapter: () => adapter,
  CustomEvent: FakeCustomEvent,
  addEventListener: addListener,
  dispatchEvent(event) {
    for (const listener of listeners.get(event.type) || []) listener(event);
  },
};

const source = fs.readFileSync("frontend/celia-motion-director.js", "utf8");
vm.runInNewContext(source, {
  window: windowObject,
  console,
  setInterval(callback) { intervals.push(callback); return intervals.length; },
  clearInterval() {},
  Date: FakeDate,
  Math,
  Number,
  Boolean,
  String,
  Array,
  Object,
  JSON,
});

if (!windowObject.celiaMotionDirector) throw new Error("motion director not installed");
const director = windowObject.celiaMotionDirector;
for (const name of [
  "neutral", "happy", "in_love", "sad", "angry", "surprised",
  "confused", "scared", "teasing", "funny", "holding_back_laughter", "passionate",
]) {
  if (!director.profiles[name]) throw new Error(`missing profile ${name}`);
}

const angryNames = [
  ...director.profiles.angry.idle,
  ...director.profiles.angry.speaking,
  ...director.profiles.angry.reaction,
].join("|");
for (const forbidden of ["Arena Hype", "Full Send", "Dance Light", "Tempestade"]) {
  if (angryNames.includes(forbidden)) throw new Error(`angry profile dances: ${forbidden}`);
}

director.debug.resetPose();
director.setGazeTarget(0, 0, { mode: "camera", holdMs: Infinity, headDelayMs: 0, blink: false });
windowObject.dispatchEvent(new FakeCustomEvent("celia-emotion", {
  detail: { emotion: "confused", source: "confused-regression", metadata: { turn_id: "turn-regression" } },
}));
const confusedXSamples = [];
for (let frame = 0; frame < 90; frame++) {
  now += 1000 / 60;
  cubism.update();
  confusedXSamples.push(values[ids.indexOf("AngleX")]);
}
const confusedMinX = Math.min(...confusedXSamples);
const confusedMaxX = Math.max(...confusedXSamples);
if (!(confusedMinX < -0.7 && confusedMaxX > 0.7)) {
  throw new Error(`confused lateral gesture did not cross both sides: min=${confusedMinX}, max=${confusedMaxX}`);
}

windowObject.dispatchEvent(new FakeCustomEvent("celia-emotion", {
  detail: { emotion: "happy", source: "test" },
}));
cubism.update();
let state = director.state();
if (state.emotion !== "happy" || state.state !== "REACTION") {
  throw new Error(`emotion/reaction failed: ${JSON.stringify(state)}`);
}
windowObject.dispatchEvent(new FakeCustomEvent("celia-emotion", {
  detail: { emotion: "neutral", source: "test" },
}));
now += 1000;
cubism.update();
state = director.state();
if (state.emotion !== "neutral" || state.previousEmotion !== "happy") {
  throw new Error(`explicit neutral transition failed: ${JSON.stringify(state)}`);
}
windowObject.dispatchEvent(new FakeCustomEvent("celia-emotion", {
  detail: { emotion: "angry", source: "test" },
}));
cubism.update();
state = director.state();
if (state.emotion !== "angry" || state.previousEmotion !== "neutral" || state.emotionTransitionMsLeft <= 0) {
  throw new Error(`direct emotion transition failed: ${JSON.stringify(state)}`);
}
if (["Arena Hype", "Full Send", "Dance Light", "Tempestade"].includes(state.preset)) {
  throw new Error(`angry transition kept an incompatible preset: ${JSON.stringify(state)}`);
}
for (let frame = 0; frame < 6; frame++) { now += 1000 / 60; cubism.update(); }
if (!["AngleX", "AngleY", "AngleZ"].some((id) => Math.abs(values[ids.indexOf(id)]) > 0.01)) {
  throw new Error("head motion was not applied after smooth reaction onset");
}

director.setEmotion("confused", { source: "test-confused", metadata: { turn_id: "turn-confused-1" } });
state = director.state();
const confusedSpan = state.confusedShakeUntil - state.confusedShakeStartedAt;
if (!(confusedSpan > 1000)) throw new Error(`confused shake duration missing: ${JSON.stringify(state)}`);
const confusedFirstStart = state.confusedShakeStartedAt;
const confusedShakeSamples = [0.25, 0.50, 0.75].map((fraction) =>
  director.debug.confusedShakeOffset(state.confusedShakeStartedAt + confusedSpan * fraction)
);
if (!(Math.min(...confusedShakeSamples) < -2.5 && Math.max(...confusedShakeSamples) > 2.5)) {
  throw new Error(`confused shake did not alternate horizontally: ${JSON.stringify(confusedShakeSamples)}`);
}
if (director.debug.confusedShakeOffset(state.confusedShakeUntil + 10000) !== 0) {
  throw new Error("confused shake restarted by itself after the one-shot gesture");
}
now = state.confusedShakeUntil + 10000;
director.setEmotion("confused", { source: "test-confused", metadata: { turn_id: "turn-confused-1" } });
state = director.state();
if (state.confusedShakeStartedAt !== confusedFirstStart) {
  throw new Error(`duplicate confused event restarted the gesture: ${JSON.stringify(state)}`);
}
director.setEmotion("confused", { source: "test-confused", metadata: { turn_id: "turn-confused-2" } });
state = director.state();
if (!(state.confusedShakeStartedAt > confusedFirstStart) || state.confusedShakeTurnKey !== "turn-confused-2") {
  throw new Error(`new confused turn did not start one new gesture: ${JSON.stringify(state)}`);
}
if (!source.includes("const confusedShake = confusedShakeOffset(now);")) {
  throw new Error("confused shake is not connected to the horizontal motion channel");
}
director.setEmotion("angry", { source: "test-after-confused" });
state = director.state();
if (state.confusedShakeUntil !== 0 || state.confusedShakeTurnKey !== "") {
  throw new Error(`confused shake did not stop on emotion change: ${JSON.stringify(state)}`);
}
director.setEmotion("confused", { source: "sincronizacao", metadata: { turn_id: "old-turn" } });
state = director.state();
if (state.confusedShakeUntil !== 0 || state.confusedShakeStartedAt !== 0) {
  throw new Error(`Pet reconnect incorrectly replayed an old confused gesture: ${JSON.stringify(state)}`);
}
director.setEmotion("angry", { source: "test-resume" });

const flushed = flushedFrames.at(-1);
if (!flushed || (Math.abs(flushed.AngleY) < 0.01 && Math.abs(flushed.BodyY) < 0.01 && Math.abs(flushed.Shoulder) < 0.01)) {
  throw new Error(`motion was written after the Cubism flush: ${JSON.stringify(flushed)}`);
}

const mouthOpenBefore = values[ids.indexOf("ParamMouthOpenY")];
const mouthFormBefore = values[ids.indexOf("ParamMouthForm")];
windowObject.dispatchEvent(new FakeCustomEvent("celia-speech-energy", {
  detail: { energy: 0.72, slope: 0.12, running: true },
}));
now += 2000;
cubism.update();
state = director.state();
if (state.state !== "SPEAKING") throw new Error(`speaking state failed: ${state.state}`);
if (Math.abs(values[ids.indexOf("ParamMouthOpenY")] - mouthOpenBefore) > 1e-6) {
  throw new Error("motion director touched mouth-open ownership");
}
if (Math.abs(values[ids.indexOf("ParamMouthForm")] - mouthFormBefore) > 1e-6) {
  throw new Error("motion director touched mouth-form ownership");
}
if (!state.mapped.shoulderSway || !state.mapped.breath || state.mapped.hair.length < 4) {
  throw new Error(`real model mapping incomplete: ${JSON.stringify(state.mapped)}`);
}

windowObject.dispatchEvent(new FakeCustomEvent("celia-lipsync-stop", {
  detail: { speechId: "test" },
}));
// Allow the longest half-speed reaction to finish after speech stops.
now += 2000;
cubism.update();
state = director.state();
if (state.state !== "IDLE") throw new Error(`idle recovery failed: ${state.state}`);

if (source.includes("ws://") || source.includes(":8001") || source.includes(":8770")) {
  throw new Error("motion director reintroduced external VTS transport");
}
if (typeof director.setGazeTarget !== "function" || typeof director.metrics !== "function") {
  throw new Error("attention/telemetry API missing");
}
// Small target stays eye-only. Large target is ballistic in the eyes first,
// then recruits the head after its explicit delay and counter-rotates the eye.
director.setGazeTarget(.25, 0, { mode: "test-small", holdMs: 1000, headDelayMs: 80 });
now += 100; cubism.update();
let gaze = director.state().gaze;
if (Math.abs(gaze.eyeX) < .03 || Math.abs(gaze.headX) > .15) {
  throw new Error(`small gaze recruited head: ${JSON.stringify(gaze)}`);
}
director.setGazeTarget(.80, 0, { mode: "test-large", holdMs: 1200, headDelayMs: 120 });
now += 60; cubism.update();
const earlyHead = Math.abs(director.state().gaze.headX);
now += 220; cubism.update();
gaze = director.state().gaze;
if (!(Math.abs(gaze.headX) > earlyHead + .15)) {
  throw new Error(`large gaze never recruited delayed head: ${JSON.stringify(gaze)}`);
}
if (!(Math.abs(gaze.eyeX) < .80)) {
  throw new Error(`eye failed to counter-rotate as head arrived: ${JSON.stringify(gaze)}`);
}
director.setGazeTarget(0, 0, { mode: "camera", holdMs: Infinity, headDelayMs: 0 });
if (!source.includes("minimumJerk") || source.includes("headZ += posture.z")) {
  throw new Error("V12 intentional kinematics/emotion distribution contract missing");
}
if (director.metrics().samples < 1) throw new Error("motion telemetry did not sample");
const initialMetrics = director.metrics();
for (const key of ["headXPercentiles", "headZPercentiles", "headXNeutralPct", "headZNeutralPct", "headXMaxSameSideSec", "headZMaxSameSideSec", "headXAutocorrCurve", "eyeClipPct"]) {
  if (!(key in initialMetrics)) throw new Error(`missing motion metric ${key}`);
}
if (initialMetrics.eyeClipPct > 0) throw new Error(`normal gaze clipped eye target: ${initialMetrics.eyeClipPct}`);

// Exhausting the budget in the middle of a deep intentional pose must not abort
// or rescale the already-started minimum-jerk trajectory.
director.debug.resetPose();
if (!director.debug.forcePose("lean_forward")) throw new Error("debug lean-forward pose did not start");
const poseSamples = [];
let exhaustedBudget = null;
for (let frame = 0; frame < 58; frame++) {
  if (frame === 18) exhaustedBudget = director.debug.setMotionBudget(0);
  now += 1000 / 60;
  cubism.update();
  poseSamples.push(director.debug.poseVector().bodyY);
}
if (Math.max(...poseSamples) < .39) throw new Error(`budget aborted in-flight gesture: max=${Math.max(...poseSamples)}`);
let prevV = 0, prevA = 0, poseMaxJerk = 0;
for (let i = 1; i < poseSamples.length; i++) {
  const dt = 1 / 60;
  const v = (poseSamples[i] - poseSamples[i - 1]) / dt;
  const a = (v - prevV) / dt;
  if (i > 2) poseMaxJerk = Math.max(poseMaxJerk, Math.abs((a - prevA) / dt));
  prevV = v; prevA = a;
}
if (poseMaxJerk > 45) throw new Error(`minimum-jerk pose exceeded ceiling: ${poseMaxJerk}`);
if (exhaustedBudget !== 0) throw new Error("test failed to exhaust motion budget at mid-gesture boundary");
if (director.state().motionBudget > .20) throw new Error("motion budget recovered too fast during one gesture");

console.log("celia motion director runtime contract: PASS; in-flight budget + jerk ceiling PASS");

// Reproduce the SDK lifecycle: restore motion parameters, apply SDK drag,
// evaluate physics, then flush through both possible tracking wrapper orders.
const assert = require("assert");
for (const trackingFirst of [true, false]) {
  const v = new Float32Array(ids.length);
  const c = { parameters: { ...core.parameters, values: v } };
  let physicsInput, actualFlush, evaluations = 0;
  const ghostBase = [], ghostObserved = [];
  const fakeHairInput = {
    sourceParameterIndex: ids.indexOf("AngleX"),
    getNormalizedParameterValue(...args) { ghostObserved.push(Number(args[2])); },
  };
  const fakeVector = items => ({ at(index) { return items[index]; } });
  const fakeHairSetting = { inputCount:1, baseInputIndex:0, outputCount:1, baseOutputIndex:0 };
  const fakeHairOutput = { destinationParameterIndex: ids.indexOf("Param159") };
  const model = { getModel: () => c, update() { actualFlush = Array.from(v); } };
  const physics = {
    _physicsRig: {
      subRigCount: 1,
      settings: fakeVector([fakeHairSetting]),
      inputs: fakeVector([fakeHairInput]),
      outputs: fakeVector([fakeHairOutput]),
    },
    evaluate(m, dt) {
    assert.strictEqual(this, physics);
    assert.strictEqual(m, model);
    assert(dt > 0);
    evaluations++;
    physicsInput = Array.from(v);
    ghostBase.push(Number(v[ids.indexOf("AngleX")]));
    fakeHairInput.getNormalizedParameterValue({}, 0, v[ids.indexOf("AngleX")], -30, 30, 0, {}, {}, false, 1);
    v[ids.indexOf("ParamBodyAngleZ3")] = .123;
    // Stable synthetic Cubism physics output for the three visible face-hair chains.
    for (const [id, raw] of [
      ["Param155", .08], ["Param156", .10], ["Param157", .12],
      ["Param158", .08], ["Param159", .10], ["Param160", .12],
      ["Param194", .08], ["Param195", .10], ["Param196", .12],
      ["Param178", .04], ["Param179", .06], ["Param180", .08], ["Param181", .10],
      ["Param184", .04], ["Param185", .06], ["Param186", .08], ["Param187", .10],
    ]) v[ids.indexOf(id)] = raw;
  } };
  const app = { _model: model, _physics: physics, _dragX: .9, _dragY: -.5 };
  const timers = [];
  let seed = 123456;
  const seededMath = Object.create(Math);
  seededMath.random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296);
  const win = { getLAppAdapter: () => ({ getModel: () => app }), addEventListener() {} };
  const context = vm.createContext({ window: win, console: {log(){},warn:console.warn},
    Date: FakeDate, Math: seededMath, location: { search: "?pet=1" },
    setInterval(fn) { timers.push(fn); return timers.length; }, clearInterval() {} });
  function tracking() {
    vm.runInContext(fs.readFileSync("frontend/celia-tracking.js", "utf8"), context);
    win.celiaTracking.instalar();
  }
  if (trackingFirst) tracking();
  vm.runInContext(source, context);
  if (!trackingFirst) tracking();
  const d = win.celiaMotionDirector;
  assert.strictEqual(d.state().hookStage, "before-physics");
  let previous, maxStep = 0;
  const starValues = [], bodyValues = [];
  for (let frame = 0; frame < 360; frame++) {
    now += 1000 / 30;
    if (frame % 60 === 0) d.setEmotion(frame % 120 ? "neutral" : "happy");
    v.fill(0); // SDK loadParameters would discard a previous pre-flush write.
    v[ids.indexOf("ParamBodyAngleX")] = 9; // canonical SDK mouse contribution
    v[ids.indexOf("Param310")] = 1; // reproduce clamped native idle phase
    v[ids.indexOf("ParamEyeLOpen")] = 1;
    v[ids.indexOf("ParamEyeROpen")] = 1;
    physics.evaluate(model, 1 / 30);
    model.update();
    assert.strictEqual(d.state().frames, frame + 1, "director applied twice per render");
    for (const id of ["AngleX", "AngleY", "AngleZ", "ParamBodyAngleX", "Param93", "Param94"]) {
      const i = ids.indexOf(id);
      assert.strictEqual(actualFlush[i], physicsInput[i], "tracking overwrote physics driver " + id);
      assert(Number.isFinite(actualFlush[i]));
      if (previous) maxStep = Math.max(maxStep, Math.abs(actualFlush[i] - previous[i]));
    }
    assert(Math.abs(actualFlush[ids.indexOf("ParamBodyAngleZ3")] - .123) < 1e-6,
      "director overwrote secondary physics output");
    starValues.push(actualFlush[ids.indexOf("Param310")]);
    bodyValues.push(actualFlush[ids.indexOf("Param93")]);
    assert.strictEqual(actualFlush[ids.indexOf("Param290")], 0, "phase enabled a hidden expression");
    previous = actualFlush;
  }
  assert(evaluations === 360);
  assert(Math.max(...starValues) > .95 && Math.min(...starValues) < .05, "star phase froze");

  // Hair outputs remain native Cubism outputs. Extra inertia must enter only
  // through the selected sub-rig input; no post-physics pulling is allowed.
  assert.strictEqual(d.state().contributions.hairGhostInputHooks, 1,
    "selective hair input hook was not installed");
  assert(Math.abs(actualFlush[ids.indexOf("Param159")] - .10) < 1e-6,
    "director rewrote a face-hair physics output after evaluate()");
  assert(Math.abs(actualFlush[ids.indexOf("Param181")] - .10) < 1e-6,
    "director rewrote a neck-hair physics output after evaluate()");
  assert(ghostObserved.length === ghostBase.length && ghostObserved.length > 0,
    "hair input hook was not exercised");
  const maxGhostDelta = ghostObserved.reduce((m, value, index) =>
    Math.max(m, Math.abs(value - ghostBase[index])), 0);
  assert(maxGhostDelta > .001, "inertial hair input never received a ghost impulse");
  // Background motion only needs to stay alive here; the explicit deep-pose
  // amplitude and jerk contract is tested above with forcePose("lean_forward").
  assert(Math.max(...bodyValues) - Math.min(...bodyValues) > .45,
    "background vertical motion froze");
  assert.strictEqual(d.state().motionSpeed, 1);
  assert.strictEqual(d.state().proceduralSpeed, .82);
  assert.strictEqual(d.state().verticalProceduralSpeed, .62);
  assert(physicsInput.slice(0, 3).some(v => Math.abs(v) > .01), "physics never received head motion");
  assert(physicsInput.slice(3, 6).some(v => Math.abs(v) > .01), "physics never received body motion");
  assert(maxStep < 3, "discontinuous direction change: " + maxStep);
  assert(d.ownsParameter("AngleX", c));
  assert(!d.ownsParameter("AngleX", core), "ownership leaked across models");
  assert(!d.ownsParameter("ParamMouthOpenY", c));
  assert(!d.ownsParameter("ParamBodyAngleZ3", c));
  console.log("pre-physics + tracking order " + trackingFirst + ": PASS; max step=" + maxStep.toFixed(3));
}

// The 1.8-second sparkle clock must not slow down with body choreography.
for (const [timestamp, expected] of [[36000, 0], [36450, .5], [36900, 1]]) {
  now = timestamp;
  cubism.update();
  assert(Math.abs(values[ids.indexOf("Param310")] - expected) < 1e-5,
    "body speed changed the emotion sparkle clock");
}
console.log("independent sparkle clock: PASS");


function runFaceMemoryContract() {
  const faceEvents = [];
  const expressions = [];
  const faceListeners = new Map();
  const timeouts = [];
  const faceIntervals = [];
  let faceNow = 10000;
  class FaceDate extends Date { static now() { return faceNow; } }
  class FakeWS {
    constructor(url) { this.url = url; this.listeners = new Map(); this.readyState = FakeWS.OPEN; }
    addEventListener(type, listener) {
      const group = this.listeners.get(type) || [];
      group.push(listener);
      this.listeners.set(type, group);
    }
    send() {}
    emit(type, event) { for (const listener of this.listeners.get(type) || []) listener(event); }
  }
  FakeWS.CONNECTING = 0;
  FakeWS.OPEN = 1;
  let faceAdapter = {
    getExpressionName: index => `exp-${index}`,
    setExpression: name => expressions.push(name),
  };
  const faceWindow = {
    WebSocket: FakeWS,
    location: { href: "http://127.0.0.1:12393/?pet=1", search: "?pet=1" },
    getLAppAdapter: () => faceAdapter,
    CustomEvent: FakeCustomEvent,
    addEventListener(type, listener) {
      const group = faceListeners.get(type) || [];
      group.push(listener);
      faceListeners.set(type, group);
    },
    dispatchEvent(event) {
      faceEvents.push(event.detail);
      for (const listener of faceListeners.get(event.type) || []) listener(event);
    },
  };
  const faceContext = vm.createContext({
    window: faceWindow,
    console: { log(){}, warn: console.warn },
    setInterval(fn) { faceIntervals.push(fn); return faceIntervals.length; },
    setTimeout(fn, delay) { timeouts.push({ fn, due: faceNow + delay }); return timeouts.length; },
    clearTimeout() {},
    Date: FaceDate,
    URL,
    URLSearchParams,
    JSON,
    Object,
    String,
    Number,
    Boolean,
  });
  vm.runInContext(fs.readFileSync("frontend/celia-face.js", "utf8"), faceContext);
  const socket = new faceWindow.WebSocket("/client-ws");
  socket.emit("message", { data: JSON.stringify({ type: "celia-face", emotion: "happy", expression: 2, source: "test" }) });
  faceAdapter.setExpression("exp-0");
  if (expressions.join(",") !== "exp-2") throw new Error("external/IDLE reset bypassed affect owner");
  socket.emit("message", { data: JSON.stringify({ type: "celia-face", expression: 0, source: "response-without-tag" }) });
  if (expressions.join(",") !== "exp-2") throw new Error("response without emotion changed affect");
  socket.emit("message", { data: JSON.stringify({ type: "celia-face", emotion: "trol_idle", expression: 22, source: "speech:old" }) });
  if (expressions.join(",") !== "exp-2") throw new Error("audio activity changed affect");
  socket.emit("message", { data: JSON.stringify({ type: "celia-face", emotion: "neutral", expression: 0, source: "test" }) });
  if (expressions.join(",") !== "exp-2,exp-0") throw new Error("explicit neutral was not applied immediately: " + expressions.join(","));
  if (faceEvents.at(-1).emotion !== "neutral") throw new Error("explicit neutral event was not dispatched");
  socket.emit("message", { data: JSON.stringify({ type: "celia-face", emotion: "angry", expression: 3, source: "test" }) });
  if (expressions.at(-1) !== "exp-3") throw new Error("direct affect transition failed");
  if (faceEvents.at(-1).emotion !== "angry") throw new Error("angry event did not dispatch directly");

  socket.emit("message", { data: JSON.stringify({ type: "celia-face", emotion: "teasing", expression: 22, source: "test" }) });
  if (expressions.at(-1) !== "exp-22") throw new Error("teasing did not start with no-tongue speaking variant");
  faceWindow.dispatchEvent(new FakeCustomEvent("celia-lipsync-start", { detail: { speechId: "s1", playerId: "p1" } }));
  if (faceWindow.celiaRosto.estado().teasingVariant !== "speaking" || expressions.at(-1) !== "exp-22") {
    throw new Error("teasing speaking variant did not stay active during speech");
  }
  faceWindow.dispatchEvent(new FakeCustomEvent("celia-lipsync-stop", { detail: { speechId: "s1", playerId: "p1" } }));
  if (faceWindow.celiaRosto.estado().teasingVariant !== "final" || expressions.at(-1) !== "exp-21") {
    throw new Error("teasing did not switch to tongue-out final variant after speech");
  }
  faceWindow.dispatchEvent(new FakeCustomEvent("celia-lipsync-start", { detail: { speechId: "s2", playerId: "p2" } }));
  if (faceWindow.celiaRosto.estado().teasingVariant !== "speaking" || expressions.at(-1) !== "exp-22") {
    throw new Error("next teasing speech did not restore no-tongue variant");
  }

  socket.emit("message", { data: JSON.stringify({ type: "celia-face", emotion: "angry", expression: 3, source: "test" }) });
  const reconnected = [];
  faceAdapter = {
    getExpressionName: index => `exp-${index}`,
    setExpression: name => reconnected.push(name),
  };
  for (const tick of faceIntervals) tick();
  if (reconnected.at(-1) !== "exp-3") throw new Error("model reconnect did not restore current affect");
  console.log("face persistent affect contract: PASS");
}
runFaceMemoryContract();
