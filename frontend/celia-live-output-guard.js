/* liveOutput=1 guard. Must load synchronously BEFORE celia-escolha.js, which
 * starts the deferred bundle (and its WebGL contexts) on its own schedule.
 *  1) the page only renders the broadcast: every camera/microphone/screen
 *     capture is refused with NotAllowedError before the native call;
 *  2) WebGL contexts are created with preserveDrawingBuffer:true so the studio
 *     composite (drawImage of the Live2D canvas) is not blank.
 * Any other URL (no liveOutput=1) is left completely untouched.
 */
(function installLiveOutputGuard() {
  "use strict";
  let liveOutput = false;
  try { liveOutput = new URLSearchParams(window.location.search).get("liveOutput") === "1"; } catch (_) {}
  if (!liveOutput) return;

  if (navigator.mediaDevices) {
    const deny = (what) => function deniedCapture() {
      return Promise.reject(new DOMException(what + " blocked: liveOutput mode", "NotAllowedError"));
    };
    for (const name of ["getUserMedia", "getDisplayMedia"]) {
      try {
        Object.defineProperty(navigator.mediaDevices, name, {value: deny(name), configurable: true, writable: true});
      } catch (_) {
        try { navigator.mediaDevices[name] = deny(name); } catch (__) {}
      }
    }
    for (const legacy of ["getUserMedia", "webkitGetUserMedia", "mozGetUserMedia"]) {
      if (typeof navigator[legacy] === "function") {
        try { navigator[legacy] = function (c, ok, fail) { if (typeof fail === "function") fail(new DOMException("blocked: liveOutput mode", "NotAllowedError")); }; } catch (_) {}
      }
    }
  }

  if (typeof HTMLCanvasElement !== "undefined" && HTMLCanvasElement.prototype
      && typeof HTMLCanvasElement.prototype.getContext === "function") {
    const nativeGetContext = HTMLCanvasElement.prototype.getContext;
    const GL_TYPES = new Set(["webgl", "webgl2", "experimental-webgl"]);
    try {
      HTMLCanvasElement.prototype.getContext = function liveOutputGetContext(type, attrs, ...rest) {
        if (GL_TYPES.has(type)) {
          const forced = Object.assign({}, attrs && typeof attrs === "object" ? attrs : null, {preserveDrawingBuffer: true});
          return nativeGetContext.call(this, type, forced, ...rest);
        }
        return nativeGetContext.call(this, type, attrs, ...rest);
      };
    } catch (_) {}
  }
})();
