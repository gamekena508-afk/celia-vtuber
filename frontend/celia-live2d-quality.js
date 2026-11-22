/* Retain the browser's native pixel density without upscaling a slow renderer. */
(function installLive2DQualityGuard() {
  "use strict";

  let scheduled = false;
  let observedCanvas = null;
  let resizeObserver = null;
  let canvasObserver = null;

  function repairCanvasResolution() {
    scheduled = false;
    const canvas = document.getElementById("canvas");
    if (!canvas || !canvas.clientWidth || !canvas.clientHeight) return;
    if (canvas !== observedCanvas) {
      resizeObserver?.disconnect();
      observedCanvas = canvas;
      resizeObserver = new ResizeObserver(scheduleRepair);
      resizeObserver.observe(canvas);
      // Once the actual canvas exists, resizing it is the only event we need.
      canvasObserver?.disconnect();
    }
    // Densidade NATIVA e o devicePixelRatio real, inclusive abaixo de 1 (escala
    // fracionaria do KDE, ex. 0.9). O antigo Math.max(1, dpr) pedia 1600x1000 num
    // painel de 1440x900: o bundle Live2D devolvia o canvas para 1439x899, este
    // script empurrava para 1600x1000 de novo, e cada atribuicao de width/height
    // apaga o buffer WebGL. Medido 2026-10-05: 670 escritas em 30 s e quadros
    // vazios (a Celia "sumindo"). A folga de 2 px absorve o arredondamento do
    // bundle (floor) contra o deste script (round); sem ela a briga continua.
    const scale = window.devicePixelRatio > 0 ? window.devicePixelRatio : 1;
    const width = Math.round(canvas.clientWidth * scale);
    const height = Math.round(canvas.clientHeight * scale);
    const SLACK = 2;
    if (canvas.width + SLACK < width || canvas.height + SLACK < height) {
      canvas.width = width;
      canvas.height = height;
      const delegate = window.LAppDelegate && window.LAppDelegate.getInstance();
      if (delegate) delegate.onResize();
    }
    canvas.dataset.celiaNativePixels = String(canvas.width >= width && canvas.height >= height);
  }

  function scheduleRepair() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(repairCanvasResolution);
  }

  canvasObserver = new MutationObserver(scheduleRepair);
  canvasObserver.observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
  window.addEventListener("resize", scheduleRepair, { passive: true });
  scheduleRepair();
}());
