// celia-3d-saia.js — tira as manchas da saia da Celia 3D (Kurt, 2026-10-06, rodada 6).
// O Pai: "a saia dela ta um bocado de mancha". Investigacao (p82-p91), na ordem:
//  - de perto, babado preto com riscos azuis/laranja/brancos; limpar texel dentro das ilhas pretas nao mudou nada
//    (antes/depois identicos), acolchoar o atlas tambem nao, desligar o mipmap tambem nao (p86/p90);
//  - instrumento magenta (p91): os riscos vem de texel colorido; o raio pelo pixel cai em preto (sao subpixel);
//  - p93: sao LASCAS — triangulos pequenos da saia mapeados inteiros em ilha de outra peca. Defeito da malha.
// Conserto 1 (malha, faixa da saia): LASCAS — triangulo mapeado inteiro numa ilha colorida mas cercado de triangulos
// pretos (p93: 188 lascas, 20 cm2) ganha vertices proprios com os UVs de um vizinho preto.
// Conserto 2 (textura, faixa da saia): texel tingido/claro na area de triangulo preto da saia vira o preto do entorno,
// e o vao em volta da saia recebe o preto dela (so ali: acolchoar o atlas inteiro sujou o peito, p98).
// Roda uma vez na carga; o .vrm nao muda.
import * as THREE from "three";

// ---- conserto 1: LASCAS (p93) ----
// Triangulo da faixa da saia cujo UV inteiro cai numa ilha colorida (casaco, pele) mas que esta cercado, por posicao,
// de triangulos pretos: 188 lascas, 20 cm2 (medido). Cada lasca ganha 3 vertices proprios (copia de tudo) com os UVs
// de um vizinho preto — os outros triangulos que dividiam aqueles vertices nao mudam.
export function consertarLascas(malha, D, W, H, flip, { Y0 = 0.55, Y1 = 0.95, escuro = 60, tinto = 18, maioria = 0.6 } = {}) {
  const g = malha.geometry, pos = g.attributes.position, uv = g.attributes.uv, ind = g.index.array, N = pos.count, T = ind.length / 3;
  const corC = (t) => { const a = ind[3 * t], b = ind[3 * t + 1], c = ind[3 * t + 2];
    const u = (uv.getX(a) + uv.getX(b) + uv.getX(c)) / 3, v = (uv.getY(a) + uv.getY(b) + uv.getY(c)) / 3;
    const x = Math.min(W - 1, Math.max(0, Math.floor(u * W))), y = Math.min(H - 1, Math.max(0, Math.floor((flip ? 1 - v : v) * H))), q = 4 * (y * W + x);
    return [D[q], D[q + 1], D[q + 2]]; };
  const preto = (c) => Math.max(...c) < escuro && Math.max(...c) - Math.min(...c) < tinto;
  const k = (i) => Math.round(pos.getX(i) * 2e4) + "," + Math.round(pos.getY(i) * 2e4) + "," + Math.round(pos.getZ(i) * 2e4);
  const porChave = new Map(), faixa = [];
  for (let t = 0; t < T; t++) {
    const a = ind[3 * t], b = ind[3 * t + 1], c = ind[3 * t + 2], yb = (pos.getY(a) + pos.getY(b) + pos.getY(c)) / 3;
    if (yb < Y0 || yb > Y1) continue; faixa.push(t);
    for (const i of [a, b, c]) { const kk = k(i); let l = porChave.get(kk); if (!l) porChave.set(kk, l = []); l.push(t); }
  }
  const eh = new Map(); for (const t of faixa) eh.set(t, preto(corC(t)));
  const novos = [], novoIdx = Array.from(ind);
  for (const t of faixa) {
    if (eh.get(t)) continue;
    const viz = new Set(); for (const i of [ind[3 * t], ind[3 * t + 1], ind[3 * t + 2]]) for (const u of porChave.get(k(i)) || []) if (u !== t) viz.add(u);
    if (viz.size < 2) continue; let p = 0, doador = -1; for (const u of viz) if (eh.get(u)) { p++; if (doador < 0) doador = u; }
    if (p / viz.size < maioria || doador < 0) continue;
    for (let c = 0; c < 3; c++) { novos.push([ind[3 * t + c], uv.getX(ind[3 * doador + c]), uv.getY(ind[3 * doador + c])]); novoIdx[3 * t + c] = N + novos.length - 1; }
  }
  if (novos.length) {
    for (const [nome, at] of Object.entries(g.attributes)) {
      const sz = at.itemSize, arr = new at.array.constructor((N + novos.length) * sz); arr.set(at.array.subarray(0, N * sz));
      novos.forEach(([src, u, v], n) => { for (let c = 0; c < sz; c++) arr[(N + n) * sz + c] = at.array[src * sz + c]; if (nome === "uv") { arr[(N + n) * sz] = u; arr[(N + n) * sz + 1] = v; } });
      g.setAttribute(nome, new THREE.BufferAttribute(arr, sz, at.normalized));
    }
    // rodada 15: o .vrm novo tem morph targets no corpo (braco_cima/braco_baixo); os vertices copiados precisam da copia deles tambem,
    // senao a GPU le alem do fim do morph e a saia ganha lascas/espinhos (825 vertices com posicao NaN)
    for (const [nome, lista] of Object.entries(g.morphAttributes || {})) {
      g.morphAttributes[nome] = lista.map((at) => {
        const sz = at.itemSize, arr = new at.array.constructor((N + novos.length) * sz); arr.set(at.array.subarray(0, N * sz));
        novos.forEach(([src], n) => { for (let c = 0; c < sz; c++) arr[(N + n) * sz + c] = at.array[src * sz + c]; });
        const b = new THREE.BufferAttribute(arr, sz, at.normalized); b.name = at.name; return b;
      });
    }
    g.setIndex(new THREE.BufferAttribute(new (ind.constructor)(novoIdx), 1));
  }
  return { lascas: novos.length / 3, verticesNovos: novos.length };
}

// ---- conserto 2: textura da saia + acolchoamento do atlas ----
export function limparSaia(vrm, { malha = "V3_corpo_normal", Y0 = 0.6, Y1 = 0.9, escuro = 60, salto = 45, tinto = 18, PAD_SAIA = 14 } = {}) {
  let m = null; vrm.scene.traverse((o) => { if (o.isMesh && o.name.startsWith(malha) && !m) m = o; });
  const mat = m && (Array.isArray(m.material) ? m.material[0] : m.material), velho = mat && (mat.emissiveMap || mat.map);
  if (!velho || !velho.image) return { ok: false, motivo: "sem mapa" };
  const t0 = performance.now(), W = velho.image.width, H = velho.image.height, flip = velho.flipY, N = W * H;
  const cv = document.createElement("canvas"); cv.width = W; cv.height = H;
  const g = cv.getContext("2d", { willReadFrequently: true }); g.drawImage(velho.image, 0, 0);
  const img = g.getImageData(0, 0, W, H), D = img.data;
  const lascas = consertarLascas(m, D, W, H, flip, { escuro, tinto, maioria: 0.5 });
  const rasteriza = (mesh, filtro, alvo) => {
    const uv = mesh.geometry.attributes.uv, pos = mesh.geometry.attributes.position, idx = mesh.geometry.index.array;
    const U = (i) => uv.getX(i) * W, V = (i) => (flip ? 1 - uv.getY(i) : uv.getY(i)) * H;
    for (let t = 0; t < idx.length; t += 3) {
      const a = idx[t], b = idx[t + 1], c = idx[t + 2]; if (filtro && !filtro(pos, a, b, c)) continue;
      const ax = U(a), ay = V(a), bx = U(b), by = V(b), cx = U(c), cy = V(c);
      const x0 = Math.max(0, Math.floor(Math.min(ax, bx, cx))), x1 = Math.min(W - 1, Math.ceil(Math.max(ax, bx, cx)));
      const y0 = Math.max(0, Math.floor(Math.min(ay, by, cy))), y1 = Math.min(H - 1, Math.ceil(Math.max(ay, by, cy)));
      const ar = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax); if (ar === 0) continue; const sgn = ar > 0 ? 1 : -1;
      for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) { const px = x + 0.5, py = y + 0.5;
        if (((bx - ax) * (py - ay) - (by - ay) * (px - ax)) * sgn >= -0.5 && ((cx - bx) * (py - by) - (cy - by) * (px - bx)) * sgn >= -0.5 && ((ax - cx) * (py - cy) - (ay - cy) * (px - cx)) * sgn >= -0.5) alvo[y * W + x] = 1; }
    }
  };
  // POR TRIANGULO (p90/p91: nem por ilha, nem acolchoando, nem com filtro nearest os riscos mudavam): triangulo da faixa
  // da saia com 2+ vertices em texel preto neutro e "triangulo de saia"; texel colorido ou claro DENTRO da area
  // dele, que nenhum triangulo de outra peca usa, vira o preto do entorno. Texel dividido com outra peca fica (contado).
  const Uv = m.geometry.attributes.uv, Pv = m.geometry.attributes.position;
  const texel = (i) => { const x = Math.min(W - 1, Math.max(0, Math.floor(Uv.getX(i) * W))), y = Math.min(H - 1, Math.max(0, Math.floor((flip ? 1 - Uv.getY(i) : Uv.getY(i)) * H))), q = 4 * (y * W + x); return [D[q], D[q + 1], D[q + 2]]; };
  const pretoNeutro = (c) => Math.max(...c) < escuro && Math.max(...c) - Math.min(...c) < tinto;
  const ehSaia = (pos, a, b, c) => { const yb = (pos.getY(a) + pos.getY(b) + pos.getY(c)) / 3; return yb >= Y0 && yb <= Y1 && (pretoNeutro(texel(a)) + pretoNeutro(texel(b)) + pretoNeutro(texel(c))) >= 2; }; // maioria: o triangulo manchado tem 1 vertice tingido
  const S = new Uint8Array(N), O = new Uint8Array(N);
  rasteriza(m, ehSaia, S);
  rasteriza(m, (pos, a, b, c) => !ehSaia(pos, a, b, c), O);
  vrm.scene.traverse((o) => { if (o === m || !o.isMesh || !o.geometry.index) return; const mo = Array.isArray(o.material) ? o.material[0] : o.material; if ((mo.emissiveMap || mo.map) === velho) rasteriza(o, null, O); });
  let trocados = 0, divididos = 0; const ilhas = 0;
  for (let q = 0; q < N; q++) {
    if (!S[q]) continue;
    const r = D[4 * q], gg = D[4 * q + 1], bb = D[4 * q + 2], mx = Math.max(r, gg, bb), mn = Math.min(r, gg, bb);
    if (mx < escuro && mx - mn <= tinto) continue;
    if (O[q]) { divididos++; continue; }
    // preto do entorno: media dos texels pretos neutros numa janela 7x7 (fallback: preto da saia)
    const x = q % W, y = (q / W) | 0; let sr = 0, sg = 0, sb = 0, k = 0;
    for (let dy = -3; dy <= 3; dy++) for (let dx = -3; dx <= 3; dx++) { const xx = x + dx, yy = y + dy; if (xx < 0 || yy < 0 || xx >= W || yy >= H) continue; const p = 4 * (yy * W + xx);
      const a = D[p], b2 = D[p + 1], c2 = D[p + 2]; if (Math.max(a, b2, c2) < escuro && Math.max(a, b2, c2) - Math.min(a, b2, c2) <= tinto) { sr += a; sg += b2; sb += c2; k++; } }
    if (k) { D[4 * q] = sr / k; D[4 * q + 1] = sg / k; D[4 * q + 2] = sb / k; } else { D[4 * q] = 10; D[4 * q + 1] = 10; D[4 * q + 2] = 12; }
    trocados++;
  }
  // acolchoamento SO em volta da saia: texel que nenhum triangulo usa, perto da saia, recebe o preto dela
  const cob = new Uint8Array(N);
  vrm.scene.traverse((o) => { if (!o.isMesh || !o.geometry.index) return; const mo = Array.isArray(o.material) ? o.material[0] : o.material; if ((mo.emissiveMap || mo.map) === velho) rasteriza(o, null, cob); });
  // fase 1 (p94: os riscos moram na BORDA das ilhas): a saia preta se espalha primeiro pelo vao em volta dela (PAD_SAIA
  // px); sem isso o vao era preenchido pela ilha colorida vizinha e a borda do babado puxava azul/laranja
  // A busca parte de TODAS as ilhas ao mesmo tempo; o texel do vao so recebe o preto se a ilha mais perto dele for a
  // saia (uma fresta mais perto da pele/colar fica como veio).
  const fila = new Int32Array(N), dist = new Uint8Array(N), daSaia = new Uint8Array(N); let nf = 0, acolchoados = 0;
  for (let q = 0; q < N; q++) { dist[q] = cob[q] ? 0 : 255; if (cob[q]) { fila[nf++] = q; daSaia[q] = S[q]; } }
  for (let ini = 0; ini < nf; ini++) {
    const q = fila[ini], d = dist[q]; if (d >= PAD_SAIA) continue; const x = q % W;
    for (const v of [x > 0 ? q - 1 : -1, x < W - 1 ? q + 1 : -1, q >= W ? q - W : -1, q < N - W ? q + W : -1]) {
      if (v < 0 || dist[v] !== 255) continue; dist[v] = d + 1; daSaia[v] = daSaia[q]; fila[nf++] = v;
      if (daSaia[q]) { for (let c = 0; c < 4; c++) D[4 * v + c] = D[4 * q + c]; acolchoados++; }
    }
  }
  // SEM fase 2 (acolchoar o atlas inteiro): o Pai viu manchas pretas no peito — o vao das ilhas da pele foi enchido com
  // o preto do colar vizinho (p98, atual x ?saiacrua). Fora da saia o atlas fica como veio.
  g.putImageData(img, 0, 0);
  const novo = new THREE.CanvasTexture(cv);
  for (const p of ["flipY", "colorSpace", "wrapS", "wrapT", "magFilter", "minFilter", "anisotropy", "channel", "generateMipmaps"]) if (p in velho) novo[p] = velho[p];
  novo.needsUpdate = true; if (mat.emissiveMap === velho) mat.emissiveMap = novo; else mat.map = novo; mat.needsUpdate = true; velho.dispose();
  return { ok: true, lascas, trocados, divididos, acolchoados, ms: Math.round(performance.now() - t0) };
}
