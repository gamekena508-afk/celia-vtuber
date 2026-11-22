/* liveOutput=1 only: transparent colour clear for the Live2D canvas.
 * Must load synchronously AFTER celia-live-output-guard.js (which forces
 * preserveDrawingBuffer:true) and BEFORE celia-escolha.js (which starts the bundle).
 *
 * Cause (measured): the compiled Live2D frame loop calls gl.clearColor(0,0,0,1) and
 * then gl.clear(gl.DEPTH_BUFFER_BIT) only. With preserveDrawingBuffer:true the colour
 * of the previous frame is never cleared, so the studio composite shows ghost copies.
 *
 * Scope, deliberately narrow:
 *  - only when ?liveOutput=1; any other URL is untouched;
 *  - only the canvas whose id is "canvas" (the Live2D stage);
 *  - only gl.clear(mask) with mask === DEPTH_BUFFER_BIT exactly;
 *  - only when the DRAW framebuffer is the default one (null), so offscreen
 *    framebuffers are never affected;
 *  - for that one call the colour clear is transparent (0,0,0,0) and the colour
 *    write mask is forced on; clearColor and colorMask are restored in a finally.
 *    The scissor test and depth mask are not changed, so the region and depth
 *    behaviour are the same as the original call.
 * It does not touch device guards, the compiled bundle or the studio.
 */
(function installLiveFrameClear() {
  "use strict";
  let liveOutput = false;
  try { liveOutput = new URLSearchParams(window.location.search).get("liveOutput") === "1"; } catch (_) {}
  if (!liveOutput) return;
  if (typeof HTMLCanvasElement === "undefined" || !HTMLCanvasElement.prototype
      || typeof HTMLCanvasElement.prototype.getContext !== "function") return;

  const proto = HTMLCanvasElement.prototype;
  if (Object.prototype.hasOwnProperty.call(proto, "__celiaLiveFrameClear")) return; // install once

  const GL_TYPES = new Set(["webgl", "webgl2", "experimental-webgl"]);
  const decorated = new WeakSet();
  const previousGetContext = proto.getContext; // the guard's wrapper, chained

  function drawFramebufferIsDefault(gl) {
    const key = gl.DRAW_FRAMEBUFFER_BINDING !== undefined ? gl.DRAW_FRAMEBUFFER_BINDING : gl.FRAMEBUFFER_BINDING;
    return gl.getParameter(key) === null;
  }

  function decorate(gl) {
    if (decorated.has(gl)) return;
    decorated.add(gl);
    const nativeClear = gl.clear;
    gl.clear = function celiaLiveClear(mask) {
      if (mask !== gl.DEPTH_BUFFER_BIT || !drawFramebufferIsDefault(gl)) {
        return nativeClear.apply(this, arguments);
      }
      const prevColor = gl.getParameter(gl.COLOR_CLEAR_VALUE);
      const prevWrite = gl.getParameter(gl.COLOR_WRITEMASK);
      try {
        gl.clearColor(0, 0, 0, 0);
        gl.colorMask(true, true, true, true);
        return nativeClear.call(this, gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      } finally {
        gl.clearColor(prevColor[0], prevColor[1], prevColor[2], prevColor[3]);
        gl.colorMask(prevWrite[0], prevWrite[1], prevWrite[2], prevWrite[3]);
      }
    };
  }

  proto.getContext = function celiaLiveFrameClearGetContext(type, attrs, ...rest) {
    const ctx = previousGetContext.call(this, type, attrs, ...rest);
    if (ctx && GL_TYPES.has(type) && this && this.id === "canvas") decorate(ctx);
    return ctx;
  };
  Object.defineProperty(proto, "__celiaLiveFrameClear", {value: true});
})();
