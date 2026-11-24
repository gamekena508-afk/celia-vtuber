/* Read-only runtime facts for the compiled settings shell. */
(function installRuntimeSettings() {
  "use strict";

  const PANEL_ID = "celia-runtime-settings";
  const RUNTIME_ENDPOINT = "/debug/observability/runtime";

  function valueAt(source, path, fallback = "Não informado") {
    let value = source;
    for (const key of path) {
      if (!value || typeof value !== "object" || !(key in value)) return fallback;
      value = value[key];
    }
    if (value === null || value === undefined || value === "") return fallback;
    if (typeof value === "boolean") return value ? "sim" : "não";
    return String(value);
  }

  function addRow(container, label, value) {
    const row = document.createElement("div");
    row.style.cssText = "display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr);gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.12);font-size:13px;";
    const name = document.createElement("span");
    name.textContent = label;
    name.style.cssText = "color:rgba(255,255,255,.72);font-weight:600;";
    const current = document.createElement("code");
    current.textContent = value;
    current.style.cssText = "color:#b8ebff;overflow-wrap:anywhere;font:12px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace;";
    row.append(name, current);
    container.append(row);
  }

  function render(panel, snapshot) {
    panel.replaceChildren();
    const heading = document.createElement("h3");
    heading.textContent = "Runtime atual";
    heading.style.cssText = "margin:0 0 6px;font-size:16px;";
    const caption = document.createElement("p");
    caption.textContent = "Leitura do processo local carregado. Estes campos são somente leitura e não exibem segredos.";
    caption.style.cssText = "margin:0 0 12px;color:rgba(255,255,255,.72);font-size:12px;line-height:1.45;";
    const rows = document.createElement("div");
    addRow(rows, "Agente", valueAt(snapshot, ["agent", "name"]));
    addRow(rows, "Modelo de conversa", valueAt(snapshot, ["llm", "model"]));
    addRow(rows, "Histórico recente", valueAt(snapshot, ["agent", "max_history_turns"], "Não informado") + " turnos");
    addRow(rows, "Modelo de ferramentas", valueAt(snapshot, ["tool_agent", "model"]));
    addRow(rows, "Voz carregada", valueAt(snapshot, ["voice", "engine_loaded"]));
    addRow(rows, "Preset de voz", valueAt(snapshot, ["voice", "active_preset", "active"]));
    addRow(rows, "Perfil conversacional", valueAt(snapshot, ["continuous_behavior", "profile"]));
    addRow(rows, "Estado conversacional", valueAt(snapshot, ["continuous_behavior", "state"]));
    addRow(rows, "Guardian fail-open", valueAt(snapshot, ["guardian", "fail_open"]));
    panel.append(heading, caption, rows);
  }

  function renderFailure(panel) {
    panel.replaceChildren();
    const heading = document.createElement("h3");
    heading.textContent = "Runtime indisponível";
    heading.style.cssText = "margin:0 0 6px;font-size:16px;";
    const message = document.createElement("p");
    message.textContent = "Não foi possível ler o processo local agora. A configuração antiga não foi usada como substituta.";
    message.style.cssText = "margin:0;color:rgba(255,255,255,.72);font-size:12px;line-height:1.45;";
    panel.append(heading, message);
  }

  async function refresh(panel) {
    if (panel.dataset.loading === "true") return;
    panel.dataset.loading = "true";
    try {
      const response = await fetch(RUNTIME_ENDPOINT, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`runtime_${response.status}`);
      render(panel, await response.json());
    } catch (_) {
      renderFailure(panel);
    } finally {
      panel.dataset.loading = "false";
    }
  }

  function install(dialog) {
    const tab = dialog.querySelector('[role="tab"][data-value="agent"]');
    const legacyPanel = dialog.querySelector('[role="tabpanel"][id$="content-agent"]');
    if (!tab || !legacyPanel) return;

    const nav = window.celiaSettingsV3;
    if (!nav) return;
    const host = nav.section(legacyPanel,"celia-v3-runtime","Runtime e fluxo ao vivo");
    let panel = legacyPanel.querySelector(`#${PANEL_ID}`);
    if (!panel) {
      panel = document.createElement("section");
      panel.id = PANEL_ID;
      panel.setAttribute("aria-live", "polite");
      panel.style.cssText = "padding:4px 1px 10px;";
      host.append(panel);
    }

    // Fluxo ao vivo (antes uma caixa fixa no canto da tela): secao propria, fora do #celia-runtime-settings (que se reescreve a cada leitura).
    let fluxo = legacyPanel.querySelector("#celia-debug-flow-secao");
    if (!fluxo) {
      fluxo = document.createElement("section");
      fluxo.id = "celia-debug-flow-secao";
      fluxo.style.cssText = "padding:4px 1px 14px;";
      const titulo = document.createElement("h3");
      titulo.textContent = "Fluxo ao vivo";
      titulo.style.cssText = "margin:12px 0 6px;font-size:16px;";
      const legenda = document.createElement("p");
      legenda.textContent = "Fio do turno (voz, cérebro, fila, TTS). Atualiza a cada segundo enquanto as Configurações estão abertas.";
      legenda.style.cssText = "margin:0 0 10px;color:rgba(255,255,255,.72);font-size:12px;line-height:1.45;";
      const caixa = document.createElement("div");
      fluxo.append(titulo, legenda, caixa);
      host.append(fluxo);
      fluxo._caixa = caixa;
    }
    if (window.celiaDebugFlow && typeof window.celiaDebugFlow.montarEm === "function" && fluxo._caixa) window.celiaDebugFlow.montarEm(fluxo._caixa);

    if (!host.dataset.celiaRuntimeOpen) {
      host.dataset.celiaRuntimeOpen = "true";
      host.parentElement.addEventListener("celia-v3-open", () => refresh(panel));
    }
    if (tab.getAttribute("aria-selected") === "true" && panel.dataset.initialized !== "true") {
      panel.dataset.initialized = "true";
      refresh(panel);
    }
    if (!tab.dataset.celiaRuntimeBound) {
      tab.dataset.celiaRuntimeBound = "true";
      tab.addEventListener("click", () => window.setTimeout(() => refresh(panel), 0));
    }
  }

  function scan() {
    for (const dialog of document.querySelectorAll('[role="dialog"]')) install(dialog);
  }

  let scanAgendado = false;
  function agendarScan() {
    if (scanAgendado) return;
    scanAgendado = true;
    requestAnimationFrame(() => { scanAgendado = false; scan(); });
  }
  new MutationObserver(agendarScan).observe(document.documentElement, { childList: true, subtree: true });
  scan();
}());
