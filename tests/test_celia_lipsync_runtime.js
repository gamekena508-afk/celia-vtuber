const fs = require("fs");
const vm = require("vm");

const listeners = new Map();
const core = {
  parameters: {
    ids: ["ParamMouthOpenY", "ParamMouthForm"],
    values: [0, 0],
    minimumValues: [0, -1],
    maximumValues: [1, 1],
  },
};
let renderedMouth;
const cubism = {
  getModel: () => core,
  update: () => { renderedMouth = [...core.parameters.values]; },
};
const model = { _model: cubism };
const adapter = { getModel: () => model };
const windowObject = {
  getLAppAdapter: () => adapter,
  CustomEvent: class CustomEvent {
    constructor(type, init) {
      this.type = type;
      this.detail = init.detail;
    }
  },
  addEventListener(type, listener) {
    listeners.set(type, listener);
  },
  dispatchEvent(event) {
    listeners.get(event.type)?.(event);
  },
};

const source = fs.readFileSync("frontend/celia-lipsync.js", "utf8");
vm.runInNewContext(source, {
  window: windowObject,
  setInterval: () => 1,
  clearInterval: () => {},
  Math,
  Number,
  Boolean,
  String,
});

const audio = { currentTime: 0.08 };
windowObject.dispatchEvent(new windowObject.CustomEvent("celia-lipsync-start", {
  detail: { audio, speechId: "speech-test", volumes: [1, 1, 1], sliceLengthMs: 20 },
}));
cubism.update();
const openWhileSpeaking = core.parameters.values[0];
if (renderedMouth[0] !== openWhileSpeaking || !(renderedMouth[0] > 0)) {
  throw new Error("mouth pose must reach Cubism in the same frame");
}
// Simulate native motion restoring mouth parameters before each render.
for (let i = 0; i < 4; i++) {
  core.parameters.values[0] = 0;
  cubism.update();
  if (!(renderedMouth[0] > 0)) throw new Error("native motion erased speech pose");
}
if (!(openWhileSpeaking > 0 && openWhileSpeaking <= 0.58)) {
  throw new Error(`mouth cap failed: ${openWhileSpeaking}`);
}

windowObject.dispatchEvent(new windowObject.CustomEvent("celia-lipsync-stop", {
  detail: { speechId: "speech-test" },
}));
for (let index = 0; index < 30; index += 1) cubism.update();
if (core.parameters.values[0] > 0.02) {
  throw new Error(`mouth did not release: ${core.parameters.values[0]}`);
}

console.log("celia-lipsync runtime contract: PASS");
