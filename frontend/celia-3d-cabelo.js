// celia-3d-cabelo.js — tira os pontinhos brancos do cabelo da Celia 3D. Kurt, 2026-10-06.
// Por que: o mapa emissivo do HEAD_SHELL_FBX (cabelo + orelhas + tiara, 2048^2) e um atlas assado em ilhas; o bake
// pegou o tufo branco da orelha em ~90 triangulos do meio das mechas (web/p52_onde_branco.py) -> pontinhos brancos.
// Conserto por ILHA de UV: ilha majoritariamente azul-marinho (cabelo) troca texel branco/cinza pelo azul medio da
// propria ilha. Ilha de tiara ou pele nao e azul -> intocada. Triangulos da altura das orelhas (bind y > ORELHA_Y) sao
// protegidos: a orelha tem lado de fora azul e o tufo/cinza de dentro tem que ficar (p53, controle 'orelhas').
// Roda uma vez no carregamento, numa copia da textura; o .vrm nao muda.
import * as THREE from "three";

const ORELHA_Y = 1.48; // os pontinhos ficam em y 1.0-1.4; tufos das orelhas em 1.5-1.6 (p52)

export function limparCabelo(vrm, { malha = "HEAD_SHELL_FBX", fracaoAzul = 0.5 } = {}) {
  let m = null; vrm.scene.traverse((o) => { if (o.isMesh && o.name === malha) m = o; });
  const velho = m && m.material && m.material.emissiveMap;
  if (!velho || !velho.image) return { ok: false, motivo: "sem mapa emissivo" };
  const t0 = performance.now(), W = velho.image.width, H = velho.image.height, flip = velho.flipY, N = W * H;
  const cv = document.createElement("canvas"); cv.width = W; cv.height = H;
  const g = cv.getContext("2d", { willReadFrequently: true }); g.drawImage(velho.image, 0, 0);
  const img = g.getImageData(0, 0, W, H), D = img.data; const tempos = { leitura: Math.round(performance.now() - t0) };
  // mascara em array tipado (1 = coberto por triangulo, 2 = protegido/orelha). O canvas 2D levava 20,9 s para preencher
  // 120 mil triangulos num caminho so (medido); rasterizar a mao leva milissegundos.
  const uv = m.geometry.attributes.uv, pos = m.geometry.attributes.position, idx = m.geometry.index.array;
  const M = new Uint8Array(N);
  const U = (i) => uv.getX(i) * W, V = (i) => (flip ? 1 - uv.getY(i) : uv.getY(i)) * H;
  const pinta = (ax, ay, bx, by, cx, cy, val) => {
    const x0 = Math.max(0, Math.floor(Math.min(ax, bx, cx))), x1 = Math.min(W - 1, Math.ceil(Math.max(ax, bx, cx)));
    const y0 = Math.max(0, Math.floor(Math.min(ay, by, cy))), y1 = Math.min(H - 1, Math.ceil(Math.max(ay, by, cy)));
    const ar = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax); if (ar === 0) return; const sgn = ar > 0 ? 1 : -1;
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
      const px = x + 0.5, py = y + 0.5;
      const w0 = ((bx - ax) * (py - ay) - (by - ay) * (px - ax)) * sgn, w1 = ((cx - bx) * (py - by) - (cy - by) * (px - bx)) * sgn, w2 = ((ax - cx) * (py - cy) - (ay - cy) * (px - cx)) * sgn;
      if (w0 >= -0.5 && w1 >= -0.5 && w2 >= -0.5) { const q = y * W + x; if (val > M[q]) M[q] = val; }
    }
  };
  for (let t = 0; t < idx.length; t += 3) {
    const a = idx[t], b = idx[t + 1], c = idx[t + 2], yb = (pos.getY(a) + pos.getY(b) + pos.getY(c)) / 3;
    pinta(U(a), V(a), U(b), V(b), U(c), V(c), yb > ORELHA_Y ? 2 : 1);
  }
  tempos.mascara = Math.round(performance.now() - t0);
  // passada 1: rotula ilhas (flood fill 4-vizinhos, pilha tipada) e soma estatistica por ilha
  const rot = new Int32Array(N).fill(-1), pilha = new Int32Array(N);
  let cap = 4096, n = new Float64Array(cap), az = new Float64Array(cap), sr = new Float64Array(cap), sg = new Float64Array(cap), sb = new Float64Array(cap);
  const cresce = () => { const f = (a) => { const b = new Float64Array(cap * 2); b.set(a); return b; }; n = f(n); az = f(az); sr = f(sr); sg = f(sg); sb = f(sb); cap *= 2; };
  let ilhas = 0;
  for (let s = 0; s < N; s++) {
    if (rot[s] >= 0 || !M[s]) continue;
    if (ilhas >= cap) cresce();
    let topo = 0; pilha[topo++] = s; rot[s] = ilhas;
    while (topo) {
      const q = pilha[--topo], x = q % W, r = D[4 * q], gg = D[4 * q + 1], bb = D[4 * q + 2];
      n[ilhas]++; if (bb > r + 25 && bb > gg + 15) { az[ilhas]++; sr[ilhas] += r; sg[ilhas] += gg; sb[ilhas] += bb; }
      let v;
      if (x > 0 && rot[v = q - 1] < 0 && M[v]) { rot[v] = ilhas; pilha[topo++] = v; }
      if (x < W - 1 && rot[v = q + 1] < 0 && M[v]) { rot[v] = ilhas; pilha[topo++] = v; }
      if (q >= W && rot[v = q - W] < 0 && M[v]) { rot[v] = ilhas; pilha[topo++] = v; }
      if (q < N - W && rot[v = q + W] < 0 && M[v]) { rot[v] = ilhas; pilha[topo++] = v; }
    }
    ilhas++;
  }
  tempos.ilhas = Math.round(performance.now() - t0);
  // passada 2: em ilha de cabelo, fora da zona protegida, branco/cinza vira o azul medio da ilha
  let trocados = 0, ilhasCabelo = 0;
  for (let i = 0; i < ilhas; i++) if (az[i] / n[i] >= fracaoAzul) ilhasCabelo++;
  for (let q = 0; q < N; q++) {
    const i = rot[q]; if (i < 0 || az[i] / n[i] < fracaoAzul || M[q] === 2) continue;
    const r = D[4 * q], gg = D[4 * q + 1], bb = D[4 * q + 2], mx = Math.max(r, gg, bb), mn = Math.min(r, gg, bb);
    if (mx - mn < 60 && (r + gg + bb) > 270) { D[4 * q] = sr[i] / az[i]; D[4 * q + 1] = sg[i] / az[i]; D[4 * q + 2] = sb[i] / az[i]; trocados++; }
  }
  tempos.troca = Math.round(performance.now() - t0);
  g.putImageData(img, 0, 0);
  const novo = new THREE.CanvasTexture(cv);
  for (const p of ["flipY", "colorSpace", "wrapS", "wrapT", "magFilter", "minFilter", "anisotropy", "channel", "generateMipmaps"]) if (p in velho) novo[p] = velho[p];
  novo.needsUpdate = true; m.material.emissiveMap = novo; m.material.needsUpdate = true; velho.dispose();
  return { ok: true, ilhas, ilhasCabelo, trocados, tempos, ms: Math.round(performance.now() - t0) };
}
