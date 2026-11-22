/* Painel mínimo de wiring da Célia. Sem bundle, sem payload de conversa.
 * 2026-10-07 (ordem do Pai, caixa 4 do print numerado): deixou de ser uma caixa fixa no canto inferior direito. Agora so aparece dentro de
 * Configuracoes > Runtime: celia-runtime-settings.js chama celiaDebugFlow.montarEm(elemento). Sem elemento montado, nao consulta o servidor. */
(function () {
  "use strict";
  const CHAVE = "celia-debug-flow:v1";
  let ligado = localStorage.getItem(CHAVE) !== "off";
  let painel = null;

  // O <pre id="celia-debug-flow"> nasce dentro do elemento que a aba Runtime entrega; nunca mais em document.body.
  function montarEm(host) {
    if (!host) return null;
    if (painel && painel.isConnected && painel.parentElement === host) return painel;
    if (painel) painel.remove();
    painel = document.createElement("pre");
    painel.id = "celia-debug-flow";
    Object.assign(painel.style, {
      margin: "0", padding: "10px 12px", borderRadius: "10px", overflow: "auto",
      background: "rgba(8,10,16,.88)", color: "#e7eefc", font: "12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace",
      border: "1px solid rgba(255,255,255,.14)", whiteSpace: "pre-wrap"
    });
    host.appendChild(painel);
    atualizar();
    return painel;
  }
  function criar() { return painel; }
  function fmt(d) {
    const b = d.behavior || {};
    const q = d.queue || {};
    const c = d.cognitive || {};
    const t = d.tts || {};
    const v = d.vision || {};
    const speech = b.active_speech || {};
    const ratio = b.talk_ratio || {};
    const cand = (b.candidates || [])[0] || {};
    const resources = d.resources || {};
    return [
      "🧭 " + (d.flow || "Célia"),
      "🎭 " + (b.profile || "?") + "  👂 " + (b.state || "?") + "  end=" + (b.turn_end_probability ?? "?"),
      "brain: " + (resources.brain || "?") + "  script: " + (resources.script || "?") + "  tools: " + (resources.tools || "?"),
      "bank: " + (resources.bank || "?"),
      "🗣️ speaker=" + (b.active_speaker_role || "?") + "  Célia=" + (ratio.celia ?? 0),
      "💭 cog=" + (c.running ? "ON" : "OFF") + " " + (c.provider_last || "none") + " pending=" + (c.pending_candidate_id ? "yes" : "no"),
      "👁️ " + (v.model || "vision-off") + " in=" + (v.in_flight ?? 0) + " topic=" + (v.last_topic || "-"),
      "⚖️ cand=" + (cand.type || "none") + " " + (cand.score ?? "-") + " " + (cand.status || ""),
      "📥 queue=" + (q.pending ?? 0) + " exp=" + (q.expired ?? 0) + " sup=" + (q.superseded ?? 0),
      "🎙️ " + (t.dtype || "?") + "/" + (t.steps ?? "?") + " fallback=" + (t.reliable_steps ?? "?"),
      "🔊 " + (speech.playback_state || "idle") + "  proc=" + (d.processing ? "YES" : "no"),
      "🖥️ screen=" + (((b.metrics || {}).screen_events) ?? 0) + "  🔇 echo=" + (((b.metrics || {}).self_echo_suppressed) ?? 0)
    ].join("\n");
  }

  async function atualizar() {
    if (!ligado) return;
    const el = criar();
    if (!el || !el.isConnected) { painel = null; return; }
    try {
      const r = await fetch("/bridge/debug/state", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      el.textContent = fmt(await r.json());
    } catch (e) {
      el.textContent = "🧭 Célia debug\n⚠️ " + String(e && e.message ? e.message : e);
    }
  }

  function desligar() {
    ligado = false;
    localStorage.setItem(CHAVE, "off");
    if (painel) { painel.textContent = "Fluxo desligado (celiaDebugFlow.ligar() religa)."; }
  }
  function ligar() {
    ligado = true;
    localStorage.setItem(CHAVE, "on");
    atualizar();
    return true;
  }

  window.celiaDebugFlow = { ligar: ligar, desligar: desligar, atualizar: atualizar, montarEm: montarEm };
  setInterval(atualizar, 1000);
})();
