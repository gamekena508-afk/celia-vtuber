/* Rosto da Célia — aplica emoção que chega por evento, não só por fala.
 *
 * agente: claude | 2026-08-11 | Fatia 6 do plano travado
 *
 * POR QUE ESTE ARQUIVO EXISTE
 * ---------------------------
 * O bundle só aplica expressão dentro do bloco `if (audioBase64)`: o rosto
 * muda quando ela FALA, e só. Emoção que nasce de um evento — susto no
 * Minecraft, interrupção, backchannel, o motor de emoção — nunca chegava
 * aqui. Ia pro `ws://127.0.0.1:8770`, de um tracker que morreu e que só
 * rodava em Windows com VTube Studio.
 *
 * O backend agora transmite `{"type":"celia-face","expression":<indice>}`.
 * Este script escuta e aplica.
 *
 * COMO ELE ENGANCHA
 * -----------------
 * Embrulhando `window.WebSocket`, o mesmo padrão que
 * `external-playback-bridge.js` já usa neste projeto. Não toca no bundle
 * minificado: bundle é artefato de build, patch nele se perde no próximo
 * `npm run build` e não dá pra revisar em diff.
 *
 * O acessor do modelo (`window.getLAppAdapter`) é o mesmo que o próprio
 * bundle usa pra aplicar expressão junto com áudio — não é gambiarra, é a
 * porta que o app já abre.
 */
(function instalarRostoCelia() {
  "use strict";

  const WebSocketNativo = window.WebSocket;
  if (!WebSocketNativo) return;

  const LOG = "[rosto]";
  const TEASING_SPEAKING_INDEX = 22; // trol enquanto fala: sem lingua
  const TEASING_FINAL_INDEX = 21;    // trol final da fala: lingua para fora
  // O adapter só existe depois que o Live2D termina de carregar. Emoção que
  // chega antes disso fica guardada e é aplicada quando o rosto aparece —
  // perder a primeira reação dela por causa de ordem de boot seria burrice.
  let pendente = null;
  let ultimoAplicado = null;
  let ultimaEmocaoEmitida = null;
  let emocaoVisual = "neutral";
  let permitido = false;
  let modeloAtual = null;
  let desejado = null;
  let conexaoSeq = 0;
  let conexaoAtual = "";
  let atividade = { speechId: "", playerId: "" };
  let contextoEmocao = { source: "", metadata: {} };
  let varianteTeasing = "speaking";
  let debug = new URLSearchParams(window.location.search).get("face_debug") === "1";
  const eventos = [];
  function registrar(evento, detalhes = {}) {
    if (!debug) return;
    let motion = {};
    try {
      const state = window.celiaMotionDirector && window.celiaMotionDirector.state ? window.celiaMotionDirector.state() : null;
      if (state) motion = { motionState: state.state, motionPreset: state.preset, motionEmotion: state.emotion };
    } catch (_) {}
    const metadata = contextoEmocao.metadata || {};
    const item = { timestamp: Date.now(), evento, connectionId: conexaoAtual, responseId: metadata.turn_id || metadata.response_id || "", speechId: atividade.speechId || "", playerId: atividade.playerId || "", source: contextoEmocao.source || "", ...motion, ...detalhes };
    eventos.push(item);
    if (eventos.length > 500) eventos.shift();
    console.debug("[face-lifecycle]", JSON.stringify(item));
  }

  // All expression writes, including the legacy React IDLE reset, pass here.
  // Only this controller may select affect; playback owns activity, not a face.
  function instalarDono(adapter) {
    const model = typeof adapter.getModel === "function" ? adapter.getModel() : adapter;
    if (!model || typeof model.setExpression !== "function") return false;
    if (!model.__celiaAffectOwner) {
      const original = model.setExpression.bind(model);
      model.setExpression = function(nome) {
        if (!permitido) {
          registrar("blocked-external-expression", { expression: nome });
          return;
        }
        return original(nome);
      };
      model.__celiaAffectOwner = true;
    }
    const changed = modeloAtual !== model;
    modeloAtual = model;
    return changed;
  }

  function pegarAdapter() {
    try {
      const f = window.getLAppAdapter;
      return typeof f === "function" ? f() : null;
    } catch (_) {
      return null;
    }
  }

  function aplicar(indice, emocao) {
    const adapter = pegarAdapter();
    if (!adapter) {
      pendente = { indice, emocao };
      return false;
    }
    try {
      // O bundle aceita índice numérico e traduz pelo nome. Repetimos o
      // mesmo caminho: getExpressionName(n) -> setExpression(nome).
      const nome =
        typeof adapter.getExpressionName === "function"
          ? adapter.getExpressionName(indice)
          : null;
      if (!nome) {
        console.warn(`${LOG} modelo nao tem expressao no indice ${indice}`);
        return false;
      }
      instalarDono(adapter);
      registrar("requested", { requestedExpression: nome, requestedIndex: indice, emotion: emocao });
      permitido = true;
      try { adapter.setExpression(nome); } finally { permitido = false; }
      desejado = { indice, emocao, nome };
      registrar("applied", { appliedExpression: nome, appliedIndex: indice, emotion: emocao });
      ultimoAplicado = { indice, emocao, nome, em: Date.now() };
      console.log(`${LOG} ${emocao} -> expressao ${indice} (${nome})`);
      return true;
    } catch (erro) {
      console.warn(`${LOG} falhou ao aplicar:`, erro);
      return false;
    }
  }

  function aplicarVarianteTeasing(fase) {
    varianteTeasing = fase === "final" ? "final" : "speaking";
    const indice = varianteTeasing === "final" ? TEASING_FINAL_INDEX : TEASING_SPEAKING_INDEX;
    const ok = aplicar(indice, "teasing");
    registrar("teasing-variant", { variant: varianteTeasing, expression: indice, applied: ok });
    return ok;
  }

  // Enquanto houver pendência, tenta de novo. Para sozinho quando aplica.
  setInterval(() => {
    const adapter = pegarAdapter();
    if (adapter && instalarDono(adapter) && desejado) {
      pendente = { indice: desejado.indice, emocao: desejado.emocao };
    }
    if (pendente && aplicar(pendente.indice, pendente.emocao)) pendente = null;
  }, 500);

  // ── Narração de bastidor (Fatia 7) ────────────────────────────────────
  // A mente que FALA narra o que a mente que FAZ está executando. Vai numa
  // faixa própria, separada da legenda: o que ela RESPONDE e o que ela está
  // FAZENDO são coisas diferentes e não podem disputar o mesmo espaço.
  let faixa = null;
  let sumirEm = null;
  // Native Pet uses the separate subtitle surface for visible speech. Keep
  // this technical narration out of that small desktop window.
  const narracaoNoPalcoPet = new URLSearchParams(window.location.search).get("pet") !== "1";

  function faixaNarracao() {
    if (faixa) return faixa;
    faixa = document.createElement("div");
    faixa.id = "celia-narracao";
    faixa.setAttribute("role", "status");
    faixa.setAttribute("aria-live", "polite");
    Object.assign(faixa.style, {
      position: "fixed",
      left: "50%",
      transform: "translateX(-50%)",
      bottom: "12px",
      zIndex: "2147483000",
      maxWidth: "min(80vw, 640px)",
      padding: "8px 16px",
      borderRadius: "999px",
      // Contraste alto de propósito: isto aparece por cima do avatar, que
      // pode ser claro ou escuro dependendo da expressão e do fundo.
      background: "rgba(12, 14, 20, 0.86)",
      color: "#f2f4f8",
      font: "500 15px/1.35 system-ui, -apple-system, Segoe UI, sans-serif",
      textAlign: "center",
      boxShadow: "0 2px 14px rgba(0,0,0,.45)",
      backdropFilter: "blur(6px)",
      opacity: "0",
      transition: "opacity .18s ease",
      pointerEvents: "none",
    });
    document.body.appendChild(faixa);
    return faixa;
  }

  function narrar(texto, camada) {
    if (!narracaoNoPalcoPet) return;
    const el = faixaNarracao();
    el.textContent = texto;
    el.style.opacity = "1";
    // Erro fica mais tempo: é o que o Pai precisa ler antes de sumir.
    const duracao = camada === "erro" ? 5000 : 3200;
    if (sumirEm) clearTimeout(sumirEm);
    sumirEm = setTimeout(() => {
      el.style.opacity = "0";
    }, duracao);
    console.log(`${LOG} narracao (${camada}): ${texto}`);
  }

  function emitirAgora(msg, emocao, origem) {
    const agora = Date.now();
    const assinatura = `${emocao}|${origem}`;
    if (
      ultimaEmocaoEmitida
      && ultimaEmocaoEmitida.assinatura === assinatura
      && agora - ultimaEmocaoEmitida.em < 250
    ) return true;

    ultimaEmocaoEmitida = { assinatura, emocao, origem, em: agora };
    if (typeof window.dispatchEvent !== "function" || typeof window.CustomEvent !== "function") return true;
    window.dispatchEvent(new window.CustomEvent("celia-emotion", {
      detail: {
        emotion: emocao,
        source: origem,
        ttlSec: Number(msg.ttl_sec) || 0,
        persistent: Boolean(msg.persistent),
        expression: typeof msg.expression === "number" ? msg.expression : null,
        metadata: msg.metadata && typeof msg.metadata === "object" ? msg.metadata : {},
      },
    }));
    return true;
  }

  function aplicarMensagem(msg, emocao) {
    if (emocao === "teasing") {
      aplicarVarianteTeasing("speaking");
      return;
    }
    const indice = msg.expression;
    if (typeof indice !== "number") return;
    if (!aplicar(indice, emocao || "?")) {
      pendente = { indice, emocao: emocao || "?" };
    }
  }

  function emitirEmocao(msg) {
    const raw = msg.emotion || msg.live2d_tag;
    if (!raw) return false; // Missing affect is not a neutral response.
    const emocao = String(raw).trim().toLowerCase();
    const origem = String(msg.source || "celia-face");
    if (["trol_speaking", "trol_idle"].includes(emocao) || origem === "interrupt" || origem === "chat_input") {
      registrar("ignored-activity", { emotion: emocao, source: origem });
      return false;
    }
    const metadata = msg.metadata && typeof msg.metadata === "object" ? msg.metadata : {};
    const mesmaEmocao = emocaoVisual === emocao && contextoEmocao.source === origem;
    contextoEmocao = {
      source: origem,
      metadata: Object.keys(metadata).length ? metadata : (mesmaEmocao ? contextoEmocao.metadata : {}),
    };
    emocaoVisual = emocao;
    if (emocao === "teasing") varianteTeasing = "speaking";
    registrar("received", { emotion: emocao, metadata });
    return emitirAgora(msg, emocao, origem);
  }

  function tratar(dados) {
    let msg;
    try {
      msg = JSON.parse(dados);
    } catch (_) {
      return;
    }
    if (!msg) return;

    if (msg.type === "celia-narracao") {
      if (typeof msg.text === "string" && msg.text) narrar(msg.text, msg.camada || "?");
      return;
    }

    if (msg.type === "celia-emotion") {
      emitirEmocao(msg);
      return;
    }

    if (msg.type !== "celia-face") return;
    // Backward-compatible fallback: old servers only send celia-face.
    if (emitirEmocao(msg)) aplicarMensagem(msg, msg.emotion || "?");
  }

  function encontrarDescritor(objeto, chave) {
    let atual = objeto;
    while (atual) {
      const descritor = Object.getOwnPropertyDescriptor(atual, chave);
      if (descritor) return descritor;
      atual = Object.getPrototypeOf(atual);
    }
    return null;
  }

  function ehCanalDaCelia(url) {
    try {
      return new URL(String(url), window.location.href).pathname === "/client-ws";
    } catch (_) {
      return false;
    }
  }

  // The upstream bundle emits its initial requests immediately after creating
  // this socket. Hold only those requests until the native connection opens.
  function guardarEnviosDuranteConexao(ws) {
    const descritor = encontrarDescritor(ws, "readyState");
    if (!descritor || typeof descritor.get !== "function") return;

    const estadoNativo = () => descritor.get.call(ws);
    const enviarNativo = ws.send.bind(ws);
    const pendentes = [];

    Object.defineProperty(ws, "readyState", {
      configurable: true,
      get() {
        const estado = estadoNativo();
        return estado === WebSocketNativo.CONNECTING ? WebSocketNativo.OPEN : estado;
      },
    });

    ws.send = (dados) => {
      if (estadoNativo() === WebSocketNativo.CONNECTING) {
        pendentes.push(dados);
        return;
      }
      return enviarNativo(dados);
    };

    ws.addEventListener("open", () => {
      while (pendentes.length) {
        enviarNativo(pendentes.shift());
      }
    });
  }

  window.WebSocket = function (url, protocolos) {
    const ws = protocolos
      ? new WebSocketNativo(url, protocolos)
      : new WebSocketNativo(url);
    const celia = ehCanalDaCelia(url);
    const connectionId = celia ? `ws-${++conexaoSeq}` : "";
    if (celia) guardarEnviosDuranteConexao(ws);
    ws.addEventListener("message", (evento) => {
      if (typeof evento.data === "string") {
        if (connectionId) conexaoAtual = connectionId;
        tratar(evento.data);
      }
    });
    return ws;
  };
  window.WebSocket.prototype = WebSocketNativo.prototype;
  Object.assign(window.WebSocket, WebSocketNativo);

  window.addEventListener("celia-lipsync-start", (event) => {
    const detail = event && event.detail ? event.detail : {};
    atividade = { speechId: String(detail.speechId || ""), playerId: String(detail.playerId || "") };
    registrar("speech-start", {});
    if (emocaoVisual === "teasing") aplicarVarianteTeasing("speaking");
  });
  window.addEventListener("celia-lipsync-stop", (event) => {
    const detail = event && event.detail ? event.detail : {};
    const speechId = String(detail.speechId || "");
    if (!speechId || !atividade.speechId || speechId === atividade.speechId) {
      registrar("speech-stop", { stoppedSpeechId: speechId });
      atividade = { speechId: "", playerId: "" };
      if (emocaoVisual === "teasing") aplicarVarianteTeasing("final");
    }
  });

  window.addEventListener("celia-identidade-ready", () => {
    if (!desejado) return;
    registrar("identity-ready-reapply", {
      requestedExpression: desejado.nome,
      requestedIndex: desejado.indice,
      emotion: desejado.emocao,
    });
    aplicar(desejado.indice, desejado.emocao);
  });

  // Superfície de prova: sem isto, testar o rosto exige clicar e olhar. Com
  // isto, dá pra provar por script que a expressão foi aplicada de verdade.
  window.celiaRosto = {
    aplicar,
    narrar,
    debug: (enabled = true) => { debug = Boolean(enabled); return eventos.slice(); },
    trace: () => eventos.slice(),
    estado: () => ({
      ultimoAplicado,
      ultimaEmocaoEmitida,
      pendente,
      emocaoVisual,
      desiredExpression: desejado && desejado.nome,
      ownerInstalled: !!modeloAtual,
      connectionId: conexaoAtual,
      activity: { ...atividade },
      teasingVariant: emocaoVisual === "teasing" ? varianteTeasing : null,
      segurarAte: 0,
      neutroPendente: false,
      temAdapter: !!pegarAdapter(),
      narracao: faixa ? { texto: faixa.textContent, visivel: faixa.style.opacity === "1" } : null,
    }),
  };

  console.log(`${LOG} instalado`);
})();
