/* Local browser compatibility for requesting the existing desktop Pet. */
(function installBrowserPetMode() {
  "use strict";

  if (window.api || window.celiaPetNative) return;

  // Medido em 2026-08-24: o Pet do Electron sobrevive ao recarregamento da
  // aba (e `bin/ligar.sh` ainda o restaura no boot), mas esta camada esquecia
  // que já tinha pedido o Pet. Na volta da página o avatar embutido reaparecia
  // e o Pai via DUAS Célias na tela. A intenção agora fica persistida.
  const PET_REQUESTED_KEY = "celia-desktop-pet:requested";
  const listeners = new Map();
  let mode = "window";
  let desktopPetLaunch = "idle";
  let hideEmbeddedScheduled = false;
  let embeddedObserver = null;
  let embeddedRenderGuard = null;
  let pausedManager = null;
  let pausedManagerOnUpdate = null;

  function rememberPetRequested(requested) {
    try {
      if (requested) localStorage.setItem(PET_REQUESTED_KEY, "1");
      else localStorage.removeItem(PET_REQUESTED_KEY);
    } catch (_) {
      // Armazenamento bloqueado nao pode derrubar o modo Pet.
    }
  }

  function petWasRequested() {
    try {
      return localStorage.getItem(PET_REQUESTED_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  function setEmbeddedLive2DRenderingPaused(paused) {
    if (!paused) {
      if (pausedManager && pausedManagerOnUpdate) pausedManager.onUpdate = pausedManagerOnUpdate;
      pausedManager = null;
      pausedManagerOnUpdate = null;
      delete document.documentElement.dataset.celiaEmbeddedLive2dPaused;
      return true;
    }

    const getManager = window.getLive2DManager;
    if (typeof getManager !== "function") return false;
    const manager = getManager();
    if (!manager) return false;
    if (pausedManager === manager && pausedManagerOnUpdate) return true;

    if (pausedManager && pausedManagerOnUpdate) pausedManager.onUpdate = pausedManagerOnUpdate;
    pausedManager = manager;
    pausedManagerOnUpdate = manager.onUpdate;
    manager.onUpdate = function celiaDetachedLive2DNoop() {};
    document.documentElement.dataset.celiaEmbeddedLive2dPaused = "true";
    return true;
  }

  function startEmbeddedRenderGuard() {
    if (embeddedRenderGuard) return;
    embeddedRenderGuard = setInterval(() => {
      if (desktopPetLaunch === "requested") setEmbeddedLive2DRenderingPaused(true);
    }, 1000);
  }

  function stopEmbeddedRenderGuard() {
    if (embeddedRenderGuard) clearInterval(embeddedRenderGuard);
    embeddedRenderGuard = null;
    setEmbeddedLive2DRenderingPaused(false);
  }

  function setEmbeddedLive2DVisible(visible) {
    setEmbeddedLive2DRenderingPaused(!visible);
    const live2d = document.getElementById("live2d-internal-wrapper");
    if (!live2d) return false;
    if (visible) {
      live2d.style.removeProperty("display");
      delete live2d.dataset.celiaDesktopPetDetached;
    } else {
      live2d.style.setProperty("display", "none", "important");
      live2d.dataset.celiaDesktopPetDetached = "true";
    }
    window.dispatchEvent(new Event("resize"));
    return true;
  }

  function enforceEmbeddedLive2DHidden() {
    hideEmbeddedScheduled = false;
    if (desktopPetLaunch === "requested") setEmbeddedLive2DVisible(false);
  }

  function scheduleEmbeddedLive2DHide() {
    if (hideEmbeddedScheduled) return;
    hideEmbeddedScheduled = true;
    requestAnimationFrame(enforceEmbeddedLive2DHidden);
  }

  function observeEmbeddedLive2D() {
    if (embeddedObserver) return;
    embeddedObserver = new MutationObserver(scheduleEmbeddedLive2DHide);
    embeddedObserver.observe(document.documentElement, { childList: true, subtree: true });
  }

  // 2026-10-07 (R17, ordem do Pai): Pet e interface sao EXCLUSIVOS — com o Pet vivo a interface nao renderiza corpo nenhum; com a interface
  // escolhida ("Live Mode") o Pet se fecha. Antes esta camada so sabia do Pet que ELA lancou: um Pet restaurado pelo bin/ligar.sh (ou aberto a
  // mao) aparecia ao mesmo tempo que o corpo da interface. Agora a verdade vem do servidor:
  //   GET  /bridge/desktop-pet/state -> {alive: o processo do Electron existe agora (/proc), enabled: intencao de restauracao}
  //   POST /bridge/mode {modo:"pet"|"janela"} -> o Pet (celia-escolha.js ?pet=1) le /bridge/mode e fecha a si mesmo quando "janela" e mais novo
  //   que a sua propria carga. Servidor antigo (sem `alive`) cai no comportamento do R15 (so `enabled`, so depois de um pedido desta pagina).
  let petWatch = null;
  let petSeenOn = false;
  let petWatchSince = 0;
  let petClosing = false;
  let petClosingSince = 0;
  let lastAlive = null;

  async function postModo(modo) {
    try {
      await fetch("/bridge/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ modo }),
        credentials: "same-origin",
      });
    } catch (_) {
      // servidor sem a rota: o Pet continua sem ser fechado pela interface.
    }
  }

  function adoptRunningPet() {
    // Pet vivo que esta pagina nao lancou (restaurado no boot, aberto a mao ou por outra aba).
    desktopPetLaunch = "requested";
    mode = "pet";
    petSeenOn = true;
    petWatchSince = Date.now();
    rememberPetRequested(true);
    setEmbeddedLive2DVisible(false);
    observeEmbeddedLive2D();
    startEmbeddedRenderGuard();
    window.dispatchEvent(new CustomEvent("celia-desktop-pet-launch", { detail: { status: desktopPetLaunch, adopted: true } }));
    window.dispatchEvent(new CustomEvent("celia-browser-pet-mode", { detail: { mode } }));
  }

  async function readPetState() {
    try {
      const response = await fetch("/bridge/desktop-pet/state", { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async function checkPetState() {
    const state = await readPetState();
    if (!state) return;
    if (typeof state.alive === "boolean") {
      lastAlive = state.alive;
      if (state.alive) {
        petSeenOn = true;
        if (petClosing) {
          // pedi para o Pet se fechar: espera ele sumir (10 s no maximo) em vez de readota-lo
          if (Date.now() - petClosingSince < 10000) return;
          petClosing = false;
        }
        if (desktopPetLaunch !== "requested" && desktopPetLaunch !== "pending") adoptRunningPet();
        return;
      }
      petClosing = false;
      if (desktopPetLaunch === "requested" && (petSeenOn || Date.now() - petWatchSince > 20000)) {
        await setMode("window", { petAlreadyClosed: true });
        window.dispatchEvent(new CustomEvent("celia-desktop-pet-launch", { detail: { status: "closed" } }));
      }
      return;
    }
    // servidor antigo: so `enabled`, e so depois de um pedido desta pagina (comportamento do R15)
    if (desktopPetLaunch !== "requested" || typeof state.enabled !== "boolean") return;
    if (state.enabled) { petSeenOn = true; return; }
    if (petSeenOn || Date.now() - petWatchSince > 20000) {
      await setMode("window", { petAlreadyClosed: true });
      window.dispatchEvent(new CustomEvent("celia-desktop-pet-launch", { detail: { status: "closed" } }));
    }
  }

  function startPetWatch() {
    if (petWatch) return;
    petWatch = setInterval(checkPetState, 2000);
    checkPetState();
  }

  async function requestDesktopPet() {
    desktopPetLaunch = "pending";
    petWatchSince = Date.now();
    petSeenOn = false; // um Pet ja visto antes NAO vale para este pedido (senao "ainda nao subiu" lia como "ja fechou")
    petClosing = false;
    window.dispatchEvent(new CustomEvent("celia-desktop-pet-launch", { detail: { status: desktopPetLaunch } }));
    await postModo("pet");
    try {
      const response = await fetch("/bridge/desktop-pet/open", {
        method: "POST",
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(`desktop_pet_launch_${response.status}`);
      const result = await response.json();
      if (!result || result.ok !== true) throw new Error("desktop_pet_launch_rejected");
      desktopPetLaunch = "requested";
      rememberPetRequested(true);
      // This is the selected operating mode of the primary interface. It does
      // not switch the Electron renderer into the compiled legacy Pet layout.
      mode = "pet";
      // Keep the chat UI intact; only the duplicate embedded avatar leaves it.
      // Its Live2D update/draw loop is paused too; CSS display:none alone still
      // burns physics/render CPU in the detached browser renderer.
      setEmbeddedLive2DVisible(false);
      observeEmbeddedLive2D();
      startEmbeddedRenderGuard();
      window.dispatchEvent(new CustomEvent("celia-desktop-pet-launch", { detail: { status: desktopPetLaunch } }));
      return { ok: true, mode, desktopPet: true };
    } catch (_) {
      desktopPetLaunch = "failed";
      window.dispatchEvent(new CustomEvent("celia-desktop-pet-launch", { detail: { status: desktopPetLaunch } }));
      return { ok: false, reason: "desktop_pet_launch_failed" };
    }
  }

  function emit(channel, value) {
    for (const listener of listeners.get(channel) || []) listener(undefined, value);
  }

  function addListener(channel, listener) {
    if (typeof listener !== "function") return;
    const channelListeners = listeners.get(channel) || new Set();
    channelListeners.add(listener);
    listeners.set(channel, channelListeners);
  }

  function removeListener(channel, listener) {
    const channelListeners = listeners.get(channel);
    if (channelListeners) channelListeners.delete(listener);
  }

  async function setMode(nextMode, options) {
    if (nextMode !== "window" && nextMode !== "pet") return { ok: false, reason: "invalid_mode" };
    if (nextMode === "pet") return requestDesktopPet();
    const petWasOn = desktopPetLaunch === "requested" || desktopPetLaunch === "pending";
    rememberPetRequested(false);
    desktopPetLaunch = "idle";
    // "Live Mode" escolhido: o Pet precisa fechar (exclusivos). So avisa quando o Pet ainda existe (nao quando ele ja fechou sozinho).
    if (petWasOn && !(options && options.petAlreadyClosed)) {
      petClosing = true;
      petClosingSince = Date.now();
      postModo("janela");
    }
    stopEmbeddedRenderGuard();
    setEmbeddedLive2DVisible(true);
    embeddedObserver?.disconnect();
    embeddedObserver = null;
    if (nextMode === mode) return { ok: true, mode };
    emit("pre-mode-changed", nextMode);
    mode = nextMode;
    window.dispatchEvent(new CustomEvent("celia-browser-pet-mode", { detail: { mode } }));
    requestAnimationFrame(() => emit("mode-changed", mode));
    return { ok: true, mode };
  }

  // A casca compilada decide a captura de tela por `if (window.electron)`:
  // no caminho Electron ela pede um `chromeMediaSourceId` pelo canal
  // `get-screen-capture` e monta uma constraint `chromeMediaSource: "desktop"`
  // para `getUserMedia`. Como esta camada instala `window.electron` para o Pet
  // Mode, o bundle tomava o caminho errado e o `invoke` estourava
  // "Browser Pet Mode has no desktop IPC" (relatado pelo Pai em 2026-08-24).
  // No navegador quem escolhe a janela e o proprio Chrome, por getDisplayMedia.
  const DESKTOP_CAPTURE_CHANNEL = "get-screen-capture";

  function isDesktopCaptureRequest(constraints) {
    const mandatory = constraints && constraints.video && constraints.video.mandatory;
    return Boolean(mandatory && mandatory.chromeMediaSource === "desktop");
  }

  function installDisplayMediaBridge() {
    const media = navigator.mediaDevices;
    if (!media || typeof media.getUserMedia !== "function") return false;
    if (media.celiaDisplayBridge) return true;
    const original = media.getUserMedia.bind(media);
    // Somente a constraint de desktop e desviada; camera e microfone seguem
    // pelo caminho original, sem passar por aqui.
    media.getUserMedia = function celiaGetUserMedia(constraints) {
      if (!isDesktopCaptureRequest(constraints)) return original(constraints);
      if (typeof media.getDisplayMedia !== "function") {
        return Promise.reject(new Error("Este navegador nao expoe getDisplayMedia"));
      }
      // O controle de audio da fonte (celia-audio-fontes.js) grava a vontade do
      // Pai aqui. Padrao continua audio:false, que e o caminho ja provado; so
      // pede audio quando ele liga o controle antes de comecar a captura.
      let querAudio = false;
      try { querAudio = localStorage.getItem("celia-fonte-audio:screen") === "1"; } catch (_) { }
      return media.getDisplayMedia({ video: true, audio: querAudio });
    };
    media.celiaDisplayBridge = true;
    return true;
  }

  installDisplayMediaBridge();

  window.api = {
    setMode,
    onToggleInputSubtitle: () => () => {},
    updateComponentHover: () => {},
    toggleForceIgnoreMouse: () => {},
  };
  window.electron = {
    process: { platform: "browser" },
    ipcRenderer: {
      on: addListener,
      removeListener,
      removeAllListeners: (channel) => listeners.delete(channel),
      send: () => {},
      invoke: async (channel) => {
        // Id vazio de proposito: a ponte acima ignora o id e entrega a escolha
        // da tela ao seletor nativo do navegador. Qualquer outro canal continua
        // falhando alto, para nao inventar uma capacidade que nao existe.
        if (channel === DESKTOP_CAPTURE_CHANNEL) return "";
        throw new Error("Browser Pet Mode has no desktop IPC");
      },
    },
  };
  async function restorePetRequest() {
    // R17: o servidor diz se o Pet existe. Vivo => adota (interface sem corpo); morto => esquece o pedido antigo (nao ressuscita o que o
    // Pai fechou). Sem `alive` (servidor antigo) => comportamento anterior, abaixo.
    const state = await readPetState();
    if (state && typeof state.alive === "boolean") {
      lastAlive = state.alive;
      if (state.alive) { adoptRunningPet(); return; }
      rememberPetRequested(false);
      return;
    }
    if (!petWasRequested()) return;
    // A intenção persistida não prova que o processo Electron ainda existe.
    // Reconfirma pelo launcher idempotente: se a janela já estiver viva, o
    // single-instance a traz para frente; se morreu, ela nasce de novo.
    const restored = await requestDesktopPet();
    if (!restored.ok) {
      rememberPetRequested(false);
      desktopPetLaunch = "failed";
      mode = "window";
      setEmbeddedLive2DVisible(true);
      return;
    }
    window.dispatchEvent(new CustomEvent("celia-desktop-pet-launch", {
      detail: { status: desktopPetLaunch, restored: true },
    }));
    window.dispatchEvent(new CustomEvent("celia-browser-pet-mode", { detail: { mode } }));
  }

  // 28/10/2026 (ordem do Pai): modo Live ligado => a cena da bancada (12593) é o único corpo e a única voz.
  // A interface comum para de renderizar o corpo (2D escondido e pausado; o 3D lê liveFocus) e o servidor
  // não lhe manda áudio. A própria cena (celiaScene=1) e as saídas (liveOutput=1) nunca entram aqui.
  let liveFocus = false;
  const pageQuery = new URLSearchParams(window.location.search);
  const isOutputPage = pageQuery.get("celiaScene") === "1" || pageQuery.get("liveOutput") === "1";
  function embeddedHidden() {
    const live2d = document.getElementById("live2d-internal-wrapper");
    return !live2d || live2d.style.getPropertyValue("display") === "none";
  }
  async function pollLiveFocus() {
    let on;
    try {
      const response = await fetch("/bridge/live/status", { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) return;
      const status = await response.json();
      on = Boolean(status && status.mode && status.mode !== "off");
    } catch (_) {
      return;
    }
    if (on === liveFocus) {
      if (on && !embeddedHidden()) setEmbeddedLive2DVisible(false);   // o bundle remontou o wrapper
      return;
    }
    liveFocus = on;
    document.documentElement.toggleAttribute("data-celia-live-foco", on);
    if (on) setEmbeddedLive2DVisible(false);
    else if (desktopPetLaunch !== "requested") setEmbeddedLive2DVisible(true);
    window.dispatchEvent(new CustomEvent("celia-live-foco", { detail: { on } }));
  }
  if (!isOutputPage) {
    setInterval(pollLiveFocus, 2000);
    pollLiveFocus();
  }

  window.celiaBrowserPet = {
    setMode,
    state: () => ({
      liveFocus,
      mode,
      desktopPetLaunch,
      petRequested: petWasRequested(),
      petAlive: lastAlive,
      embeddedLive2DPaused: Boolean(pausedManagerOnUpdate),
    }),
    displayMediaBridged: () => Boolean(navigator.mediaDevices && navigator.mediaDevices.celiaDisplayBridge),
    forgetPetRequest: () => {
      rememberPetRequested(false);
      return setMode("window");
    },
  };

  startPetWatch();
  if (document.body) restorePetRequest();
  else document.addEventListener("DOMContentLoaded", restorePetRequest, { once: true });
}());
