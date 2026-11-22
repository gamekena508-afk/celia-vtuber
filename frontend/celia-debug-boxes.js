/*
 * celia-debug-boxes.js — duas caixas pretas de debug (ligadas por padrao, como o painel celia-debug-flow).
 *
 *   Caixa A "Thinking"  : o que o modelo esta fazendo AGORA (uma linha, com emoji).
 *   Caixa B "Processos" : cada processo que acontece, com emoji; as linhas novas
 *                         entram embaixo e empurram as antigas para cima, como uma
 *                         legenda, e vao sumindo sozinhas.
 *
 * FONTE DOS DADOS: o stream que ja existe, /debug/observability/events (SSE).
 * Nenhum barramento novo, nenhuma conexao a mais alem desta, e NUNCA texto de
 * conversa: o servidor so publica metadados estruturais (estagio, fonte, ms).
 *
 * ISTO NAO E LEGENDA. O Pet tem UMA superficie de legenda (celia-legenda-embutida.js,
 * ver docs/CONTRATO_LEGENDA_PET.md) e este arquivo nao a toca, nao abre janela
 * e nao cria um segundo caminho de legenda. E um overlay de debug da pagina.
 * Aparece sozinho (igual ao painel celia-debug-flow) e some quando se pede:
 *     - abrir a interface com  ?debugboxes=0   (fica salvo)   ou
 *     - no console:  celiaDebugBoxes.desligar()  /  .ligar()
 * (?debugboxes=1 liga de novo). Em gravacao/stream, desligue antes de comecar.
 *
 * `mapear(evento)` e uma funcao pura (testada em tests/test_celia_debug_boxes.js).
 */
(function (raiz) {
  "use strict";

  const CHAVE = "celia-debug-boxes:v1";
  const MAX_LINHAS = 6;        // linhas visiveis na caixa B
  const TTL_MS = 14000;        // quanto tempo uma linha fica antes de sumir
  const AGRUPA_MS = 4000;      // mesma linha repetida dentro disso vira "xN"
  const MAX_POR_SEGUNDO = 4;   // protecao contra rajada de eventos
  const OCIOSA_MS = 25000;     // a caixa A volta a "parada" depois disso

  // ── funcoes puras ──────────────────────────────────────────────────────────
  function ms(valor) {
    if (typeof valor !== "number" || !isFinite(valor)) return "";
    if (valor < 1000) return Math.round(valor) + " ms";
    return (valor / 1000).toFixed(2).replace(".", ",") + " s";
  }

  function fonteNome(meta) {
    const s = String((meta && meta.source) || "");
    if (s === "screen") return { emoji: "🖥️", nome: "tela" };
    if (s === "camera") return { emoji: "📷", nome: "câmera" };
    return { emoji: "👁️", nome: s || "visão" };
  }

  function juntar(partes) {
    return partes.filter(function (p) { return p; }).join(" ");
  }

  // Devolve { a: {emoji, texto}|null, b: {emoji, texto, chave}|null } ou null.
  function mapear(ev) {
    if (!ev || typeof ev !== "object") return null;
    const no = String(ev.node_id || "");
    const tipo = String(ev.kind || "");
    const st = String(ev.status || "");
    const meta = ev.metadata || {};
    const dur = ms(ev.duration_ms);
    const erro = st === "error" || st === "fail" || st === "failed";

    // ── visão ────────────────────────────────────────────────────────────
    if (no === "vision.source") {
      const f = fonteNome(meta);
      if (st === "on") {
        const res = meta.width && meta.height ? "(" + meta.width + "x" + meta.height + ")" : "";
        return { a: null, b: { emoji: f.emoji, texto: juntar([f.nome, "ligada", res]), chave: "src-on-" + meta.source } };
      }
      const motivo = meta.reason && meta.reason !== "signal" ? "(" + meta.reason + ")" : "";
      return { a: null, b: { emoji: f.emoji + "⛔", texto: juntar([f.nome, "desligada", motivo]), chave: "src-off-" + meta.source } };
    }
    if (no === "vision.frame") {
      const n = meta.count > 1 ? "×" + meta.count : "";
      if (meta.reason === "budget") {
        return { a: null, b: { emoji: "⏳", texto: juntar(["visão no teto", (meta.calls_window || "?") + "/" + (meta.calls_max || "?") + " por min", n]), chave: "frame-budget" } };
      }
      if (meta.reason === "source_off") {
        return { a: null, b: { emoji: "🚫", texto: juntar(["frame recusado, fonte desligada", n]), chave: "frame-off-" + meta.source } };
      }
      return null;
    }
    if (no === "vision.analysis") {
      const f = fonteNome(meta);
      if (st === "start") {
        return {
          a: { emoji: "👁️", texto: "olhando a " + f.nome + "…" },
          b: { emoji: "📸", texto: juntar(["analisando", f.nome]), chave: "an-" + meta.source },
        };
      }
      if (st === "ok") return { a: null, b: { emoji: "🧠", texto: juntar(["visão pronta", dur, "·", f.nome]), chave: "an-ok-" + meta.source } };
      if (st === "stale") return { a: null, b: { emoji: "🗑️", texto: juntar(["análise descartada", f.nome]), chave: "an-stale-" + meta.source } };
      if (erro) return { a: null, b: { emoji: "⚠️", texto: juntar(["visão falhou", meta.error_type || "", f.nome]), chave: "an-fail-" + meta.source } };
      return null;
    }

    // ── pensamento (loop cognitivo) ──────────────────────────────────────
    if (no === "cognitive.wake") {
      if (st === "coalesced") {
        return { a: null, b: { emoji: "🔁", texto: juntar(["wakes juntados", meta.count > 1 ? "×" + meta.count : ""]), chave: "wake-co" } };
      }
      return { a: null, b: { emoji: "⏰", texto: juntar(["pensamento acordado:", meta.reason || "?"]), chave: "wake-" + (meta.reason || "") } };
    }
    if (no === "cognitive.think") {
      if (st === "start") {
        return {
          a: { emoji: "🤔", texto: "pensando…" },
          b: { emoji: "🤔", texto: juntar(["pensando", meta.reason ? "(" + meta.reason + ")" : ""]), chave: "think" },
        };
      }
      if (st === "ok") {
        const fala = meta.should_speak ? "vai falar" : "ficou quieta";
        return {
          a: { emoji: "💭", texto: juntar(["pensou", dur, "·", fala]) },
          b: { emoji: "💭", texto: juntar(["pensou", dur, meta.provider ? "· " + meta.provider : "", "·", fala]), chave: "think-ok" },
        };
      }
      if (st === "empty") return { a: { emoji: "😶", texto: "nada a dizer " + dur }, b: { emoji: "😶", texto: juntar(["pensamento vazio", dur]), chave: "think-empty" } };
      if (st === "stale") return { a: { emoji: "🗑️", texto: "pensamento velho, descartado" }, b: { emoji: "🗑️", texto: "pensamento descartado", chave: "think-stale" } };
      return null;
    }

    // ── roteador de LLM ──────────────────────────────────────────────────
    if (no === "router.call") {
      if (st === "ok") {
        return { a: null, b: { emoji: "🔀", texto: juntar([meta.role || "llm", "→", meta.provider || "?", dur]), chave: "route-" + (meta.role || "") } };
      }
      return { a: null, b: { emoji: "⚠️", texto: juntar([meta.role || "llm", "sem resposta", dur]), chave: "route-fail-" + (meta.role || "") } };
    }

    // ── turno de conversa (eventos que o servidor já emitia) ─────────────
    if (tipo === "trace_start") {
      const quem = no.indexOf("browser") >= 0 ? "navegador" : no.indexOf("external") >= 0 ? "externo" : "bridge";
      return { a: null, b: { emoji: "🎙️", texto: "turno começou (" + quem + ")", chave: "turn-start" } };
    }
    if (tipo === "trace_end") {
      return { a: { emoji: "😴", texto: "parada" }, b: { emoji: erro ? "⚠️" : "🏁", texto: juntar(["turno", erro ? "com erro" : "fim", dur]), chave: "turn-end" } };
    }
    if (no === "agent.basic_memory") {
      if (tipo === "span_start") return { a: { emoji: "💬", texto: "gerando resposta…" }, b: { emoji: "💬", texto: "gerando resposta", chave: "agent" } };
      if (tipo === "span_end" || tipo === "span_finish") {
        return { a: { emoji: erro ? "⚠️" : "✅", texto: juntar([erro ? "agente falhou" : "resposta pronta", dur]) }, b: { emoji: erro ? "⚠️" : "✅", texto: juntar(["resposta", dur]), chave: "agent-end" } };
      }
      if (st === "unavailable" || tipo === "agent_unavailable") return { a: null, b: { emoji: "⚠️", texto: "agente indisponível", chave: "agent-down" } };
    }
    if (tipo === "tts_generated" || (no === "service.omnivoice" && !erro)) {
      return { a: null, b: { emoji: "🗣️", texto: juntar(["TTS", dur]), chave: "tts" } };
    }
    if (tipo === "tts_wait" || (no === "service.tts" && !erro)) {
      return { a: null, b: { emoji: "⏳", texto: juntar(["TTS na fila", dur]), chave: "tts-wait" } };
    }
    if (tipo === "bank_playback" || no === "tts.dispatch") {
      return { a: null, b: { emoji: "🔁", texto: "áudio reaproveitado", chave: "bank" } };
    }
    if (tipo === "turn_cancelled") {
      return { a: null, b: { emoji: "✋", texto: "turno cancelado", chave: "cancel" } };
    }
    if (meta.tool_name) {
      return { a: null, b: { emoji: "🛠️", texto: juntar([meta.tool_name, meta.tool_status || st, dur]), chave: "tool-" + meta.tool_name } };
    }
    if (erro || tipo === "error") {
      return { a: null, b: { emoji: "⚠️", texto: juntar([no, "erro", meta.error_type || ""]), chave: "err-" + no } };
    }
    return null; // desconhecido e sem erro: não polui a caixa
  }

  const api = { mapear: mapear, ms: ms, _constantes: { MAX_LINHAS: MAX_LINHAS, TTL_MS: TTL_MS, AGRUPA_MS: AGRUPA_MS, MAX_POR_SEGUNDO: MAX_POR_SEGUNDO } };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof raiz.document === "undefined") return; // rodando em node (teste): só a parte pura

  // ── interface ──────────────────────────────────────────────────────────────
  const doc = raiz.document;
  let ligado = false;
  let caixa = null, corpoA = null, corpoB = null, fonteSSE = null, podador = null;
  let corpoEmocao = null, emocaoAtual = null;
  let ultimaSeq = 0;
  let linhas = [];         // { el, chave, ts, n, texto, emoji }
  let ultimoA = 0;
  let janelaSeg = { t: 0, n: 0 };

  function querLigado() {
    try {
      const q = new URLSearchParams(raiz.location.search);
      if (q.get("debugboxes") === "1") { raiz.localStorage.setItem(CHAVE, "on"); return true; }
      if (q.get("debugboxes") === "0") { raiz.localStorage.setItem(CHAVE, "off"); return false; }
      return raiz.localStorage.getItem(CHAVE) !== "off"; // padrao: ligado
    } catch (e) { return true; }
  }

  function estilo(el, css) { Object.assign(el.style, css); return el; }

  const BASE = {
    background: "rgba(0,0,0,.86)", color: "#f1f1ed",
    font: "12px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    borderRadius: "10px", padding: "6px 10px", boxSizing: "border-box",
    border: "1px solid rgba(255,255,255,.10)", width: "100%",
  };

  function criar() {
    if (caixa) return;
    caixa = estilo(doc.createElement("div"), {
      position: "fixed", left: "10px", bottom: "10px", width: "320px",
      zIndex: "2147483645", pointerEvents: "none", display: "flex",
      flexDirection: "column", gap: "6px",
    });
    caixa.id = "celia-debug-boxes";
    const a = estilo(doc.createElement("div"), BASE);
    const tituloA = estilo(doc.createElement("div"), { color: "#74746f", fontSize: "10px", letterSpacing: ".08em" });
    tituloA.textContent = "THINKING";
    corpoA = doc.createElement("div");
    // 2a linha (ordem do Pai, 2026-10-07): a emocao em que ela esta agora, vinda do evento "celia-emotion" que celia-face.js ja emite.
    corpoEmocao = estilo(doc.createElement("div"), { color: "#9fb4d8", marginTop: "2px" });
    pintarEmocao();
    a.appendChild(tituloA); a.appendChild(corpoA); a.appendChild(corpoEmocao);
    const b = estilo(doc.createElement("div"), Object.assign({}, BASE, { minHeight: "34px" }));
    const tituloB = estilo(doc.createElement("div"), { color: "#74746f", fontSize: "10px", letterSpacing: ".08em", marginBottom: "2px" });
    tituloB.textContent = "PROCESSOS";
    corpoB = estilo(doc.createElement("div"), { display: "flex", flexDirection: "column", justifyContent: "flex-end" });
    b.appendChild(tituloB); b.appendChild(corpoB);
    caixa.appendChild(a); caixa.appendChild(b);
    doc.body.appendChild(caixa);
    definirA("😴", "parada");
  }

  function pintarEmocao() {
    if (corpoEmocao) corpoEmocao.textContent = "🎭 emoção: " + (emocaoAtual || "—");
  }
  if (typeof raiz.addEventListener === "function") {
    raiz.addEventListener("celia-emotion", function (e) {
      const v = e && e.detail && e.detail.emotion;
      if (typeof v === "string" && v) { emocaoAtual = v; pintarEmocao(); }
    });
  }

  function definirA(emoji, texto) {
    if (!corpoA) return;
    corpoA.textContent = emoji + " " + texto;
    ultimoA = Date.now();
  }

  function removerLinha(l) {
    l.el.style.opacity = "0";
    setTimeout(function () { if (l.el.parentNode) l.el.parentNode.removeChild(l.el); }, 350);
  }

  function pintar(l) {
    l.el.textContent = l.emoji + " " + l.texto + (l.n > 1 ? "  ×" + l.n : "");
  }

  function adicionarB(b) {
    const agora = Date.now();
    const anterior = linhas[linhas.length - 1];
    if (anterior && anterior.chave === b.chave && agora - anterior.ts < AGRUPA_MS) {
      anterior.n += 1; anterior.ts = agora; anterior.texto = b.texto; anterior.emoji = b.emoji;
      pintar(anterior);
      return;
    }
    const el = estilo(doc.createElement("div"), {
      opacity: "0", transition: "opacity .25s ease", whiteSpace: "nowrap",
      overflow: "hidden", textOverflow: "ellipsis",
    });
    const l = { el: el, chave: b.chave, ts: agora, n: 1, texto: b.texto, emoji: b.emoji };
    pintar(l);
    corpoB.appendChild(el);              // entra embaixo; as antigas sobem
    raiz.requestAnimationFrame(function () { el.style.opacity = "1"; });
    linhas.push(l);
    while (linhas.length > MAX_LINHAS) removerLinha(linhas.shift());
  }

  function podar() {
    const agora = Date.now();
    linhas = linhas.filter(function (l) {
      if (agora - l.ts > TTL_MS) { removerLinha(l); return false; }
      return true;
    });
    if (agora - ultimoA > OCIOSA_MS && corpoA && corpoA.textContent.indexOf("parada") < 0) definirA("😴", "parada");
  }

  function aoEvento(ev) {
    if (!ev || typeof ev.sequence !== "number" || ev.sequence <= ultimaSeq) return;
    ultimaSeq = ev.sequence;
    const m = mapear(ev);
    if (!m) return;
    if (m.a) definirA(m.a.emoji, m.a.texto);
    if (m.b) {
      const t = Math.floor(Date.now() / 1000);
      if (janelaSeg.t !== t) janelaSeg = { t: t, n: 0 };
      if (janelaSeg.n >= MAX_POR_SEGUNDO) return;
      janelaSeg.n += 1;
      adicionarB(m.b);
    }
  }

  function conectar() {
    fetch("/debug/observability/snapshot?after=999999999", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) { ultimaSeq = (d && d.latest_sequence) || 0; })
      .catch(function () {})
      .then(function () {
        if (!ligado) return;
        fonteSSE = new EventSource("/debug/observability/events?after=" + ultimaSeq);
        fonteSSE.addEventListener("trace", function (e) {
          try { aoEvento(JSON.parse(e.data)); } catch (err) {}
        });
        // EventSource reconecta sozinho; `ultimaSeq` evita repetir eventos.
      });
  }

  function ligar() {
    try { raiz.localStorage.setItem(CHAVE, "on"); } catch (e) {}
    if (ligado) return estado();
    ligado = true;
    criar();
    conectar();
    podador = setInterval(podar, 500);
    return estado();
  }

  function desligar() {
    try { raiz.localStorage.setItem(CHAVE, "off"); } catch (e) {}
    ligado = false;
    if (fonteSSE) { fonteSSE.close(); fonteSSE = null; }
    if (podador) { clearInterval(podador); podador = null; }
    if (caixa && caixa.parentNode) caixa.parentNode.removeChild(caixa);
    caixa = corpoA = corpoB = corpoEmocao = null;
    linhas = [];
    return estado();
  }

  function estado() {
    return { ligado: ligado, ultimaSequencia: ultimaSeq, linhas: linhas.length, conectado: Boolean(fonteSSE) };
  }

  raiz.celiaDebugBoxes = Object.assign({ ligar: ligar, desligar: desligar, estado: estado, _mapear: mapear }, {});

  function iniciar() { if (querLigado()) ligar(); }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})(typeof window !== "undefined" ? window : globalThis);
