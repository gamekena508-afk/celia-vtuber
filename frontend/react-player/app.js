// Célia React V2 — player (P2). Contrato 1.0.0: docs/planos/react/contratos/.
// Dois back-ends com a mesma interface: FixtureBackend (?fixture=1, sem servidor de análise)
// e ApiBackend (React Session API do Codex). O player nunca toca trecho que não esteja READY.
import { PlayedAccumulator, segmentAt, timelineRuns, releasedEvents, fmt } from "./timeline.js";

const $ = (id) => document.getElementById(id);
const qs = new URLSearchParams(location.search);
const RATES = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
const TICK_MS = 250;

const el = {
  player: $("player"), video: $("video"), barra: $("barra"), trilho: $("trilho"), pegador: $("pegador"),
  dica: $("dica"), dicaTempo: $("dica-tempo"), dicaEstado: $("dica-estado"), espera: $("espera"),
  esperaTexto: $("espera-texto"), esperaSub: $("espera-sub"), playCentral: $("play-central"),
  tAtual: $("t-atual"), tTotal: $("t-total"), analise: $("analise"), vel: $("btn-vel"), menuVel: $("menu-vel"),
  vol: $("vol"), titulo: $("titulo"), canal: $("canal-nome"), mini: $("miniatura"), aviso: $("aviso"),
  segmentos: $("segmentos"), contexto: $("contexto"), ctxContagem: $("ctx-contagem"),
  form: $("form-url"), url: $("campo-url"), carregar: $("btn-carregar"),
};

const st = {
  session: null, segments: [], durationMs: 0, acc: new PlayedAccumulator(),
  pauseReason: null, mode: "duo", released: [], backend: null, scrubbing: false, lastSent: 0, seq: 0,
};

// ---------------- back-ends ----------------

class FixtureBackend {
  // Simula a análise progressiva: S1 pronto rápido, os demais em sequência.
  // ?fixture=estatico congela no estado do contrato (S1,S2 READY; S3 PROCESSING; S4 QUEUED).
  constructor(onEvent) { this.on = onEvent; this.timers = []; this.events = []; }
  async load(url, mode) {
    const dur = 600000, n = 4, step = dur / n;
    const segments = Array.from({ length: n }, (_, i) => ({
      segment_id: `fixture-10min-r1-s${i}`, video_id: "fixture-10min", revision: 1, index: i,
      start_ms: i * step, end_ms: (i + 1) * step, status: "QUEUED", failure_reason: null, checksum: null, provenance: null,
    }));
    this.events = FIXTURE_EVENTS;
    const session = {
      contract: "1.0.0", mode,
      video: { video_id: "fixture-10min", source_url: url, title: "Vídeo sintético de 10 minutos (fixture)", channel: "Fixture · sem análise real",
        thumbnail_url: null, duration_ms: dur, media_url: "/react/api/media/fixture-10min", revision: 1 },
      segments, playback: { session_id: "sess-fixture-01", video_id: "fixture-10min", revision: 1, position_ms: 0,
        paused: true, rate: 1, played_ranges: [], pause_reason: null, sequence: 0 },
    };
    const estatico = qs.get("fixture") === "estatico";
    const ritmo = Number(qs.get("ritmo") || 1);
    if (estatico) {
      segments[0].status = segments[1].status = "READY";
      segments[2].status = "PROCESSING";
    } else {
      [3, 14, 26, 40].forEach((s, i) => {
        this.timers.push(setTimeout(() => this.set(i, "PROCESSING"), Math.max(0, (s - 3) * 1000 * ritmo)));
        this.timers.push(setTimeout(() => this.set(i, "READY"), s * 1000 * ritmo));
      });
    }
    this.session = session;
    return session;
  }
  set(i, status, reason = null) {
    const s = this.session.segments[i];
    s.status = status; s.failure_reason = reason;
    if (status === "READY") { s.checksum = "f".repeat(64); }
    this.on({ type: `segment.${status.toLowerCase()}`, payload: { ...s } });
  }
  prioritize() {}
  retry(segmentId) { const i = this.session.segments.findIndex(s => s.segment_id === segmentId); this.set(i, "QUEUED"); setTimeout(() => this.set(i, "READY"), 2000); }
  send() {}
  // Fixture: o gate roda no cliente para demonstrar o anti-spoiler; no backend real ele é do servidor.
  release(played, posMs) {
    return releasedEvents(this.events, this.session.segments, played, posMs);
  }
}

class ApiBackend {
  constructor(onEvent) { this.on = onEvent; this.ws = null; this.sessionId = null; this.queue = []; }
  async load(url, mode) {
    // ?sessao=<id> abre uma sessão salva (link novo: enviarLink cria a sessão e navega para cá).
    const sessao = qs.get("sessao");
    const r = await fetch(`/react/api/sessions/${encodeURIComponent(sessao)}`);
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    const session = await r.json();
    this.sessionId = session.playback.session_id;
    this.connect();
    return session;
  }
  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/react/ws/${this.sessionId}`);
    this.ws.onmessage = (m) => { try { this.on(JSON.parse(m.data)); } catch (e) { console.warn("[react] envelope inválido", e); } };
    this.ws.onopen = () => { this.queue.splice(0).forEach(x => this.ws.send(x)); };
    this.ws.onclose = () => { this.on({ type: "_ws.closed", payload: {} }); setTimeout(() => this.connect(), 1500); };
  }
  prioritize(posMs) { this.send("playback.seek", { position_ms: posMs }); }
  // D5: retry escopado pela sessão (o id do segmento se repete entre sessões do mesmo vídeo).
  async retry(segmentId) { await fetch(`/react/api/sessions/${encodeURIComponent(this.sessionId)}/segments/${encodeURIComponent(segmentId)}/retry`, { method: "POST" }); }
  send(type, payload) {
    const env = JSON.stringify({ v: "1.0.0", type, session_id: this.sessionId, sequence: ++st.seq, at: new Date().toISOString(), payload });
    if (this.ws && this.ws.readyState === 1) this.ws.send(env); else this.queue.push(env);
  }
  release() { return null; } // vem do servidor em context.released
}

const FIXTURE_EVENTS = [
  { event_id: "ev-0001", segment_id: "fixture-10min-r1-s0", kind: "speech", start_ms: 12000, end_ms: 15500, speaker: "apresentador", text: "Hoje a gente vai testar uma coisa estranha.", confidence: 0.93, basis: "heard" },
  { event_id: "ev-0002", segment_id: "fixture-10min-r1-s0", kind: "visual", start_ms: 60000, end_ms: 62000, speaker: null, text: "Um gato pula na mesa e derruba o copo.", confidence: 0.81, basis: "seen" },
  { event_id: "ev-0003", segment_id: "fixture-10min-r1-s1", kind: "speech", start_ms: 200000, end_ms: 204000, speaker: "apresentador", text: "Fala do minuto 3:20.", confidence: 0.9, basis: "heard" },
  { event_id: "ev-0004", segment_id: "fixture-10min-r1-s1", kind: "on_screen_text", start_ms: 275000, end_ms: 278000, speaker: null, text: "Legenda na tela: PARTE 2", confidence: 0.88, basis: "seen" },
  { event_id: "ev-canario", segment_id: "fixture-10min-r1-s3", kind: "speech", start_ms: 540000, end_ms: 544000, speaker: "apresentador", text: "CANARIO-SPOILER-8MIN: o gato era um robô.", confidence: 0.95, basis: "heard" },
];

// ---------------- carregamento ----------------

async function carregar(url) {
  aviso(null);
  el.carregar.disabled = true;
  try {
    if (st.backend?.timers) st.backend.timers.forEach(clearTimeout);
    st.backend = qs.has("fixture") ? new FixtureBackend(onEvent) : new ApiBackend(onEvent);
    const s = await st.backend.load(url, st.mode);
    st.released = []; st.pauseReason = null;
    aplicarSessao(s);
  } catch (e) {
    aviso(`Não consegui carregar o vídeo: ${e.message}`);
  } finally {
    el.carregar.disabled = false;
  }
}

// Sessão provisória: o backend responde ao link antes do download terminar (duração 1 ms, sem mídia).
const baixando = (s) => (s?.video?.duration_ms || 0) <= 1;

function aplicarSessao(s) {
  const trocouMidia = !st.session || st.session.video.media_url !== s.video.media_url || baixando(st.session) !== baixando(s);
  st.session = s; st.segments = s.segments; st.durationMs = baixando(s) ? 0 : s.video.duration_ms;
  st.acc = new PlayedAccumulator(s.playback.played_ranges);
  el.titulo.textContent = baixando(s) ? "Baixando o vídeo…" : s.video.title;
  el.canal.textContent = s.video.channel || "";
  if (s.video.thumbnail_url) { el.mini.src = s.video.thumbnail_url; el.mini.hidden = false; } else el.mini.hidden = true;
  document.title = `${baixando(s) ? "Baixando" : s.video.title} · Célia React`;
  el.player.classList.add("carregado");
  if (baixando(s)) {
    // Não aponta o <video> para uma mídia que ainda não existe (dava 404 e "não consegui abrir").
    el.video.removeAttribute("src"); el.video.load();
    el.espera.hidden = false; el.esperaTexto.textContent = "Baixando o vídeo…";
    el.esperaSub.textContent = "Ele abre aqui sozinho quando o download terminar.";
    el.playCentral.hidden = true;
  } else {
    if (trocouMidia) {
      el.video.src = s.video.media_url;
      if (s.playback.position_ms) el.video.currentTime = s.playback.position_ms / 1000;
    }
    el.video.playbackRate = s.playback.rate || 1;
    el.espera.hidden = true; el.playCentral.hidden = !el.video.paused;
    aviso(null);
  }
  el.barra.setAttribute("aria-valuemax", String(Math.round(st.durationMs / 1000)));
  renderTudo();
}

function onEvent(env) {
  const p = env.payload || {};
  if (env.type === "video.failed") {
    el.espera.hidden = true;
    aviso(`O download ou a preparação do vídeo falhou: ${p.reason || "motivo não informado"}`);
    return;
  }
  if (env.type?.startsWith("segment.")) {
    const seg = p.segment || p; // contrato: payload { segment: {...} }; fixture manda o segmento direto
    const i = st.segments.findIndex(s => s.segment_id === seg.segment_id);
    if (i >= 0) st.segments[i] = { ...st.segments[i], ...seg };
    else return;
    renderSegmentos(); renderBarra(); renderAnalise(); armarBorda();
    if (seg.status === "READY") talvezRetomar();
  } else if (env.type === "video.ready_to_play" && p.session) {
    // Estado autoritativo do servidor: ao (re)conectar e quando o download termina
    // (aí chegam título, canal, miniatura, duração real, mídia e os segmentos de verdade).
    aplicarSessao(p.session);
    talvezRetomar();
  } else if (env.type === "context.released") {
    const ids = new Set(st.released.map(e => e.event_id));
    for (const ev of p.events || []) if (!ids.has(ev.event_id)) st.released.push(ev);
    st.released.sort((a, b) => a.start_ms - b.start_ms || (a.event_id < b.event_id ? -1 : 1));
    renderContexto();
  } else if (env.type === "session.state" && p.playback) {
    st.acc.replace(p.playback.played_ranges); renderBarra();
  } else if (env.type === "react.command") {
    // Comando da Célia: só confirma depois de o player executar de verdade.
    if (p.action === "pause") { pausar("celia"); }
    else if (p.action === "play") { tocar(); }
    st.backend.send("react.command.ack", { command_id: p.command_id, action: p.action, executed: p.action === "pause" ? el.video.paused : !el.video.paused, position_ms: posMs() });
  } else if (env.type === "_ws.closed") {
    renderAnalise("Reconectando à análise…");
  }
}

// ---------------- reprodução e gate ----------------

const posMs = () => Math.round(el.video.currentTime * 1000);
const segAtual = (t = posMs()) => segmentAt(Math.min(t, st.durationMs), st.segments, st.durationMs);
const pronto = (t) => segAtual(t)?.status === "READY";

function tocar() {
  if (!st.session) return;
  if (!pronto(posMs())) { esperar(); return; }
  st.pauseReason = null;
  el.video.play().catch(() => {});
}

function pausar(reason) {
  st.pauseReason = reason;
  el.video.pause();
}

function esperar() {
  const s = segAtual();
  pausar("waiting_for_analysis");
  el.espera.hidden = false;
  el.esperaTexto.textContent = s?.status === "FAILED" ? "A análise deste trecho falhou" : "Preparando este trecho…";
  el.esperaSub.textContent = s ? `Parte ${s.index + 1} de ${st.segments.length} · ${fmt(s.start_ms)}–${fmt(s.end_ms)}` : "";
  st.backend.send("playback.waiting_for_analysis", { position_ms: posMs(), segment_id: s?.segment_id });
  if (s) st.backend.prioritize(posMs());
}

function talvezRetomar() {
  if (pronto(posMs())) el.espera.hidden = true;
  // Só retoma sozinho se quem pausou foi a espera pela análise; pausa do usuário nunca retoma.
  if (st.pauseReason === "waiting_for_analysis" && pronto(posMs())) {
    st.pauseReason = null;
    el.video.play().catch(() => {});
    st.backend.send("playback.resumed_after_analysis", { position_ms: posMs() });
  }
}

function seek(ms) {
  if (!st.session) return;
  ms = Math.max(0, Math.min(st.durationMs, Math.round(ms)));
  el.video.currentTime = ms / 1000;
  st.acc.seek(ms);
  st.backend.send("playback.seek", { position_ms: ms });
  if (!pronto(ms)) esperar();
  else el.espera.hidden = true;
  renderTempo(); renderBarra(); armarBorda();
}

// Gate. NÃO depende de requestAnimationFrame: rAF para com a aba oculta/minimizada
// (medido: o vídeo entrou no cinza com o painel escondido). Roda em quatro fontes:
// rAF (suavidade visível), evento timeupdate do vídeo, intervalo de 100 ms e um timer
// armado exatamente para a borda do próximo trecho não pronto.
function verificar() {
  if (!st.session || el.video.paused || st.scrubbing) return;
  const t = posMs();
  const s = segAtual(t);
  if (!s || s.status !== "READY") {
    const borda = s ? s.start_ms : t;
    st.acc.tick(borda);
    el.video.currentTime = borda / 1000;
    esperar();
  } else {
    st.acc.tick(t);
  }
  renderTempo(); renderBarra();
}

let bordaT;
function armarBorda() {
  clearTimeout(bordaT);
  if (!st.session || el.video.paused) return;
  const t = posMs();
  const prox = st.segments.find(s => s.end_ms > t && s.status !== "READY");
  if (!prox) return;
  const espera = Math.max(0, (prox.start_ms - t) / el.video.playbackRate);
  bordaT = setTimeout(() => { verificar(); armarBorda(); }, espera + 5);
}

function quadro() { verificar(); requestAnimationFrame(quadro); }
el.video.addEventListener("timeupdate", verificar);
setInterval(verificar, 100);

setInterval(() => {
  if (!st.session || el.video.paused) return;
  const pos = posMs();
  st.backend.send("playback.timeupdate", { position_ms: pos, rate: el.video.playbackRate });
  const local = st.backend.release(st.acc.ranges, pos);
  if (local) { st.released = local; renderContexto(); }
}, TICK_MS);

el.video.addEventListener("play", () => {
  if (!pronto(posMs())) { esperar(); return; }
  st.acc.play(posMs()); el.player.classList.add("tocando"); el.playCentral.hidden = true; mostrarControles();
  st.backend.send("playback.play", { position_ms: posMs() });
  armarBorda();
});
el.video.addEventListener("pause", () => {
  st.acc.pause(); el.player.classList.remove("tocando"); mostrarControles();
  if (!st.pauseReason) st.pauseReason = el.video.ended ? "ended" : "user";
  st.backend.send("playback.pause", { position_ms: posMs(), reason: st.pauseReason });
});
el.video.addEventListener("ratechange", () => {
  st.acc.setRate(el.video.playbackRate); el.vel.textContent = `${el.video.playbackRate}×`; armarBorda();
  st.backend.send("playback.ratechange", { rate: el.video.playbackRate });
});
el.video.addEventListener("volumechange", () => { el.player.classList.toggle("mudo", el.video.muted || el.video.volume === 0); el.vol.value = el.video.muted ? 0 : el.video.volume; });
el.video.addEventListener("loadedmetadata", () => { if (!st.durationMs) st.durationMs = Math.round(el.video.duration * 1000); renderTempo(); });
el.video.addEventListener("error", () => {
  // Sem src (sessão ainda baixando) o <video> também dispara "error"; isso não é falha.
  if (el.video.getAttribute("src")) aviso("O navegador não conseguiu abrir a mídia deste vídeo.");
});
el.video.addEventListener("ended", () => { st.pauseReason = "ended"; st.backend.send("playback.ended", { position_ms: posMs() }); });

// ---------------- render ----------------

function renderBarra() {
  if (!st.durationMs) return;
  const runs = timelineRuns(st.segments, st.acc.ranges, st.durationMs);
  const frag = document.createDocumentFragment();
  for (const r of runs) {
    const d = document.createElement("div");
    d.className = `faixa ${r.color}`;
    d.dataset.start = r.start_ms; d.dataset.end = r.end_ms;
    d.style.left = `${(r.start_ms / st.durationMs) * 100}%`;
    d.style.width = `${((r.end_ms - r.start_ms) / st.durationMs) * 100}%`;
    frag.appendChild(d);
  }
  for (const s of st.segments.slice(1)) {
    const m = document.createElement("div");
    m.className = "marco"; m.style.left = `${(s.start_ms / st.durationMs) * 100}%`;
    frag.appendChild(m);
  }
  el.trilho.replaceChildren(frag);
  el.pegador.style.left = `${(posMs() / st.durationMs) * 100}%`;
}

function renderTempo() {
  el.tAtual.textContent = fmt(posMs());
  el.tTotal.textContent = fmt(st.durationMs);
  el.barra.setAttribute("aria-valuenow", String(Math.round(posMs() / 1000)));
  el.barra.setAttribute("aria-valuetext", `${fmt(posMs())} de ${fmt(st.durationMs)}`);
}

const ROTULO = { QUEUED: "Na fila", PROCESSING: "Analisando", VALIDATING: "Validando", READY: "Pronto", FAILED: "Falhou", CANCELED: "Cancelado" };

function renderSegmentos() {
  el.segmentos.replaceChildren(...st.segments.map(s => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="sid">S${s.index + 1}</span><span class="faixa-t">${fmt(s.start_ms)} – ${fmt(s.end_ms)}</span><span class="estado ${s.status}">${ROTULO[s.status] || s.status}</span>`;
    if (s.status === "FAILED") {
      const b = document.createElement("button");
      b.textContent = "Falhou · tentar de novo"; b.className = "tentar";
      b.onclick = () => st.backend.retry(s.segment_id);
      li.lastChild.replaceWith(b);
    }
    li.title = s.failure_reason || (s.provenance ? `analisado por ${s.provenance.model}` : "");
    if (s.status === "READY" && s.provenance?.analyzer === "fixture") {
      // READY de fixture não tem análise de conteúdo: nunca deixar parecer real.
      li.querySelector(".estado").textContent = "Pronto · simulado";
      li.querySelector(".estado").classList.add("simulado");
    }
    return li;
  }));
}

function renderAnalise(msg) {
  const prontos = st.segments.filter(s => s.status === "READY").length;
  const ativo = st.segments.find(s => s.status === "PROCESSING" || s.status === "VALIDATING");
  el.analise.classList.toggle("trabalhando", !!ativo);
  el.analise.innerHTML = msg ? `<span class="ponto"></span>${msg}` :
    `<span class="ponto"></span>${prontos}/${st.segments.length} prontos${ativo ? ` · analisando S${ativo.index + 1}` : ""}`;
}

function renderContexto() {
  el.ctxContagem.textContent = st.released.length ? `(${st.released.length})` : "";
  if (!st.released.length) { el.contexto.innerHTML = '<li class="vazio-lista">Nada liberado ainda.</li>'; return; }
  el.contexto.replaceChildren(...st.released.map(ev => {
    const li = document.createElement("li");
    const q = document.createElement("span"); q.className = "quando"; q.textContent = fmt(ev.start_ms);
    const t = document.createElement("span"); t.textContent = ev.text;
    const b = document.createElement("span"); b.className = "base"; b.textContent = { heard: "ouvido", seen: "visto", inferred: "inferido" }[ev.basis] || "";
    li.append(q, t, b); li.dataset.eventId = ev.event_id;
    return li;
  }));
}

function renderTudo() { renderBarra(); renderTempo(); renderSegmentos(); renderAnalise(); renderContexto(); }

function aviso(msg) { el.aviso.hidden = !msg; el.aviso.textContent = msg || ""; }

// ---------------- barra: hover, clique, arrasto, teclado ----------------

const msDoPonteiro = (ev) => {
  const r = el.trilho.getBoundingClientRect();
  return Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * st.durationMs;
};
const ESTADO_COR = { red: "assistido", blue: "liberado", gray: "em análise" };

el.barra.addEventListener("pointermove", (ev) => {
  if (!st.durationMs) return;
  const ms = msDoPonteiro(ev);
  const runs = timelineRuns(st.segments, st.acc.ranges, st.durationMs);
  const cor = (runs.find(r => r.start_ms <= ms && ms < r.end_ms) || runs[runs.length - 1]).color;
  el.dica.hidden = false;
  el.dica.style.left = `${(ms / st.durationMs) * 100}%`;
  el.dicaTempo.textContent = fmt(ms);
  el.dicaEstado.textContent = ESTADO_COR[cor]; el.dicaEstado.className = cor;
  if (st.scrubbing) { el.pegador.style.left = `${(ms / st.durationMs) * 100}%`; el.tAtual.textContent = fmt(ms); }
});
el.barra.addEventListener("pointerleave", () => { if (!st.scrubbing) el.dica.hidden = true; });
el.barra.addEventListener("pointerdown", (ev) => {
  if (!st.durationMs) return;
  st.scrubbing = true; el.barra.classList.add("arrastando"); el.barra.setPointerCapture(ev.pointerId);
  st.voltarTocando = !el.video.paused;
  if (st.voltarTocando) pausar("scrub");
});
el.barra.addEventListener("pointerup", (ev) => {
  if (!st.scrubbing) return;
  st.scrubbing = false; el.barra.classList.remove("arrastando"); el.dica.hidden = true;
  seek(msDoPonteiro(ev));
  if (st.voltarTocando && pronto(posMs())) tocar();
});
el.barra.addEventListener("keydown", (ev) => {
  const k = { ArrowLeft: -5000, ArrowRight: 5000, PageDown: -10000, PageUp: 10000 }[ev.key];
  if (k) { ev.preventDefault(); ev.stopPropagation(); seek(posMs() + k); }
  else if (ev.key === "Home") { ev.preventDefault(); seek(0); }
  else if (ev.key === "End") { ev.preventDefault(); seek(st.durationMs); }
});

// ---------------- controles ----------------

const alternar = () => (el.video.paused ? tocar() : pausar("user"));
$("btn-play").onclick = alternar;
el.playCentral.onclick = tocar;
el.video.addEventListener("click", alternar);
el.video.addEventListener("dblclick", telaCheia);
$("btn-voltar").onclick = () => seek(posMs() - 10000);
$("btn-avancar").onclick = () => seek(posMs() + 10000);
$("btn-mudo").onclick = () => { el.video.muted = !el.video.muted; if (!el.video.muted && el.video.volume === 0) el.video.volume = 0.5; };
el.vol.oninput = () => { el.video.volume = Number(el.vol.value); el.video.muted = el.video.volume === 0; };
$("btn-tela").onclick = telaCheia;
function telaCheia() { document.fullscreenElement ? document.exitFullscreen() : el.player.requestFullscreen?.(); }

el.menuVel.replaceChildren(...RATES.map(r => {
  const b = document.createElement("button");
  b.setAttribute("role", "menuitemradio"); b.textContent = r === 1 ? "Normal" : `${r}×`; b.dataset.rate = r;
  b.onclick = () => { el.video.playbackRate = r; fecharMenu(); };
  return b;
}));
function fecharMenu() { el.menuVel.hidden = true; el.vel.setAttribute("aria-expanded", "false"); }
el.vel.onclick = () => {
  const abrir = el.menuVel.hidden;
  el.menuVel.hidden = !abrir; el.vel.setAttribute("aria-expanded", String(abrir));
  el.menuVel.querySelectorAll("button").forEach(b => b.setAttribute("aria-checked", String(Number(b.dataset.rate) === el.video.playbackRate)));
  if (abrir) el.menuVel.querySelector('[aria-checked="true"]')?.focus();
};
document.addEventListener("click", (ev) => { if (!ev.target.closest(".vel-wrap")) fecharMenu(); });

document.querySelectorAll(".modo button").forEach(b => b.onclick = () => {
  st.mode = b.dataset.modo;
  document.querySelectorAll(".modo button").forEach(x => x.setAttribute("aria-checked", String(x === b)));
  st.backend?.send("session.mode", { mode: st.mode });
});

document.addEventListener("keydown", (ev) => {
  const alvo = ev.target instanceof Element ? ev.target : document.body;
  if (alvo.closest("input, textarea, [role=menu]") || ev.ctrlKey || ev.metaKey || ev.altKey || !st.session) return;
  if (alvo === el.barra && ev.key.startsWith("Arrow")) return;
  const acoes = {
    " ": alternar, k: alternar, K: alternar,
    j: () => seek(posMs() - 10000), l: () => seek(posMs() + 10000),
    ArrowLeft: () => seek(posMs() - 5000), ArrowRight: () => seek(posMs() + 5000),
    ArrowUp: () => { el.video.volume = Math.min(1, el.video.volume + 0.05); },
    ArrowDown: () => { el.video.volume = Math.max(0, el.video.volume - 0.05); },
    m: () => $("btn-mudo").click(), f: telaCheia,
    ">": () => mudarVel(1), "<": () => mudarVel(-1),
  };
  if (/^[0-9]$/.test(ev.key)) { ev.preventDefault(); seek((Number(ev.key) / 10) * st.durationMs); return; }
  const a = acoes[ev.key];
  if (a) { ev.preventDefault(); a(); mostrarControles(); }
});
function mudarVel(d) { const i = RATES.indexOf(el.video.playbackRate); el.video.playbackRate = RATES[Math.max(0, Math.min(RATES.length - 1, i + d))]; }

let ocultarT;
function mostrarControles() {
  el.player.classList.remove("ocultar"); clearTimeout(ocultarT);
  if (!el.video.paused) ocultarT = setTimeout(() => el.player.classList.add("ocultar"), 2500);
}
el.player.addEventListener("pointermove", mostrarControles);

el.form.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const url = el.url.value.trim();
  if (qs.has("fixture")) { location.href = "./?fixture=1"; return; }
  if (!/^https:\/\/(www\.|m\.)?(youtube\.com|youtu\.be)\//i.test(url)) {
    avisoGeral("Use um link do YouTube (youtube.com ou youtu.be)."); return;
  }
  enviarLink(url);
});

function avisoGeral(msg) {
  const alvo = NO_PLAYER ? el.aviso : $("aviso-inicio");
  alvo.hidden = !msg; alvo.textContent = msg || "";
}

// Novo link: o backend responde na hora (download em segundo plano) e o player abre a sessão nova.
async function enviarLink(url) {
  el.carregar.disabled = true;
  try {
    const r = await fetch("/react/api/videos", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url, mode: st.mode }) });
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    const s = await r.json();
    location.href = `./?sessao=${encodeURIComponent(s.playback.session_id)}`;
  } catch (e) {
    avisoGeral(`Não consegui carregar o vídeo: ${e.message}`);
  } finally {
    el.carregar.disabled = false;
  }
}

requestAnimationFrame(quadro);
// ---------------- navegação: principal (miniaturas) ↔ player ----------------
// Principal: /react/ (ou ?fixture=lista). Player: ?sessao=<id> (ou ?fixture=1|estatico).
const FIXTURE_LISTA = qs.get("fixture") === "lista";
const NO_PLAYER = qs.has("sessao") || (qs.has("fixture") && !FIXTURE_LISTA);
const URL_INICIO = qs.has("fixture") ? "./?fixture=lista" : "./";
$("voltar").href = URL_INICIO;
document.querySelector(".marca").href = URL_INICIO;
$("inicio").hidden = NO_PLAYER;
$("tela-player").hidden = !NO_PLAYER;
$("voltar").hidden = !NO_PLAYER;

function capaCartao(item) {
  const capa = document.createElement("div");
  capa.className = "capa";
  const letra = () => {
    const d = document.createElement("div");
    d.className = "letra"; d.textContent = (item.video.title || "?").trim().charAt(0).toUpperCase();
    capa.prepend(d);
  };
  const fontes = [item.video.thumbnail_url, item.local_thumbnail_url].filter(Boolean);
  if (fontes.length) {
    const img = document.createElement("img");
    img.alt = ""; img.loading = "lazy"; img.decoding = "async";
    img.referrerPolicy = "no-referrer";
    let i = 0;
    img.onerror = () => { i += 1; if (i < fontes.length) img.src = fontes[i]; else { img.remove(); letra(); } };
    img.src = fontes[0];
    capa.append(img);
  } else letra();
  const dur = document.createElement("span");
  dur.className = "duracao"; dur.textContent = item.downloading ? "baixando" : fmt(item.video.duration_ms);
  capa.append(dur);
  if (item.played_ms > 0) {
    const fundo = document.createElement("div"); fundo.className = "visto-fundo";
    const visto = document.createElement("div"); visto.className = "visto";
    visto.style.width = `${Math.min(100, (item.played_ms / item.video.duration_ms) * 100)}%`;
    capa.append(fundo, visto);
  }
  return capa;
}

function cartao(item) {
  const li = document.createElement("li");
  li.className = "cartao";
  const a = document.createElement("a");
  a.href = item.href || `./?sessao=${encodeURIComponent(item.session_id)}`;
  a.setAttribute("aria-label", `${item.video.title}, ${fmt(item.video.duration_ms)}`);
  const dados = document.createElement("div");
  dados.className = "dados";
  const h = document.createElement("h2"); h.textContent = item.video.title;
  const c = document.createElement("span"); c.className = "canal-c"; c.textContent = item.video.channel || "";
  const e = document.createElement("span"); e.className = "estado-c";
  const tudo = item.ready === item.total;
  e.innerHTML = tudo ? '<span class="pronto">Análise completa</span>' : `${item.ready}/${item.total} partes analisadas`;
  if (item.failed) e.append(` · ${item.failed} com falha`);
  if (item.fixture) e.append(" · simulado");
  if (item.downloading) { h.textContent = "Baixando o vídeo…"; e.textContent = item.video.source_url || ""; }
  else if (item.media_ok === false) { e.textContent = "Mídia indisponível neste PC"; li.classList.add("indisponivel"); }
  dados.append(h, c, e);
  a.append(capaCartao(item), dados);
  li.append(a);
  return li;
}

async function carregarInicio() {
  let itens = [];
  try {
    if (FIXTURE_LISTA) {
      itens = [{ session_id: "fixture", href: "./?fixture=1", ready: 4, total: 4, played_ms: 180000, fixture: true,
        video: { title: "Vídeo sintético de 10 minutos (fixture)", channel: "Fixture · sem análise real", duration_ms: 600000, thumbnail_url: null } }];
    } else {
      const r = await fetch("/react/api/sessions");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      itens = await r.json();
    }
  } catch (e) {
    const av = $("aviso-inicio"); av.hidden = false; av.textContent = `Não consegui listar os vídeos: ${e.message}`;
  }
  $("grade").replaceChildren(...itens.map(cartao));
  $("vazio-inicio").hidden = itens.length > 0;
}

if (qs.has("fixture") && !FIXTURE_LISTA) el.url.value = "https://www.youtube.com/watch?v=FIXTURE0001";
if (!NO_PLAYER) carregarInicio();
else if (qs.has("sessao") || qs.has("fixture")) carregar(el.url.value || "");

// Espião para testes (somente leitura + atalhos de fixture).
window.reactPlayer = {
  get estado() { return { pos: posMs(), paused: el.video.paused, pauseReason: st.pauseReason, rate: el.video.playbackRate,
    played: st.acc.ranges.map(r => ({ ...r })), segments: st.segments.map(s => ({ index: s.index, status: s.status })),
    runs: st.durationMs ? timelineRuns(st.segments, st.acc.ranges, st.durationMs) : [], released: st.released.map(e => e.event_id),
    esperando: !el.espera.hidden } },
  seek, tocar, pausar: () => pausar("user"), carregar,
  fixture: { set: (i, status, reason) => st.backend?.set?.(i, status, reason) },
};
