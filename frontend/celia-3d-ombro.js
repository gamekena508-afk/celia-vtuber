// celia-3d-ombro.js — Corrective Smooth (delta mush) do ombro, ao vivo no navegador. Kurt, 2026-10-06.
// Por que: o Pai viu pontas no contorno do ombro quando o braco desce. A regua p20_pontas.py mediu 34 vertices
// saltando > 6 mm (T-pose: 0). Suavizar os PESOS nao resolve (15 passadas: 26; 30 passadas: 65). No Blender quem
// resolvia era o modificador Corrective Smooth (V3_OMBRO_SUAVE), que NAO vai no VRM. Aqui ele roda por quadro:
//   1. pele do ombro deformada na CPU (mesma conta do skinning do three);
//   2. alisa a deformacao (Laplaciano, borda fixa) e devolve o relevo do repouso (o trico) girado pela pele;
//   3. grava no atributo de posicao a posicao de bind que, skinada pela GPU, cai exatamente no resultado.
// Em T-pose devolve o repouso (identidade). So mexe na faixa do ombro do corpo (V3_corpo_normal).
import * as THREE from "three";

const ZONA = { xMin: 0.06, xMax: 0.40, yMin: 1.10, yMax: 1.46 }; // espaco do bind (glTF): ombro ~1.27, quadril 0.876

export class OmbroSuave {
  // raizes: ossos dos ombros (raw). So entra na zona quem tem peso em algum osso abaixo deles; tronco rigido fica de borda.
  constructor(malha, { passes = 10, lambda = 0.5, raizes = null, yMin = ZONA.yMin } = {}) {
    this.m = malha; this.K = passes; this.lam = lambda; this.ativo = true;
    const g = malha.geometry, pos = g.attributes.position, N = pos.count;
    this.pos = pos; this.orig = Float32Array.from(pos.array);
    // pesos/indices em Float32/Int32 (getComponent desnormaliza, mas e lento no laco do quadro)
    const si = g.attributes.skinIndex, sw = g.attributes.skinWeight;
    this.W = new Float32Array(N * 4); this.J = new Int32Array(N * 4);
    for (let i = 0; i < N; i++) for (let c = 0; c < 4; c++) { this.W[4 * i + c] = sw.getComponent(i, c); this.J[4 * i + c] = si.getComponent(i, c); }
    // solda por posicao: costura de UV nao pode abrir
    const chave = new Map(), grupo = new Int32Array(N); let G = 0;
    for (let i = 0; i < N; i++) {
      const k = Math.round(this.orig[3 * i] * 1e5) + "," + Math.round(this.orig[3 * i + 1] * 1e5) + "," + Math.round(this.orig[3 * i + 2] * 1e5);
      let v = chave.get(k); if (v === undefined) { v = G++; chave.set(k, v); } grupo[i] = v;
    }
    const rep = new Int32Array(G).fill(-1); for (let i = 0; i < N; i++) if (rep[grupo[i]] < 0) rep[grupo[i]] = i;
    const doBraco = new Uint8Array(malha.skeleton.bones.length);
    if (raizes) malha.skeleton.bones.forEach((b, j) => { for (let o = b; o; o = o.parent) if (raizes.includes(o)) { doBraco[j] = 1; break; } });
    const pesaBraco = (i) => { if (!raizes) return true; for (let c = 0; c < 4; c++) if (this.W[4 * i + c] > 0 && doBraco[this.J[4 * i + c]]) return true; return false; };
    const naZona = new Uint8Array(G);
    for (let i = 0; i < N; i++) { const q = grupo[i]; if (naZona[q]) continue; const x = Math.abs(this.orig[3 * i]), y = this.orig[3 * i + 1];
      if (x > ZONA.xMin && x < ZONA.xMax && y > yMin && y < ZONA.yMax && pesaBraco(i)) naZona[q] = 1; }
    this.ossosBraco = doBraco.reduce((a, b) => a + b, 0);
    const viz = new Map(); const add = (a, b) => { if (!naZona[a]) return; let s = viz.get(a); if (!s) viz.set(a, s = new Set()); s.add(b); };
    const idx = g.index.array;
    for (let t = 0; t < idx.length; t += 3) { const a = grupo[idx[t]], b = grupo[idx[t + 1]], c = grupo[idx[t + 2]];
      add(a, b); add(a, c); add(b, a); add(b, c); add(c, a); add(c, b); }
    this.zona = Int32Array.from([...viz.keys()]);
    // CSR dos vizinhos (indices em "precisa")
    const precisaSet = new Set(this.zona); for (const s of viz.values()) for (const u of s) precisaSet.add(u);
    this.precisa = Int32Array.from(precisaSet); const ondeP = new Map(); this.precisa.forEach((q, k) => ondeP.set(q, k));
    this.zonaP = Int32Array.from(this.zona, (q) => ondeP.get(q));
    const ini = new Int32Array(this.zona.length + 1), lista = [];
    this.zona.forEach((q, k) => { for (const u of viz.get(q)) lista.push(ondeP.get(u)); ini[k + 1] = lista.length; });
    this.vIni = ini; this.vLista = Int32Array.from(lista);
    this.repP = Int32Array.from(this.precisa, (q) => rep[q]);
    // vertices reais a regravar (todas as copias soldadas da zona)
    const zonaSet = new Set(this.zona), verts = [], vertZ = [];
    const zonaK = new Map(); this.zona.forEach((q, k) => zonaK.set(q, k));
    for (let i = 0; i < N; i++) if (zonaSet.has(grupo[i])) { verts.push(i); vertZ.push(zonaK.get(grupo[i])); }
    this.verts = Int32Array.from(verts); this.vertZ = Int32Array.from(vertZ);
    // copia com a mesma pele do representante herda o resultado dele (quase todas: costura de UV)
    this.mesma = Uint8Array.from(verts, (i, v) => { const r = this.repP[this.zonaP[vertZ[v]]];
      for (let c = 0; c < 4; c++) if (this.W[4 * i + c] !== this.W[4 * r + c] || (this.W[4 * i + c] && this.J[4 * i + c] !== this.J[4 * r + c])) return 0; return 1; });
    this.vInv = Float32Array.from(this.zona, (q, k) => 1 / ((this.vIni[k + 1] - this.vIni[k]) || 1));
    // relevo do repouso: d0 = repouso - alisado(repouso)
    const P = this.precisa.length, R = new Float32Array(P * 3);
    for (let k = 0; k < P; k++) { const i = this.repP[k]; R[3 * k] = this.orig[3 * i]; R[3 * k + 1] = this.orig[3 * i + 1]; R[3 * k + 2] = this.orig[3 * i + 2]; }
    const S0 = this._alisar(R);
    this.d0 = new Float32Array(this.zona.length * 3);
    for (let k = 0; k < this.zona.length; k++) { const p = this.zonaP[k]; for (let c = 0; c < 3; c++) this.d0[3 * k + c] = R[3 * p + c] - S0[3 * p + c]; }
    this.D = new Float32Array(P * 3); this.Mrep = new Float32Array(P * 12); this.M = new Float32Array(12);
    this.ms = 0; this.verticesZona = this.verts.length;
  }

  _alisar(Pin) {
    // buffers reaproveitados entre quadros (sem lixo para o GC no laco)
    if (!this._bA || this._bA.length !== Pin.length) { this._bA = new Float32Array(Pin.length); this._bB = new Float32Array(Pin.length); }
    // a borda (fora da zona) e igual nos dois buffers e nunca muda; a zona e toda reescrita a cada passada
    let A = this._bA, B = this._bB; A.set(Pin); B.set(Pin);
    for (let it = 0; it < this.K; it++) {
      for (let k = 0; k < this.zona.length; k++) {
        const p = this.zonaP[k]; let sx = 0, sy = 0, sz = 0; const a = this.vIni[k], b = this.vIni[k + 1];
        for (let j = a; j < b; j++) { const u = this.vLista[j]; sx += A[3 * u]; sy += A[3 * u + 1]; sz += A[3 * u + 2]; }
        const n = this.vInv[k], l = this.lam;
        B[3 * p] = A[3 * p] + l * (sx * n - A[3 * p]); B[3 * p + 1] = A[3 * p + 1] + l * (sy * n - A[3 * p + 1]); B[3 * p + 2] = A[3 * p + 2] + l * (sz * n - A[3 * p + 2]);
      }
      const t = A; A = B; B = t;
    }
    return A;
  }

  // por quadro, uma vez por osso: Binv * Bone_j * Bind (12 floats). Como a conta e linear nos pesos,
  // a matriz do vertice vira so a soma ponderada destas (medido: por vertice custava 12-22 ms).
  _ossos() {
    const bm = this.m.skeleton.boneMatrices, nb = bm.length / 16;
    if (!this.BB || this.BB.length !== nb * 12) this.BB = new Float32Array(nb * 12);
    const t = this._t || (this._t = new THREE.Matrix4()), e = t.elements;
    for (let j = 0; j < nb; j++) {
      t.fromArray(bm, j * 16).premultiply(this.m.bindMatrixInverse).multiply(this.m.bindMatrix);
      const o = 12 * j; this.BB[o] = e[0]; this.BB[o + 1] = e[1]; this.BB[o + 2] = e[2]; this.BB[o + 3] = e[4]; this.BB[o + 4] = e[5]; this.BB[o + 5] = e[6];
      this.BB[o + 6] = e[8]; this.BB[o + 7] = e[9]; this.BB[o + 8] = e[10]; this.BB[o + 9] = e[12]; this.BB[o + 10] = e[13]; this.BB[o + 11] = e[14];
    }
  }

  // matriz afim de skinning do vertice i: M = soma w_j * (Binv * Bone_j * Bind)  (12 floats, colunas 0..2 + translacao)
  _matriz(i, out, o) {
    const BB = this.BB, W = this.W, J = this.J;
    for (let e = 0; e < 12; e++) out[o + e] = 0;
    for (let c = 0; c < 4; c++) { const w = W[4 * i + c]; if (!w) continue; const j = 12 * J[4 * i + c];
      for (let e = 0; e < 12; e++) out[o + e] += w * BB[j + e]; }
  }

  atualizar() {
    if (!this.ativo) return;
    const t0 = performance.now(), P = this.precisa.length, D = this.D, Mr = this.Mrep, o = this.orig;
    this.m.skeleton.update(); this._ossos();
    for (let k = 0; k < P; k++) {
      const i = this.repP[k], b = 12 * k; this._matriz(i, Mr, b);
      const x = o[3 * i], y = o[3 * i + 1], z = o[3 * i + 2];
      D[3 * k] = Mr[b] * x + Mr[b + 3] * y + Mr[b + 6] * z + Mr[b + 9];
      D[3 * k + 1] = Mr[b + 1] * x + Mr[b + 4] * y + Mr[b + 7] * z + Mr[b + 10];
      D[3 * k + 2] = Mr[b + 2] * x + Mr[b + 5] * y + Mr[b + 8] * z + Mr[b + 11];
    }
    const SD = this._alisar(D), arr = this.pos.array, M = this.M;
    for (let v = 0; v < this.verts.length; v++) {
      const i = this.verts[v], k = this.vertZ[v], p = this.zonaP[k], b = 12 * p;
      // alvo final: alisado + relevo do repouso girado pela pele do proprio vertice
      const dx = this.d0[3 * k], dy = this.d0[3 * k + 1], dz = this.d0[3 * k + 2];
      const fx = SD[3 * p] + Mr[b] * dx + Mr[b + 3] * dy + Mr[b + 6] * dz;
      const fy = SD[3 * p + 1] + Mr[b + 1] * dx + Mr[b + 4] * dy + Mr[b + 7] * dz;
      const fz = SD[3 * p + 2] + Mr[b + 2] * dx + Mr[b + 5] * dy + Mr[b + 8] * dz;
      // posicao de bind que a GPU vai skinar ate o alvo: p' = M_i^-1 (F - t)
      let X = Mr, m = b; if (!this.mesma[v]) { this._matriz(i, M, 0); X = M; m = 0; }
      const a = X[m], bb = X[m + 3], c = X[m + 6], d = X[m + 1], e = X[m + 4], f = X[m + 7], g = X[m + 2], h = X[m + 5], ii = X[m + 8];
      const qx = fx - X[m + 9], qy = fy - X[m + 10], qz = fz - X[m + 11];
      const A = e * ii - f * h, B = -(d * ii - f * g), C = d * h - e * g, det = a * A + bb * B + c * C;
      if (Math.abs(det) < 1e-9) { arr[3 * i] = o[3 * i]; arr[3 * i + 1] = o[3 * i + 1]; arr[3 * i + 2] = o[3 * i + 2]; continue; }
      const inv = 1 / det;
      arr[3 * i] = (A * qx + (-(bb * ii - c * h)) * qy + (bb * f - c * e) * qz) * inv;
      arr[3 * i + 1] = (B * qx + (a * ii - c * g) * qy + (-(a * f - c * d)) * qz) * inv;
      arr[3 * i + 2] = (C * qx + (-(a * h - bb * g)) * qy + (a * e - bb * d) * qz) * inv;
    }
    this.pos.needsUpdate = true;
    this.ms = performance.now() - t0;
  }

  desligar() { this.ativo = false; this.pos.array.set(this.orig); this.pos.needsUpdate = true; }
}
