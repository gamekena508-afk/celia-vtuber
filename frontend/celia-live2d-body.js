/* Live FPS and persisted body controls for the browser and native Pet surfaces. */
(function installLive2DBodyControls() {
  "use strict";

  const STORAGE_PREFIX = "celia-live2d-body:";
  const CONTROLS_ID = "celia-body-controls";
  const HUD_GAP = 8;
  const MIN_SCALE = 0.1;
  const MAX_SCALE = 5;
  const POSITION_LIMIT = 8;
  const SCALE_STEP = 0.12;
  const MOVE_STEP = 0.08;
  const PET_DEFAULT_SCALE = 2.25;
  const AUTO_SAVE_MS = 180;
  const initializedMatrices = new WeakSet();
  let matrixWatchActive = false;
  let autoSaveTimer = null;
  let activeScope = null;
  let restoredScope = null;
  let latestFps = 0;

  function isNativePet() {
    return new URLSearchParams(location.search).get("pet") === "1";
  }

  function scope() {
    // Requesting/detaching the external Electron Pet does not turn this
    // browser renderer into the Pet renderer. Only ?pet=1 owns Pet pose data.
    return isNativePet() ? "pet" : "interface";
  }

  function storageKey() {
    return STORAGE_PREFIX + scope();
  }

  function modelMatrix() {
    const adapter = window.getLAppAdapter && window.getLAppAdapter();
    const model = adapter && adapter.getModel && adapter.getModel();
    const matrix = model && model._modelMatrix;
    return matrix && typeof matrix.scale === "function" ? matrix : null;
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function readPoseFrom(matrix) {
    if (!matrix) return null;
    const values = matrix.getArray();
    return {
      scale: matrix.getScaleX(),
      x: values[12],
      y: values[13],
    };
  }

  function readPose() {
    return readPoseFrom(modelMatrix());
  }

  // Caixa do corpo no espaco local do modelo, lida dos vertices reais.
  function localBounds() {
    const adapter = window.getLAppAdapter && window.getLAppAdapter();
    const user = adapter && adapter.getModel && adapter.getModel();
    const cubism = user && user.getModel && user.getModel();
    if (!cubism || typeof cubism.getDrawableCount !== "function") return null;
    // So o que aparece na tela. O modelo tem ~1232 drawables e so ~442 visiveis;
    // os ocultos (poses alternativas, acessorios) se espalham com as animacoes.
    // Medido 2026-10-05: contando todos, a "largura do corpo" oscilou de 0.745 a
    // 1.133 e passou disso; o enquadramento entao cortava a escala e a travava
    // (zoom so diminuia, escala presa em 0.754).
    const visivel = (i) => {
      if (typeof cubism.getDrawableDynamicFlagIsVisible === "function" && !cubism.getDrawableDynamicFlagIsVisible(i)) return false;
      return typeof cubism.getDrawableOpacity !== "function" || cubism.getDrawableOpacity(i) > 0.01;
    };
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let i = 0; i < cubism.getDrawableCount(); i++) {
      if (!visivel(i)) continue;
      const v = cubism.getDrawableVertices(i);
      for (let k = 0; k + 1 < v.length; k += 2) {
        if (v[k] < x0) x0 = v[k];
        if (v[k] > x1) x1 = v[k];
        if (v[k + 1] < y0) y0 = v[k + 1];
        if (v[k + 1] > y1) y1 = v[k + 1];
      }
    }
    return Number.isFinite(x0) && x1 > x0 && y1 > y0 ? { x0, y0, x1, y1 } : null;
  }

  // Pet em tela cheia e adaptativo: a pose salva e guardada em unidades do
  // modelo, que nao acompanham o tamanho da janela. Numa superficie menor a mesma
  // pose jogava o corpo para fora (medido 2026-10-05: borda direita em x~1708 num
  // painel de 1440). Aqui a caixa real do corpo e mantida dentro da tela.
  // Espaco de visao do Cubism, medido contra a captura: x vai de -W/H a +W/H e
  // y de -1 a +1. Embaixo fica livre porque o enquadramento do Pet e busto.
  const FIT_MARGIN = 0.03;
  const FIT_MIN_HEAD = 0.35;
  function fitPoseToView(pose) {
    const canvas = document.getElementById("canvas");
    const rect = canvas && canvas.getBoundingClientRect();
    const box = localBounds();
    if (!rect || rect.width <= 0 || rect.height <= 0 || !box) return pose;
    const half = rect.width / rect.height;
    let { scale, x, y } = pose;
    const room = 2 * (half - FIT_MARGIN);
    if (scale * (box.x1 - box.x0) > room) scale = room / (box.x1 - box.x0);
    const left = scale * box.x0 + x;
    const right = scale * box.x1 + x;
    if (right > half - FIT_MARGIN) x -= right - (half - FIT_MARGIN);
    else if (left < -half + FIT_MARGIN) x += (-half + FIT_MARGIN) - left;
    const top = scale * box.y1 + y;
    if (top > 1 - FIT_MARGIN) y -= top - (1 - FIT_MARGIN);
    else if (top < -1 + FIT_MIN_HEAD) y += (-1 + FIT_MIN_HEAD) - top;
    return { scale, x, y };
  }

  function applyPoseTo(matrix, rawPose) {
    if (!matrix || !rawPose) return false;
    let next = {
      scale: clamp(Number(rawPose.scale), MIN_SCALE, MAX_SCALE),
      x: clamp(Number(rawPose.x), -POSITION_LIMIT, POSITION_LIMIT),
      y: clamp(Number(rawPose.y), -POSITION_LIMIT, POSITION_LIMIT),
    };
    if (!Number.isFinite(next.scale) || !Number.isFinite(next.x) || !Number.isFinite(next.y)) return false;
    if (isNativePet()) next = fitPoseToView(next);
    // CubismModelMatrix scale/translateX/translateY are ABSOLUTE setters.
    // Feeding ratios/deltas here caused the Pet to jump/flash on every zoom/drag.
    matrix.scale(next.scale, next.scale);
    matrix.translateX(next.x);
    matrix.translateY(next.y);
    if (scope() === "pet") {
      document.documentElement.dataset.celiaPetModelScale = String(next.scale);
    }
    return true;
  }

  function applyPose(rawPose) {
    return applyPoseTo(modelMatrix(), rawPose);
  }

  function savedPose() {
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey()) || "null");
      return parsed && parsed.version === 1 ? parsed : null;
    } catch (_) {
      return null;
    }
  }

  function restore() {
    const matrix = modelMatrix();
    const pose = savedPose();
    if (!matrix || !pose) return false;
    initializedMatrices.add(matrix);
    return applyPoseTo(matrix, pose);
  }

  function save() {
    const pose = readPose();
    if (!pose) return false;
    localStorage.setItem(storageKey(), JSON.stringify({ version: 1, ...pose }));
    const status = document.getElementById("celia-body-save");
    if (status) {
      status.textContent = "Salvo";
      setTimeout(() => { status.textContent = "Salvar"; }, 900);
    }
    return true;
  }

  function scheduleSave() {
    clearTimeout(autoSaveTimer);
    autoSaveTimer = setTimeout(() => { autoSaveTimer = null; save(); }, AUTO_SAVE_MS);
  }

  function scaleBy(delta) {
    const pose = readPose();
    const changed = pose ? applyPose({ ...pose, scale: pose.scale + delta }) : false;
    if (changed && scope() === "pet") scheduleSave();
    return changed;
  }

  function moveBy(dx, dy) {
    const pose = readPose();
    const changed = pose ? applyPose({ ...pose, x: pose.x + dx, y: pose.y + dy }) : false;
    if (changed && scope() === "pet") scheduleSave();
    return changed;
  }

  function initializeMatrix(matrix) {
    if (!matrix || initializedMatrices.has(matrix)) return false;
    const pose = savedPose();
    const initial = pose || (isNativePet() ? { ...readPoseFrom(matrix), scale: PET_DEFAULT_SCALE } : null);
    if (initial) applyPoseTo(matrix, initial);
    initializedMatrices.add(matrix);
    return true;
  }

  function restoreWhenModelReady() {
    if (matrixWatchActive) return;
    matrixWatchActive = true;
    let attempts = 0;
    const attempt = () => {
      const matrix = modelMatrix();
      if (matrix) { initializeMatrix(matrix); matrixWatchActive = false; return; }
      if (++attempts >= 120) { matrixWatchActive = false; return; }
      requestAnimationFrame(attempt);
    };
    attempt();
  }

  function addButton(parent, label, title, handler) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.title = title;
    Object.assign(button.style, {
      minWidth: "28px",
      height: "26px",
      border: "1px solid rgba(255,255,255,.22)",
      borderRadius: "7px",
      color: "#fff",
      background: "rgba(7,12,19,.84)",
      cursor: "pointer",
      font: "700 13px/1 system-ui, sans-serif",
    });
    button.addEventListener("click", handler);
    parent.appendChild(button);
  }

  // A casca compilada desenha a propria barra de titulo (faixa arrastavel de
  // 30px com Minimize/Maximize/Close entre x=1314 e x=1430, medido em
  // 2026-08-24). Ancorar o FPS em top:10px punha o numero exatamente por cima
  // do botao Fechar. O HUD desce abaixo da faixa, seja qual for a altura dela.
  function titleBarHeight() {
    let tallest = 0;
    for (const element of document.querySelectorAll("body div")) {
      const rect = element.getBoundingClientRect();
      if (rect.top > 2 || rect.height <= 0 || rect.height > 72) continue;
      if (rect.width < window.innerWidth * 0.7) continue;
      if (getComputedStyle(element).webkitAppRegion !== "drag") continue;
      tallest = Math.max(tallest, rect.height);
    }
    return tallest;
  }

  function positionHud() {
    const hud = document.getElementById("celia-body-hud");
    if (!hud) return 0;
    const top = Math.round(titleBarHeight()) + HUD_GAP;
    if (hud.style.top !== top + "px") hud.style.top = top + "px";
    return top;
  }

  function toggleControls() {
    const controls = document.getElementById(CONTROLS_ID);
    if (!controls) return false;
    const open = controls.style.display !== "none";
    controls.style.display = open ? "none" : "flex";
    return !open;
  }

  function installHud() {
    if (document.getElementById("celia-body-hud") || !document.body) return;
    // Ordem do Pai (2026-08-24): no canto direito do topo fica SÓ o FPS, branco
    // com contorno preto. As setas de posição e o botão Salvar continuam
    // existindo, mas escondidos atrás de um clique no próprio número — a
    // capacidade de reposicionar o corpo não foi removida, só saiu da vista.
    const hud = document.createElement("div");
    hud.id = "celia-body-hud";
    Object.assign(hud.style, {
      position: "fixed",
      right: "12px",
      top: HUD_GAP + "px",
      zIndex: "2147483003",
      display: "flex",
      flexDirection: "column",
      alignItems: "flex-end",
      gap: "6px",
      background: "none",
      border: "0",
      padding: "0",
      pointerEvents: "none",
      userSelect: "none",
    });

    const fps = document.createElement("span");
    fps.id = "celia-live-fps";
    fps.textContent = "FPS --";
    fps.title = "Clique para mostrar ou esconder os controles de corpo";
    fps.setAttribute("role", "button");
    fps.tabIndex = 0;
    Object.assign(fps.style, {
      color: "#fff",
      font: "800 15px/1 system-ui, sans-serif",
      letterSpacing: ".02em",
      // paint-order deixa o traço atrás do preenchimento; sem ele o contorno
      // come metade da espessura da letra e o número fica fino demais.
      webkitTextStroke: "3px #000",
      paintOrder: "stroke fill",
      textShadow: "0 1px 2px rgba(0,0,0,.85)",
      cursor: "pointer",
      pointerEvents: "auto",
    });
    fps.addEventListener("click", toggleControls);
    fps.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleControls();
      }
    });
    hud.appendChild(fps);

    const controls = document.createElement("div");
    controls.id = CONTROLS_ID;
    Object.assign(controls.style, {
      display: "none",
      alignItems: "center",
      gap: "6px",
      padding: "7px",
      border: "1px solid rgba(255,255,255,.18)",
      borderRadius: "10px",
      background: "rgba(4,8,14,.78)",
      backdropFilter: "blur(8px)",
      color: "#fff",
      font: "600 12px/1 system-ui, sans-serif",
      pointerEvents: "auto",
    });
    addButton(controls, "-", "Diminuir corpo", () => scaleBy(-SCALE_STEP));
    addButton(controls, "+", "Aumentar corpo", () => scaleBy(SCALE_STEP));
    addButton(controls, "<", "Mover corpo para a esquerda", () => moveBy(-MOVE_STEP, 0));
    addButton(controls, ">", "Mover corpo para a direita", () => moveBy(MOVE_STEP, 0));
    addButton(controls, "^", "Mover corpo para cima", () => moveBy(0, MOVE_STEP));
    addButton(controls, "v", "Mover corpo para baixo", () => moveBy(0, -MOVE_STEP));
    const saveButton = document.createElement("button");
    saveButton.id = "celia-body-save";
    saveButton.type = "button";
    saveButton.textContent = "Salvar";
    saveButton.title = "Salvar tamanho e posicao do corpo neste modo";
    Object.assign(saveButton.style, {
      height: "26px",
      border: "0",
      borderRadius: "7px",
      padding: "0 9px",
      color: "#06131a",
      background: "#8ee6ff",
      cursor: "pointer",
      font: "800 11px/1 system-ui, sans-serif",
    });
    saveButton.addEventListener("click", save);
    controls.appendChild(saveButton);
    hud.appendChild(controls);

    document.body.appendChild(hud);
    // A barra de titulo e montada pelo React depois deste script; remede
    // algumas vezes ate a casca assentar, e de novo a cada redimensionamento.
    positionHud();
    for (const delay of [400, 1200, 3000]) window.setTimeout(positionHud, delay);
    // 2026-10-07: com a tela de escolha 2D|3D a casca (e a barra de titulo) so monta DEPOIS do clique, normalmente mais de 3 s depois do
    // script; as 3 remedidas acima ja tinham passado e o FPS ficava em cima do botao Fechar (medido: top=10 em vez de 38). Remede por intervalo.
    window.setInterval(positionHud, 1000);
    window.addEventListener("resize", positionHud);
  }

  function removeHud() {
    for (const element of document.querySelectorAll("#celia-body-hud")) element.remove();
  }

  function trackFps() {
    let frames = 0;
    let startedAt = performance.now();
    function frame(now) {
      frames += 1;
      if (now - startedAt >= 500) {
        latestFps = Math.round(frames * 1000 / (now - startedAt));
        const label = document.getElementById("celia-live-fps");
        if (label) label.textContent = "FPS " + latestFps;
        frames = 0;
        startedAt = now;
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  function refreshScope() {
    const nextScope = scope();
    if (nextScope === activeScope) return;
    activeScope = nextScope;
    restoredScope = null;
    setTimeout(() => {
      if (activeScope === nextScope && restoredScope !== nextScope && restore()) restoredScope = nextScope;
    }, 900);
  }

  window.celiaLive2DBody = {
    getFps: () => latestFps,
    getPose: readPose,
    fitPoseToView,
    localBounds,
    moveBy,
    restore,
    save,
    positionHud,
    scaleBy,
    storageKey,
    toggleControls,
  };

  function start() {
    // The native Pet is intentionally just Live2D. Its window gestures remain
    // in celia-pet.js, while visual diagnostics/pose controls stay in the UI.
    if (isNativePet()) {
      removeHud();
      restoreWhenModelReady();
      // Tela mudou de tamanho/escala: reenquadra a pose atual sem gravar, para
      // nao sobrescrever o que o Pai salvou com Ctrl+S.
      window.addEventListener("resize", () => { const pose = readPose(); if (pose) applyPose(pose); });
      // Live2D can replace _modelMatrix while the Pet boots/remounts. Every new
      // matrix receives the persisted Pet pose instead of the old forced bust.
      new MutationObserver(() => {
        removeHud();
        restoreWhenModelReady();
      }).observe(document.documentElement, { childList: true, subtree: true });
      return;
    }
    installHud();
    trackFps();
    refreshScope();
    // Browser Pet announces its only dynamic scope transition. Native Pet has
    // a fixed query parameter, so polling the model matrix is unnecessary.
    window.addEventListener("celia-browser-pet-mode", refreshScope);
  }

  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start, { once: true });
}());
