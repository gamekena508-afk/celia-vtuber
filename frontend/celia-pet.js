/* Native desktop-pet controls. Electron exposes the narrow window API. */
(function installCeliaPet() {
  "use strict";

  const pet = new URLSearchParams(location.search).get("pet") === "1";
  if (!pet) return;

  let locked = false;
  let interactionArmed = false;
  let fullscreenMode = false;
  let dragging = null;
  let pendingMove = null;
  let displayLayout = [];
  let activeDisplayId = null;
  let normalizedSurfaceHost = null;
  let surfaceObserver = null;
  let surfaceRepairScheduled = false;
  const api = () => {
    if (window.pywebview && window.pywebview.api) return window.pywebview.api;
    return window.celiaPetNative || null;
  };

  function updateLock(nextLocked) {
    locked = Boolean(nextLocked);
    document.body.classList.toggle("celia-pet-locked", locked);
  }

  function normalizeLive2DSurface() {
    const wrapper = document.getElementById("live2d-internal-wrapper");
    const host = wrapper && wrapper.parentElement;
    if (!wrapper || !host) return false;
    if (host === normalizedSurfaceHost) return true;
    Object.assign(host.style, {
      position: "fixed",
      left: "0px",
      top: "0px",
      right: "auto",
      bottom: "auto",
      width: "100vw",
      height: "100vh",
      maxWidth: "none",
      maxHeight: "none",
      margin: "0",
      padding: "0",
      overflow: "hidden",
    });
    Object.assign(wrapper.style, {
      width: "100vw",
      height: "100vh",
      maxWidth: "none",
      maxHeight: "none",
    });
    normalizedSurfaceHost = host;
    document.documentElement.dataset.celiaPetSurfaceNormalized = "true";
    requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
    return true;
  }

  function watchLive2DSurface() {
    normalizeLive2DSurface();
    if (surfaceObserver) return;
    surfaceObserver = new MutationObserver(() => {
      if (surfaceRepairScheduled) return;
      surfaceRepairScheduled = true;
      requestAnimationFrame(() => {
        surfaceRepairScheduled = false;
        normalizeLive2DSurface();
      });
    });
    surfaceObserver.observe(document.documentElement, { childList: true, subtree: true });
  }

  function prepareSurface() {
    for (const element of [document.documentElement, document.body]) {
      Object.assign(element.style, {
        overflow: "hidden",
        overscrollBehavior: "none",
        touchAction: "none",
        background: "transparent",
        backgroundColor: "transparent",
      });
    }
    document.documentElement.dataset.celiaPetFraming = "bust";
    updateLock(false);
  }

  function matrixDeltaFromPixels(dx, dy) {
    const canvas = document.getElementById("canvas");
    const rect = canvas && canvas.getBoundingClientRect();
    const width = rect && rect.width > 0 ? rect.width : window.innerWidth;
    const height = rect && rect.height > 0 ? rect.height : window.innerHeight;
    return {
      x: width > 0 ? (dx * 2) / width : 0,
      y: height > 0 ? (dy * 2) / height : 0,
    };
  }

  async function loadState() {
    if (!api()) return;
    const state = await api().get_state();
    fullscreenMode = state.fixed_surface === true || state.fullscreen === true || state.desktop_span === true;
    displayLayout = Array.isArray(state.displays) ? state.displays : [];
    const primaryDisplay = displayLayout.find((display) => display.primary) || displayLayout[0];
    activeDisplayId = primaryDisplay ? String(primaryDisplay.id) : null;
    updateLock(state.locked);
    // Affect belongs to the canonical Emotion Bus sync. The Pet must never
    // select a local face here or it can race a freshly synchronized emotion.
  }

  function displayForPoint(x, y) {
    if (!displayLayout.length) return null;
    const inside = displayLayout.find((display) => {
      const bounds = display.bounds;
      return x >= bounds.x && x < bounds.x + bounds.width && y >= bounds.y && y < bounds.y + bounds.height;
    });
    if (inside) return inside;
    let best = null;
    let bestDistance = Infinity;
    for (const display of displayLayout) {
      const bounds = display.bounds;
      const nearestX = Math.max(bounds.x, Math.min(x, bounds.x + bounds.width));
      const nearestY = Math.max(bounds.y, Math.min(y, bounds.y + bounds.height));
      const distance = (x - nearestX) ** 2 + (y - nearestY) ** 2;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = display;
      }
    }
    return best;
  }

  function preserveFloorAcrossDisplays(x, y) {
    if (!fullscreenMode || displayLayout.length < 2) return;
    const nextDisplay = displayForPoint(x, y);
    if (!nextDisplay) return;
    const previousDisplay = displayLayout.find((display) => String(display.id) === activeDisplayId);
    if (!previousDisplay) {
      activeDisplayId = String(nextDisplay.id);
      return;
    }
    if (String(nextDisplay.id) === activeDisplayId) return;
    const previousBottom = previousDisplay.bounds.y + previousDisplay.bounds.height;
    const nextBottom = nextDisplay.bounds.y + nextDisplay.bounds.height;
    const body = window.celiaLive2DBody;
    if (body && typeof body.moveBy === "function") {
      const delta = matrixDeltaFromPixels(0, previousBottom - nextBottom);
      body.moveBy(0, delta.y);
    }
    activeDisplayId = String(nextDisplay.id);
  }

  function queueMove(dx, dy) {
    if (!api()) return;
    if (fullscreenMode) {
      // A fullscreen surface must not be moved with setBounds: that causes a
      // compositor reconfigure/flicker. Move only the Live2D model matrix.
      const body = window.celiaLive2DBody;
      if (body && typeof body.moveBy === "function") {
        const delta = matrixDeltaFromPixels(dx, dy);
        body.moveBy(delta.x, -delta.y);
      }
      return;
    }
    pendingMove = pendingMove ? { dx: pendingMove.dx + dx, dy: pendingMove.dy + dy } : { dx, dy };
    requestAnimationFrame(async () => {
      if (!pendingMove) return;
      const move = pendingMove;
      pendingMove = null;
      const state = await api().move_by(move.dx, move.dy);
      updateLock(state.locked);
    });
  }

  function returnToGhostMode() {
    if (!interactionArmed) return;
    interactionArmed = false;
    endDrag();
    const native = api();
    if (native && typeof native.set_mouse_ignored === "function") {
      native.set_mouse_ignored(true);
    }
  }

  function armInteraction() {
    interactionArmed = true;
  }

  function disarmInteraction() {
    interactionArmed = false;
    endDrag();
  }

  function endDrag() {
    dragging = null;
    if (document.pointerLockElement) document.exitPointerLock();
  }

  document.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !interactionArmed) return;
    const x = event.screenX || event.clientX;
    const y = event.screenY || event.clientY;
    preserveFloorAcrossDisplays(x, y);
    dragging = { x, y };
    event.currentTarget.setPointerCapture && event.currentTarget.setPointerCapture(event.pointerId);
  }, true);
  document.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const x = event.screenX || event.clientX;
    const y = event.screenY || event.clientY;
    preserveFloorAcrossDisplays(x, y);
    const dx = x - dragging.x;
    const dy = y - dragging.y;
    queueMove(dx, dy);
    dragging = { x, y };
  }, true);
  document.addEventListener("pointerup", endDrag, true);
  document.addEventListener("pointercancel", endDrag, true);
  document.addEventListener("wheel", async (event) => {
    // A roda NUNCA chega ao escalador do bundle compilado no Pet, armado ou nao. Medido em 2026-10-18: com o Pet
    // fantasma (ou desarmado pelos 700 ms do gate) a roda passava para o canvas, o bundle iniciava um laco de easing
    // por requestAnimationFrame (`useLive2DResize.lt`) que reescreve a escala do modelo A CADA QUADRO e nunca para:
    // a escala caiu de 1.834 para 0.807 e o zoom do Pet ficava travado.
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!interactionArmed) return;
    // Um evento de roda tem UM dono. O canvas Live2D compilado tem o próprio
    // escalador de roda, com estado de escala independente: nunca deixar um
    // evento dirigir os dois controladores.
    //
    // REGRESSÃO CORRIGIDA em 2026-10-01: o commit 1ddc6d9 fez a roda chamar
    // `resize_by()` fora do modo fixo, redimensionando a BrowserWindow. Como o
    // Live2D acompanha o tamanho do canvas, redimensionar a janela PARECE zoom
    // — foi a queixa "tento mover a Célia e ela dá zoom". Fora do modo fixo a
    // roda volta a não ter dono aqui.
    if (!fullscreenMode) return;
    const body = window.celiaLive2DBody;
    if (!body || typeof body.scaleBy !== "function") return;
    body.scaleBy(event.deltaY < 0 ? 0.12 : -0.12);
  }, { capture: true, passive: false });

  document.addEventListener("dblclick", async (event) => {
    if (!interactionArmed) return;
    const native = api();
    if (!native || typeof native.toggle_position_lock !== "function") return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const state = await native.toggle_position_lock();
    updateLock(state?.locked);
  }, true);
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.key.toLowerCase() === "s") {
      event.preventDefault();
      const body = window.celiaLive2DBody;
      if (body && typeof body.save === "function") body.save();
    }
  }, true);
  document.addEventListener("keyup", (event) => {
    if (event.code === "Space" || event.key === "Control") returnToGhostMode();
  }, true);
  window.addEventListener("blur", returnToGhostMode);

  window.celiaPetVisualDebug = {
    getState: () => ({ fullscreenMode, activeDisplayId, displayLayout }),
    normalizeSurface: normalizeLive2DSurface,
    preserveFloorAt: preserveFloorAcrossDisplays,
  };

  function start() {
    prepareSurface();
    watchLive2DSurface();
    // Body persistence owns Live2D pose lifecycle. Do not rescale/remount here.
    // The transparent Pet is ghosted until Electron receives Ctrl+Space.
    const native = api();
    if (native && typeof native.set_mouse_ignored === "function") {
      native.set_mouse_ignored(true);
      if (typeof native.on_interaction_armed === "function") {
        native.on_interaction_armed(armInteraction);
      }
      if (typeof native.on_interaction_disarmed === "function") {
        native.on_interaction_disarmed(disarmInteraction);
      }
    }
    window.addEventListener("pywebviewready", loadState);
    if (api()) loadState();
  }

  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start, { once: true });
}());
