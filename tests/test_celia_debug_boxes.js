"use strict";
// Mapeador evento -> emoji das caixas de debug (parte pura, sem DOM).
const assert = require("node:assert");
const path = require("node:path");

const api = require(path.resolve(__dirname, "..", "frontend", "celia-debug-boxes.js"));
const { mapear, ms } = api;

assert.strictEqual(ms(842), "842 ms");
assert.strictEqual(ms(1840), "1,84 s");
assert.strictEqual(ms(undefined), "");

const ev = (node_id, status, metadata = {}, extra = {}) => ({
  sequence: 1, kind: "pulse", node_id, status, metadata, duration_ms: null, ...extra,
});

// ── visão: tela e câmera têm linhas próprias ──────────────────────────────────
let m = mapear(ev("vision.source", "on", { source: "screen", width: 1280, height: 720 }));
assert.strictEqual(m.b.emoji, "🖥️");
assert.match(m.b.texto, /tela ligada \(1280x720\)/);

m = mapear(ev("vision.source", "on", { source: "camera" }));
assert.strictEqual(m.b.emoji, "📷");
assert.match(m.b.texto, /câmera ligada/);

m = mapear(ev("vision.source", "off", { source: "camera", reason: "timeout" }));
assert.match(m.b.texto, /câmera desligada \(timeout\)/);
assert.ok(m.b.emoji.includes("⛔"));
m = mapear(ev("vision.source", "off", { source: "screen", reason: "signal" }));
assert.doesNotMatch(m.b.texto, /signal/, "motivo 'signal' é ruído, só mostra o excepcional");

m = mapear(ev("vision.analysis", "start", { source: "screen" }));
assert.match(m.a.texto, /olhando a tela/);
assert.match(m.b.texto, /analisando tela/);
m = mapear(ev("vision.analysis", "ok", { source: "camera" }, { duration_ms: 842 }));
assert.match(m.b.texto, /visão pronta 842 ms · câmera/);
m = mapear(ev("vision.analysis", "stale", { source: "camera" }));
assert.strictEqual(m.b.emoji, "🗑️");
m = mapear(ev("vision.analysis", "fail", { source: "screen", error_type: "ConnectError" }));
assert.match(m.b.texto, /visão falhou ConnectError/);

m = mapear(ev("vision.frame", "skipped", { source: "screen", reason: "budget", calls_window: 12, calls_max: 12, count: 7 }));
assert.match(m.b.texto, /visão no teto 12\/12 por min ×7/);
m = mapear(ev("vision.frame", "skipped", { source: "camera", reason: "source_off", count: 1 }));
assert.match(m.b.texto, /fonte desligada/);

// ── pensamento: caixa A ("Thinking") e B ──────────────────────────────────────
m = mapear(ev("cognitive.think", "start", { reason: "vision_screen_change" }));
assert.strictEqual(m.a.emoji, "🤔");
assert.match(m.a.texto, /pensando/);
m = mapear(ev("cognitive.think", "ok", { provider: "luna", should_speak: true }, { duration_ms: 1200 }));
assert.match(m.a.texto, /pensou 1,20 s · vai falar/);
m = mapear(ev("cognitive.wake", "coalesced", { count: 4 }));
assert.match(m.b.texto, /wakes juntados ×4/);
m = mapear(ev("cognitive.wake", "ok", { reason: "screen_change" }));
assert.match(m.b.texto, /pensamento acordado: screen_change/);

// ── roteador e turno (eventos que o servidor já emitia) ───────────────────────
m = mapear(ev("router.call", "ok", { role: "screen_vision", provider: "luna" }, { duration_ms: 910 }));
assert.match(m.b.texto, /screen_vision → luna 910 ms/);
m = mapear(ev("router.call", "fail", { role: "cognitive_loop" }, { duration_ms: 5100 }));
assert.strictEqual(m.b.emoji, "⚠️");

m = mapear({ kind: "trace_start", node_id: "browser.conversation", status: "running", metadata: {} });
assert.match(m.b.texto, /turno começou \(navegador\)/);
m = mapear({ kind: "span_start", node_id: "agent.basic_memory", status: "running", metadata: {} });
assert.match(m.a.texto, /gerando resposta/);
m = mapear({ kind: "span_end", node_id: "agent.basic_memory", status: "ok", metadata: {}, duration_ms: 1700 });
assert.match(m.a.texto, /resposta pronta 1,70 s/);
m = mapear({ kind: "trace_end", node_id: "browser.conversation", status: "ok", metadata: {}, duration_ms: 4000 });
assert.match(m.a.texto, /parada/);
m = mapear({ kind: "tts_generated", node_id: "service.omnivoice", status: "ok", metadata: {}, duration_ms: 1809 });
assert.match(m.b.texto, /TTS 1,81 s/);
m = mapear({ kind: "tool", node_id: "mcp.registry", status: "ok", metadata: { tool_name: "get_current_time", tool_status: "ok" }, duration_ms: 40 });
assert.match(m.b.texto, /get_current_time ok 40 ms/);

// ── ruído não entra; erro entra ───────────────────────────────────────────────
assert.strictEqual(mapear({ kind: "history_write", node_id: "memory.history", status: "ok", metadata: {} }), null);
assert.strictEqual(mapear(null), null);
assert.strictEqual(mapear({}), null);
m = mapear({ kind: "error", node_id: "service.tts", status: "error", metadata: { error_type: "TimeoutError" } });
assert.match(m.b.texto, /service.tts erro TimeoutError/);

// Chaves de agrupamento distinguem as fontes (tela ≠ câmera nunca se fundem)
const a = mapear(ev("vision.analysis", "ok", { source: "screen" }));
const b = mapear(ev("vision.analysis", "ok", { source: "camera" }));
assert.notStrictEqual(a.b.chave, b.b.chave);

// Nada de texto de conversa: o mapeador só lê campos estruturais
const semConteudo = mapear(ev("vision.analysis", "ok", { source: "screen", summary: "SEGREDO", prompt: "SEGREDO" }));
assert.ok(!JSON.stringify(semConteudo).includes("SEGREDO"));

console.log("test_celia_debug_boxes: ok");
