(() => {
  "use strict";
  const form = document.querySelector("#auth-form");
  const tokenInput = document.querySelector("#admin-token");
  const clearButton = document.querySelector("#clear-token");
  const refreshButton = document.querySelector("#refresh");
  const statusText = document.querySelector("#connection-status");
  const dot = document.querySelector("#connection-dot");
  const list = document.querySelector("#bots");
  let token = "";
  let refreshTimer = null;
  const setStatus = (text, state = "") => { statusText.textContent = text; dot.className = `dot ${state}`; };
  const labelState = (bot) => bot.process_alive ? (bot.connected ? "Executando · conectado" : "Executando · sem heartbeat") : "Parado";
  function makeCard(bot) {
    const article = document.createElement("article"); article.className = "bot-card";
    const head = document.createElement("header");
    const identity = document.createElement("div");
    const name = document.createElement("h2"); name.textContent = bot.display_name || bot.bot_id[0].toUpperCase() + bot.bot_id.slice(1);
    const id = document.createElement("span"); id.className = "bot-id"; id.textContent = bot.bot_id;
    identity.append(name, id);
    const badge = document.createElement("span"); badge.className = `badge ${bot.process_alive ? "ready" : "off"}`; badge.textContent = labelState(bot);
    head.append(identity, badge);
    const details = document.createElement("div"); details.className = "details";
    const readiness = (bot.readiness || "—").replaceAll("_", " ");
    for (const [key, value] of [["Processo", bot.pid ? `PID ${bot.pid}` : "Nenhum"], ["Conexão", bot.connected ? "Heartbeat recente" : "Desconectado"], ["Fase", bot.phase || "—"], ["Prontidão", readiness]]) {
      const cell = document.createElement("div"); const label = document.createElement("span"); label.className = "detail-label"; label.textContent = key;
      const val = document.createElement("span"); val.className = "detail-value"; val.textContent = value; cell.append(label, val); details.append(cell);
    }
    const reason = document.createElement("p"); reason.className = "reason";
    reason.textContent = !bot.launchable ? "Launcher ainda não aprovado para execução nesta instalação." : bot.enabled ? "Controle manual disponível." : "Bot desativado no manifesto.";
    const actions = document.createElement("div"); actions.className = "actions";
    const start = document.createElement("button"); start.type = "button"; start.textContent = "Iniciar"; start.disabled = !bot.launchable || !bot.enabled || bot.process_alive;
    const stop = document.createElement("button"); stop.type = "button"; stop.textContent = "Parar"; stop.disabled = !bot.process_alive;
    start.addEventListener("click", () => operate(bot.bot_id, "start")); stop.addEventListener("click", () => operate(bot.bot_id, "stop"));
    actions.append(start, stop); article.append(head, details, reason, actions); return article;
  }
  async function load() {
    if (!token) return;
    refreshButton.disabled = true;
    try {
      const response = await fetch("/admin/api/bots", { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
      list.replaceChildren(...data.bots.map(makeCard)); setStatus(`${data.bots.length} bots · atualização ${new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`, "ok");
    } catch (error) { list.replaceChildren(); setStatus(`Não foi possível consultar: ${String(error.message || error)}`, "error"); }
    finally { refreshButton.disabled = false; }
  }
  async function operate(botId, action) {
    try {
      const response = await fetch(`/admin/api/bots/${encodeURIComponent(botId)}/${action}`, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
      setStatus(`Ação ${action === "start" ? "iniciar" : "parar"} enviada para ${botId}.`, "ok"); await load();
    } catch (error) { setStatus(`Ação recusada: ${String(error.message || error)}`, "error"); }
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); token = tokenInput.value.trim();
    if (!token) return;
    tokenInput.value = ""; tokenInput.disabled = true; form.querySelector("button[type=submit]").hidden = true; clearButton.hidden = false; refreshButton.disabled = false;
    await load(); refreshTimer = setInterval(load, 15000);
  });
  clearButton.addEventListener("click", () => { token = ""; clearInterval(refreshTimer); refreshTimer = null; list.replaceChildren(); tokenInput.disabled = false; tokenInput.value = ""; form.querySelector("button[type=submit]").hidden = false; clearButton.hidden = true; refreshButton.disabled = true; setStatus("Token removido da memória desta página."); tokenInput.focus(); });
  refreshButton.addEventListener("click", load);
  window.addEventListener("pagehide", () => { token = ""; clearInterval(refreshTimer); });
})();
