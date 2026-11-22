// Regras do contrato React V2 1.0.0, iguais a tools/react_v2_contract_check.py.
// Funções puras: sem DOM, para teste de paridade com a referência em Python.

export const MAX_STEP_MS = 1500;

export function mergeRanges(ranges) {
  const out = [];
  for (const r of [...ranges].sort((a, b) => a.start_ms - b.start_ms)) {
    if (r.end_ms <= r.start_ms) continue;
    const last = out[out.length - 1];
    if (last && r.start_ms <= last.end_ms) last.end_ms = Math.max(last.end_ms, r.end_ms);
    else out.push({ start_ms: r.start_ms, end_ms: r.end_ms });
  }
  return out;
}

// Acumulador incremental: só reprodução real vira faixa vermelha.
export class PlayedAccumulator {
  constructor(ranges = []) { this.ranges = mergeRanges(ranges); this.prev = null; this.paused = true; this.rate = 1; }
  play(pos) { this.paused = false; this.prev = pos; }
  pause() { this.paused = true; }
  seek(pos) { this.prev = pos; }           // o salto nunca vira faixa
  setRate(rate) { this.rate = rate; }
  tick(pos) {
    const prev = this.prev;
    this.prev = pos;
    if (this.paused || prev === null) return false;
    const d = pos - prev;
    if (d <= 0 || d > MAX_STEP_MS * this.rate) return false;
    this.ranges = mergeRanges([...this.ranges, { start_ms: prev, end_ms: pos }]);
    return true;
  }
  replace(ranges) { this.ranges = mergeRanges(ranges); }
}

export function segmentAt(t, segments, durationMs) {
  for (const s of segments) {
    const last = s.end_ms === durationMs;
    if ((s.start_ms <= t && t < s.end_ms) || (last && t === s.end_ms)) return s;
  }
  return null;
}

export function colorAt(t, segments, played, durationMs) {
  if (played.some(r => r.start_ms <= t && t < r.end_ms)) return "red";
  const s = segmentAt(t, segments, durationMs);
  return s && s.status === "READY" ? "blue" : "gray";
}

export function timelineRuns(segments, played, durationMs) {
  const cuts = new Set([0, durationMs]);
  for (const s of segments) { cuts.add(s.start_ms); cuts.add(s.end_ms); }
  for (const r of played) { cuts.add(r.start_ms); cuts.add(r.end_ms); }
  const sorted = [...cuts].filter(c => c >= 0 && c <= durationMs).sort((a, b) => a - b);
  const runs = [];
  for (let i = 0; i < sorted.length - 1; i++) {
    const a = sorted[i], b = sorted[i + 1];
    const c = colorAt(a, segments, played, durationMs);
    const last = runs[runs.length - 1];
    if (last && last.color === c && last.end_ms === a) last.end_ms = b;
    else runs.push({ start_ms: a, end_ms: b, color: c });
  }
  return runs;
}

export function releasedEvents(events, segments, played, positionMs) {
  const ready = new Set(segments.filter(s => s.status === "READY").map(s => s.segment_id));
  return events.filter(ev => ready.has(ev.segment_id) && ev.end_ms <= positionMs &&
    played.some(r => r.start_ms <= ev.start_ms && ev.end_ms <= r.end_ms));
}

export function fmt(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}
