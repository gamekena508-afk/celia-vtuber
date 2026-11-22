/* Animated confusion accessory, separate from the Live2D model and its face. */
(function installCeliaConfusion() {
  "use strict";

  if (typeof document === "undefined") return;

  const ENTRY_GIF = "./media/celia-confusion/entrada.gif";
  const LOOP_GIF = "./media/celia-confusion/loop.gif";
  const ENTRY_DURATION_MS = 4050;
  const EXIT_DURATION_MS = 260;
  const MIN_SIZE_PX = 58;
  const MAX_SIZE_PX = 168;
  const MODEL_ANCHOR_X = 0.11;
  const MODEL_ANCHOR_Y = 0.88;
  const MODEL_BOUNDS_FALLBACK = Object.freeze({
    minX: -0.40, maxX: 0.40, minY: -0.96, maxY: 0.98,
  });
  const modelBoundsCache = new WeakMap();

  let accessory = null;
  let image = null;
  let entryTimer = null;
  let exitTimer = null;
  let trackingFrame = null;
  let generation = 0;
  let visible = false;
  let confusedRequested = false;
  let lastAnchor = null;

  function installStyle() {
    if (document.getElementById("celia-confusion-style")) return;
    const style = document.createElement("style");
    style.id = "celia-confusion-style";
    style.textContent = `
      #celia-confusion-accessory {
        position: fixed;
        left: 50%;
        top: 18%;
        width: 96px;
        aspect-ratio: 1;
        z-index: 2147483001;
        opacity: 0;
        transform: translateY(-20%) scale(.58);
        transform-origin: 50% 65%;
        pointer-events: none;
        transition: opacity ${EXIT_DURATION_MS}ms ease-out, transform ${EXIT_DURATION_MS}ms ease-out;
      }
      #celia-confusion-accessory.is-visible { opacity: 1; transform: translateY(-20%) scale(1); }
      #celia-confusion-accessory.is-entering { transition: opacity 380ms ease-out, transform 480ms cubic-bezier(.18, 1.45, .42, 1); }
      #celia-confusion-accessory.is-looping { animation: celia-confusion-float 2.4s ease-in-out infinite; }
      #celia-confusion-accessory img { display: block; width: 100%; height: 100%; object-fit: contain; }
      @keyframes celia-confusion-float {
        0%, 100% { transform: translateY(-20%) scale(1); }
        50% { transform: translateY(calc(-20% - 9px)) scale(1); }
      }
    `;
    document.head.appendChild(style);
  }

  function ensureAccessory() {
    if (accessory) return true;
    if (!document.body) return false;
    installStyle();
    accessory = document.createElement("div");
    accessory.id = "celia-confusion-accessory";
    accessory.hidden = true;
    accessory.setAttribute("aria-hidden", "true");
    image = document.createElement("img");
    image.alt = "";
    image.decoding = "async";
    accessory.appendChild(image);
    document.body.appendChild(accessory);
    return true;
  }

  function finite(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function live2dIsVisible() {
    const wrapper = document.getElementById("live2d-internal-wrapper");
    const canvas = document.getElementById("canvas");
    const target = wrapper || canvas;
    if (!target || typeof target.getBoundingClientRect !== "function") return false;
    if (wrapper && wrapper.dataset && wrapper.dataset.celiaDesktopPetDetached === "true") return false;
    if (target.style && (target.style.display === "none" || target.style.visibility === "hidden")) return false;
    if (typeof window.getComputedStyle === "function") {
      const style = window.getComputedStyle(target);
      if (style && (style.display === "none" || style.visibility === "hidden")) return false;
    }
    const rect = target.getBoundingClientRect();
    return Boolean(rect && rect.width > 0 && rect.height > 0);
  }

  function live2dRect() {
    if (!live2dIsVisible()) return null;
    const canvas = document.getElementById("canvas");
    if (canvas && typeof canvas.getBoundingClientRect === "function") {
      const rect = canvas.getBoundingClientRect();
      if (rect && rect.width > 0 && rect.height > 0) return rect;
    }
    const wrapper = document.getElementById("live2d-internal-wrapper");
    if (wrapper && typeof wrapper.getBoundingClientRect === "function") {
      const rect = wrapper.getBoundingClientRect();
      if (rect && rect.width > 0 && rect.height > 0) return rect;
    }
    return null;
  }

  function live2dGeometry() {
    try {
      const adapter = window.getLAppAdapter && window.getLAppAdapter();
      const model = adapter && typeof adapter.getModel === "function" ? adapter.getModel() : null;
      const core = model && model._model;
      const matrix = model && model._modelMatrix;
      if (
        !core
        || !matrix
        || typeof matrix.transformX !== "function"
        || typeof matrix.transformY !== "function"
      ) return null;
      return { core, matrix };
    } catch (_) {
      return null;
    }
  }

  function modelBounds(core) {
    if (!core) return MODEL_BOUNDS_FALLBACK;
    const cached = modelBoundsCache.get(core);
    if (cached) return cached;
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    try {
      const count = Number(core.getDrawableCount && core.getDrawableCount()) || 0;
      for (let index = 0; index < count; index += 1) {
        const positions = core.getDrawableVertexPositions(index);
        if (!positions) continue;
        for (let offset = 0; offset + 1 < positions.length; offset += 2) {
          const x = Number(positions[offset]);
          const y = Number(positions[offset + 1]);
          if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
          minX = Math.min(minX, x);
          maxX = Math.max(maxX, x);
          minY = Math.min(minY, y);
          maxY = Math.max(maxY, y);
        }
      }
    } catch (_) {}
    const bounds = (
      Number.isFinite(minX)
      && Number.isFinite(maxX)
      && Number.isFinite(minY)
      && Number.isFinite(maxY)
      && maxX > minX
      && maxY > minY
    )
      ? { minX, maxX, minY, maxY }
      : MODEL_BOUNDS_FALLBACK;
    modelBoundsCache.set(core, bounds);
    return bounds;
  }

  function projectionFor(rect) {
    if (rect.width > rect.height) {
      return { x: rect.height / rect.width, y: 1 };
    }
    if (rect.height > rect.width) {
      return { x: 1, y: rect.width / rect.height };
    }
    return { x: 1, y: 1 };
  }

  function modelPointToPixel(rect, matrix, modelX, modelY) {
    const projection = projectionFor(rect);
    const clipX = matrix.transformX(modelX) * projection.x;
    const clipY = matrix.transformY(modelY) * projection.y;
    return {
      x: rect.left + ((clipX + 1) * rect.width) / 2,
      y: rect.top + ((1 - clipY) * rect.height) / 2,
      projection,
    };
  }

  function computeAnchor() {
    const rect = live2dRect();
    if (!rect) return null;
    const geometry = live2dGeometry();
    if (!geometry) {
      const size = clamp(rect.height * 0.09, MIN_SIZE_PX, MAX_SIZE_PX);
      return {
        left: rect.left + rect.width * 0.34,
        top: rect.top + Math.max(18, rect.height * 0.12),
        size,
        centerX: rect.left + rect.width * 0.34 + size * 0.5,
        centerY: rect.top + Math.max(18, rect.height * 0.12) + size * 0.5,
        scale: 1,
        modelX: null,
        modelY: null,
        clamped: false,
      };
    }

    const { core, matrix } = geometry;
    const bounds = modelBounds(core);
    const modelX = bounds.minX + (bounds.maxX - bounds.minX) * MODEL_ANCHOR_X;
    const modelY = bounds.minY + (bounds.maxY - bounds.minY) * MODEL_ANCHOR_Y;
    const point = modelPointToPixel(rect, matrix, modelX, modelY);
    const scaleX = Math.abs(finite(matrix.getScaleX && matrix.getScaleX(), 1));
    const scaleY = Math.abs(finite(matrix.getScaleY && matrix.getScaleY(), scaleX));
    const pxPerUnitX = scaleX * point.projection.x * rect.width * 0.5;
    const pxPerUnitY = scaleY * point.projection.y * rect.height * 0.5;
    const pxPerUnit = Math.max(1, (pxPerUnitX + pxPerUnitY) * 0.5);
    const size = clamp(pxPerUnit * 0.20, MIN_SIZE_PX, MAX_SIZE_PX);

    const margin = 8;
    const rawLeft = point.x - size * 0.5;
    const rawTop = point.y - size * 0.5;
    const minLeft = rect.left + margin;
    const maxLeft = Math.max(minLeft, rect.left + rect.width - size - margin);
    // CSS floats the symbol upward by 20% (+9px at the peak), so reserve that
    // space here instead of allowing the visible GIF to clip above the canvas.
    const minTop = rect.top + margin + size * 0.20 + 10;
    const maxTop = Math.max(minTop, rect.top + rect.height - size - margin);
    const left = clamp(rawLeft, minLeft, maxLeft);
    const top = clamp(rawTop, minTop, maxTop);

    return {
      left,
      top,
      size,
      centerX: point.x,
      centerY: point.y,
      scale: (scaleX + scaleY) * 0.5,
      modelX,
      modelY,
      clamped: Math.abs(left - rawLeft) > 0.1 || Math.abs(top - rawTop) > 0.1,
    };
  }

  function updateAnchor() {
    if (!accessory || !visible) return false;
    const anchor = computeAnchor();
    if (!anchor) return false;
    accessory.style.left = `${Math.round(anchor.left * 10) / 10}px`;
    accessory.style.top = `${Math.round(anchor.top * 10) / 10}px`;
    accessory.style.width = `${Math.round(anchor.size * 10) / 10}px`;
    lastAnchor = anchor;
    return true;
  }

  function stopTracking() {
    if (trackingFrame !== null) cancelAnimationFrame(trackingFrame);
    trackingFrame = null;
  }

  function trackAnchor() {
    trackingFrame = null;
    if (!visible) return;
    updateAnchor();
    trackingFrame = requestAnimationFrame(trackAnchor);
  }

  function startTracking() {
    updateAnchor();
    if (trackingFrame === null) trackingFrame = requestAnimationFrame(trackAnchor);
  }

  function clearTimers() {
    if (entryTimer) clearTimeout(entryTimer);
    if (exitTimer) clearTimeout(exitTimer);
    entryTimer = null;
    exitTimer = null;
  }

  function showVisual() {
    if (!ensureAccessory() || !live2dIsVisible()) return;
    if (visible) {
      startTracking();
      return;
    }
    clearTimers();
    generation += 1;
    const currentGeneration = generation;
    visible = true;
    accessory.hidden = false;
    accessory.classList.remove("is-looping");
    accessory.classList.add("is-entering");
    image.src = ENTRY_GIF;
    startTracking();
    requestAnimationFrame(() => {
      if (visible && currentGeneration === generation) accessory.classList.add("is-visible");
    });
    entryTimer = setTimeout(() => {
      if (!visible || currentGeneration !== generation) return;
      accessory.classList.remove("is-entering");
      accessory.classList.add("is-looping");
      image.src = LOOP_GIF;
    }, ENTRY_DURATION_MS);
  }

  function hideVisual() {
    if (!accessory || !visible) return;
    clearTimers();
    generation += 1;
    visible = false;
    stopTracking();
    accessory.classList.remove("is-entering", "is-looping", "is-visible");
    exitTimer = setTimeout(() => {
      if (visible) return;
      accessory.hidden = true;
      image.removeAttribute("src");
    }, EXIT_DURATION_MS);
  }

  function syncVisibility() {
    if (confusedRequested && live2dIsVisible()) showVisual();
    else hideVisual();
  }

  function show() {
    confusedRequested = true;
    syncVisibility();
  }

  function hide() {
    confusedRequested = false;
    hideVisual();
  }

  function handleMessage(raw) {
    let message;
    try {
      message = JSON.parse(raw);
    } catch (_) {
      return;
    }
    if (!message || message.type !== "celia-face" || typeof message.emotion !== "string") return;
    const emotion = message.emotion.trim().toLowerCase();
    const source = String(message.source || "").trim().toLowerCase();
    if (["trol_speaking", "trol_idle"].includes(emotion) || source === "interrupt" || source === "chat_input") return;
    if (emotion === "confused") show();
    else if (emotion) hide();
  }

  function handleEmotionEvent(event) {
    const detail = event && event.detail ? event.detail : {};
    const emotion = typeof detail.emotion === "string"
      ? detail.emotion.trim().toLowerCase()
      : "";
    if (!emotion) return;
    if (emotion === "confused") show();
    else hide();
  }

  // celia-face.js is the only WebSocket-to-affect translator. Both the
  // Motion Director and this accessory consume the same canonical event.
  window.addEventListener("celia-emotion", handleEmotionEvent);
  window.addEventListener("resize", syncVisibility);
  window.addEventListener("celia-browser-pet-mode", syncVisibility);
  window.addEventListener("celia-desktop-pet-launch", syncVisibility);

  window.celiaConfusao = {
    show,
    hide,
    handleMessage,
    updateAnchor,
    state: () => ({
      visible,
      requested: confusedRequested,
      avatarVisible: live2dIsVisible(),
      source: image ? image.getAttribute("src") : null,
      tracking: trackingFrame !== null,
      anchor: lastAnchor ? { ...lastAnchor } : null,
    }),
  };
})();
