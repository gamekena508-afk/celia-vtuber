(() => {
  "use strict";

  // Native chat adapter. The React message owns its ASR snapshot; no overlay,
  // extra websocket, DOM matching by text, or second conversation history.
  const history = [];
  let sequence = 0;

  function handlePayload(message) {
    if (!message || !["user-input-transcription", "asr-trace"].includes(message.type)) return null;
    if (message.source !== "microphone" || typeof message.raw_text !== "string") return null;
    if (!message.raw_text && !message.asr_rejected) return null;
    const id = String(message.utterance_id || "asr-local-" + (++sequence));
    const index = history.findIndex(item => item.id === id);
    const previous = index >= 0 ? history[index] : {};
    const next = {
      ...previous, id,
      raw: message.raw_text || previous.raw || "",
      corrected: typeof message.corrected_text === "string"
        ? message.corrected_text : previous.corrected || message.text || "",
      decision: message.asr_decision || previous.decision || "KEEP",
      event: message.asr_event || previous.event || "",
      rejected: message.asr_rejected ?? previous.rejected ?? false,
      uncertain: message.asr_uncertain ?? previous.uncertain ?? false,
      rules: message.asr_normalization_rules?.length
        ? message.asr_normalization_rules : previous.rules || [],
      reason: message.asr_reject_reason || previous.reason || "",
      stage: message.asr_stage || previous.stage || "",
      route: message.asr_route_status || previous.route || "",
      acoustic: message.asr_acoustic || previous.acoustic || null,
      submitted: Boolean(message.asr_submitted_to_brain || previous.submitted),
    };
    if (index >= 0) history[index] = next;
    else history.unshift(next);
    history.splice(200);
    return next;
  }

  function upsertMessage(messages, text, asr) {
    const index = asr ? messages.findIndex(message => message.asr?.id === asr.id) : -1;
    const message = {
      ...(index >= 0 ? messages[index] : {
        id: asr ? "asr-" + asr.id : "typed-" + Date.now() + "-" + (++sequence),
        role: "human", type: "text", timestamp: new Date().toISOString(),
      }),
      content: text,
      ...(asr ? { asr } : {}),
    };
    if (index < 0) return [...messages, message];
    return messages.map((entry, position) => position === index ? message : entry);
  }

  function renderMessage(React, item) {
    const h = React.createElement;
    const row = (label, text, color) => h("div", {
      key: label, className: "asr-chat-row",
      style: {color, marginBottom: "6px", whiteSpace: "pre-wrap", overflowWrap: "anywhere"},
    }, h("strong", {style: {fontSize: "11px", display: "block"}}, label), text);
    const state = item.rejected ? "Descartado · não enviado ao cérebro"
      : item.submitted ? "Encaminhado ao cérebro"
      : item.route === "ROUTING_SUPPRESSED" ? "Registrado · sem resposta neste modo"
      : item.route === "PROFILE_COMMAND_CONSUMED" ? "Comando de perfil aplicado"
      : item.route === "HUD_COMMAND_CONSUMED" ? "Comando de HUD aplicado"
      : item.route === "ROTEIRO_INTERRUPTION_CONSUMED" ? "Interrupção aplicada"
      : "Transcrito · aguardando encaminhamento";
    const detail = [item.reason, ...(item.rules || [])].filter(Boolean).join(" · ");
    return h("div", {className: "celia-asr-chat", "data-utterance-id": item.id,
      style: {minWidth: 0, fontSize: "13px", lineHeight: 1.4}},
      row("ASR", item.raw || "[sem transcrição · áudio descartado antes do Whisper]", "#f7b955"),
      row("Corrigido", item.corrected || (item.rejected ? "[não produzido]" : item.raw), "#67e8f9"),
      h("div", {className: "asr-chat-status", style: {
        fontSize: "11px", color: item.rejected ? "#fb7185" : item.uncertain ? "#c4b5fd" : "#cbd5e1",
      }}, !item.rejected && item.raw === item.corrected ? "Sem alteração · " + state : state),
      detail ? h("details", {style: {fontSize: "10px", marginTop: "4px"}},
        h("summary", null, "Detalhes da transcrição"), detail) : null);
  }

  window.celiaAsrInspector = {
    handlePayload, upsertMessage, renderMessage,
    history: () => history.map(item => ({ ...item })),
  };
})();
