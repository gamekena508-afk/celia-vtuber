// celia-3d-maos.js — pesos novos para as maos da Celia 3D (dedos que dobram sem rasgar). Kurt, 2026-10-06.
// Por que: a mao TEM dedos separados na malha, mas os pesos do gerador estao trocados — curvar 0.2 ja rasga os dedos
// (web/p56_mao.py). Aqui, na carga, cada vertice da mao vai para o osso do PROPRIO dedo (cadeia de falanges mais
// proxima), misturando suave nas juntas e na base (com a palma). O pulso guarda a parte do antebraco. O .vrm nao muda.
import * as THREE from "three";

const DEDOS = ["Index", "Middle", "Ring", "Little"], FAL = ["Proximal", "Intermediate", "Distal"], POL = ["Metacarpal", "Proximal", "Distal"];
const JUNTA = 0.006;   // m: meia-faixa de mistura entre falanges vizinhas
const NO = 0.01;       // m: meia-faixa de mistura palma <-> falange proximal (os nos dos dedos)

function segDist(p, a, b) { // distancia ao segmento e parametro t (0..1, sem clamp para saber se esta antes/depois)
  const ab = b.clone().sub(a), t = p.clone().sub(a).dot(ab) / ab.lengthSq();
  return [p.distanceTo(a.clone().addScaledVector(ab, Math.max(0, Math.min(1, t)))), t, ab.length()];
}

export function repesarMaos(malha, humanoid) {
  const t0 = performance.now(), g = malha.geometry;
  const pos = g.attributes.position, si = g.attributes.skinIndex, sw = g.attributes.skinWeight, N = pos.count;
  const stats = { ok: true, vertices: 0, ossosAdicionados: [] };
  // Os ossos do dedo medio (DEF-MiddleFinger1..3) ficaram FORA do esqueleto da malha na exportacao (sem peso; p57):
  // os vertices do medio estavam nos dedos vizinhos. Entram aqui, com o bind da T-pose (o construtor roda em repouso).
  {
    const sk0 = malha.skeleton, faltam = [];
    for (const lado of ["left", "right"]) for (const d of [...DEDOS, "Thumb"]) for (const f of (d === "Thumb" ? POL : FAL)) {
      const n = humanoid.getRawBoneNode(lado + d + f); if (n && !sk0.bones.includes(n) && !faltam.includes(n)) faltam.push(n);
    }
    if (faltam.length) {
      malha.parent.updateWorldMatrix(true, true); for (const b of faltam) b.updateWorldMatrix(true, false);
      const novo = new THREE.Skeleton([...sk0.bones, ...faltam], [...sk0.boneInverses, ...faltam.map((b) => b.matrixWorld.clone().invert())]);
      malha.bind(novo, malha.bindMatrix); stats.ossosAdicionados = faltam.map((b) => b.name);
    }
  }
  const sk = malha.skeleton;
  stats.reencaixe = reencaixarDedos(malha, humanoid);
  const idxDe = (nome) => { const n = humanoid.getRawBoneNode(nome); return n ? sk.bones.indexOf(n) : -1; };
  const bindPos = (j) => new THREE.Vector3().setFromMatrixPosition(sk.boneInverses[j].clone().invert());
  for (const lado of ["left", "right"]) {
    const mao = idxDe(lado + "Hand"); if (mao < 0) { stats.ok = false; continue; }
    // cadeias: [indices dos 3 ossos], [4 pontos: inicio de cada falange + ponta extrapolada]
    const cadeias = [];
    for (const d of [...DEDOS, "Thumb"]) {
      const nomes = (d === "Thumb" ? POL : FAL).map((f) => lado + d + f), js = nomes.map(idxDe);
      if (js.some((j) => j < 0)) continue;
      const pts = js.map(bindPos); pts.push(pts[2].clone().add(pts[2].clone().sub(pts[1]).multiplyScalar(0.9)));
      cadeias.push({ dedo: d, js, pts });
    }
    const daMao = new Set([mao, ...cadeias.flatMap((c) => c.js)]);
    const v = new THREE.Vector3();
    for (let i = 0; i < N; i++) {
      let s = 0, outro = -1, wOutro = 0;
      for (let c = 0; c < 4; c++) { const j = si.getComponent(i, c), w = sw.getComponent(i, c); if (daMao.has(j)) s += w; else if (w > wOutro) { wOutro = w; outro = j; } }
      if (s < 0.5) continue;
      v.fromBufferAttribute(pos, i).applyMatrix4(malha.bindMatrix);
      // dedo dono: cadeia mais proxima
      let melhor = null, dm = Infinity;
      for (const c of cadeias) for (let k = 0; k < 3; k++) { const [d] = segDist(v, c.pts[k], c.pts[k + 1]); if (d < dm) { dm = d; melhor = c; } }
      // posicao ao longo da cadeia (m), 0 no no do dedo
      const L = [0]; for (let k = 0; k < 3; k++) L.push(L[k] + melhor.pts[k].distanceTo(melhor.pts[k + 1]));
      let ao = Infinity, al = 0;
      for (let k = 0; k < 3; k++) { const [d, t, len] = segDist(v, melhor.pts[k], melhor.pts[k + 1]); if (d < ao) { ao = d; al = L[k] + t * len; } }
      const pesos = new Map(), soma = (j, w) => pesos.set(j, (pesos.get(j) || 0) + w);
      const base = melhor.dedo === "Thumb" ? 0.4 : 0; // polegar: o metacarpo leva junto um pouco da palma
      if (al <= -NO) soma(mao, 1);
      else if (al < NO) { const u = (al + NO) / (2 * NO); soma(mao, 1 - u); soma(melhor.js[0], u); }
      else {
        let k = 0; while (k < 2 && al > L[k + 1]) k++;
        const ate = L[k + 1], de = L[k];
        // faixa de mistura [junta - JUNTA, junta + JUNTA]: 50/50 exatamente na junta
        if (k < 2 && al > ate - JUNTA) { const u = (al - (ate - JUNTA)) / (2 * JUNTA); soma(melhor.js[k], 1 - u); soma(melhor.js[k + 1], u); }
        else if (k > 0 && al < de + JUNTA) { const u = (al - (de - JUNTA)) / (2 * JUNTA); soma(melhor.js[k - 1], 1 - u); soma(melhor.js[k], u); }
        else soma(melhor.js[k], 1);
      }
      if (base && pesos.has(melhor.js[0])) { const w = pesos.get(melhor.js[0]); pesos.set(melhor.js[0], w * (1 - base)); soma(mao, w * base); }
      // a parte que nao era da mao (pulso -> antebraco) fica com o dono antigo
      const lista = [...pesos.entries()].map(([j, w]) => [j, w * s]).sort((a, b) => b[1] - a[1]).slice(0, outro >= 0 ? 3 : 4);
      if (outro >= 0) lista.push([outro, 1 - s]);
      const tot = lista.reduce((a, [, w]) => a + w, 0);
      for (let c = 0; c < 4; c++) { const e = lista[c]; si.setComponent(i, c, e ? e[0] : 0); sw.setComponent(i, c, e ? e[1] / tot : 0); }
      stats.vertices++;
    }
  }
  si.needsUpdate = true; sw.needsUpdate = true; stats.ms = Math.round(performance.now() - t0);
  return stats;
}

// Os ossos dos dedos do gerador ficam ~6 cm ACIMA da mao desenhada (p59/p60: juntas em y 1.26, mao em 1.18-1.23):
// girar um osso fora do dedo arrasta a malha torta com qualquer peso. Aqui cada cadeia vai para dentro do proprio dedo:
// (1) desloca as cadeias pelo desvio medio mao-ossos, perpendicular ao eixo da mao; (2) separa os vertices de cada dedo
// pela cadeia mais proxima; (3) eixo do dedo por PCA; (4) juntas sobre esse eixo, mesmas proporcoes das falanges.
// So posicoes mudam (rotacoes de repouso ficam): o bind dos ossos movidos e refeito na T-pose do carregamento.
function reencaixarDedos(malha, humanoid) {
  const sk = malha.skeleton, g = malha.geometry, pos = g.attributes.position, si = g.attributes.skinIndex, sw = g.attributes.skinWeight;
  malha.parent.updateWorldMatrix(true, true);
  const mundo = (b) => new THREE.Vector3().setFromMatrixPosition(b.matrixWorld), saida = {};
  for (const lado of ["left", "right"]) {
    const pulso = humanoid.getRawBoneNode(lado + "Hand"); if (!pulso) continue;
    const cadeias = [];
    for (const d of [...DEDOS, "Thumb"]) {
      const ossos = (d === "Thumb" ? POL : FAL).map((f) => humanoid.getRawBoneNode(lado + d + f));
      if (ossos.some((o) => !o)) continue;
      const J = ossos.map(mundo); J.push(J[2].clone().add(J[2].clone().sub(J[1]).multiplyScalar(0.9)));
      cadeias.push({ d, ossos, J });
    }
    // vertices da mao (peso em qualquer osso abaixo do pulso)
    const daMao = new Set(); sk.bones.forEach((b, j) => { for (let o = b; o; o = o.parent) if (o === pulso) { daMao.add(j); break; } });
    const V = [];
    for (let i = 0; i < pos.count; i++) { let s = 0; for (let c = 0; c < 4; c++) if (daMao.has(si.getComponent(i, c))) s += sw.getComponent(i, c);
      if (s >= 0.5) V.push(new THREE.Vector3().fromBufferAttribute(pos, i).applyMatrix4(malha.bindMatrix)); }
    if (V.length < 50 || !cadeias.length) continue;
    // (1) desvio medio, sem a componente ao longo da mao
    const eixoMao = mundo(cadeias.find((c) => c.d === "Middle").ossos[0]).sub(mundo(pulso)).normalize();
    const cm = V.reduce((a, v) => a.add(v), new THREE.Vector3()).multiplyScalar(1 / V.length);
    const todas = cadeias.flatMap((c) => c.J.slice(0, 3)), co = todas.reduce((a, v) => a.add(v), new THREE.Vector3()).multiplyScalar(1 / todas.length);
    const desvio = cm.clone().sub(co); desvio.addScaledVector(eixoMao, -desvio.dot(eixoMao));
    for (const c of cadeias) c.Jt = c.J.map((j) => j.clone().add(desvio));
    // (2) vertices de cada dedo
    const membros = new Map(cadeias.map((c) => [c, []]));
    for (const v of V) {
      let melhor = null, dm = Infinity, alM = 0;
      for (const c of cadeias) {
        let L = 0;
        for (let k = 0; k < 3; k++) { const [d, t, len] = segDist(v, c.Jt[k], c.Jt[k + 1]); if (d < dm) { dm = d; melhor = c; alM = L + t * len; } L += len; }
      }
      if (alM > -0.004) membros.get(melhor).push(v);
    }
    saida[lado] = { desvio_mm: desvio.toArray().map((x) => Math.round(x * 1000)), dedos: {} };
    // (3)+(4) eixo por PCA e juntas novas
    for (const c of cadeias) {
      const M = membros.get(c); if (M.length < 12) { saida[lado].dedos[c.d] = { vertices: M.length, pulado: true }; continue; }
      const ctr = M.reduce((a, v) => a.add(v), new THREE.Vector3()).multiplyScalar(1 / M.length);
      let eixo = c.J[3].clone().sub(c.J[0]).normalize();
      for (let it = 0; it < 30; it++) { const n = new THREE.Vector3(); for (const v of M) { const q = v.clone().sub(ctr); n.addScaledVector(q, q.dot(eixo)); } if (n.lengthSq() < 1e-12) break; eixo = n.normalize(); }
      if (eixo.dot(c.J[3].clone().sub(c.J[0])) < 0) eixo.negate();
      let smin = Infinity, smax = -Infinity; for (const v of M) { const t = v.clone().sub(ctr).dot(eixo); smin = Math.min(smin, t); smax = Math.max(smax, t); }
      const ls = [0, 1, 2].map((k) => c.J[k].distanceTo(c.J[k + 1])), tot = ls[0] + ls[1] + ls[2];
      const s0 = smin + 0.08 * (smax - smin), alcance = smax - s0;
      const novos = [s0, s0 + alcance * ls[0] / tot, s0 + alcance * (ls[0] + ls[1]) / tot].map((t) => ctr.clone().addScaledVector(eixo, t));
      c.ossos.forEach((o, k) => {
        o.parent.updateWorldMatrix(true, false);
        o.position.copy(novos[k].clone().applyMatrix4(o.parent.matrixWorld.clone().invert())); o.updateWorldMatrix(false, true);
      });
      saida[lado].dedos[c.d] = { vertices: M.length, moveu_mm: Math.round(novos[0].distanceTo(c.J[0]) * 1000) };
    }
  }
  // bind refeito para todos os ossos (so os dedos mudaram; os outros dao a mesma inversa)
  malha.parent.updateWorldMatrix(true, true);
  sk.bones.forEach((b, j) => { if (/Finger|Thumb/.test(b.name)) sk.boneInverses[j].copy(b.matrixWorld).invert(); });
  return saida;
}
