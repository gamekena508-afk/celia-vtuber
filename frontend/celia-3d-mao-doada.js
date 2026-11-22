// celia-3d-mao-doada.js — MAO TRANSPLANTADA de uma VRM bem rigada (Kurt, 2026-10-06, rodada 6).
// O Pai: "pesquisa um modelo VRM no GitHub e pega a mao dele, so pinta; uma mao bem rigada". A mao modelada em codigo
// saiu uma pa laranja grande (reprovada). Doadora: VRM1_Constraint_Twist_Sample (c) 2022 pixiv Inc., VRM Public
// License 1.0 — modificar e redistribuir permitido, uso comercial ok, credito dispensado (meta do proprio arquivo:
// modification=allowModificationRedistribution, commercialUsage=corporation, creditNotation=unnecessary).
// Na carga (o .vrm da Celia nao muda):
//  1. sai da malha do corpo a mao velha (sopa de triangulos, dedos fundidos, p72);
//  2. da doadora vem a mao + um pedaco de pulso (que entra na manga), alinhada no pulso da Celia pelo referencial
//     antebraco/cima/frente da T-pose e escalada pelo ANTEBRACO (mao/antebraco da doadora = 0,59, proporcao de anime);
//  3. os ossos dos dedos da Celia vao para as juntas da doadora: o rig da doadora vem inteiro, so com os nomes da Celia;
//  4. textura da pele da doadora tingida para o tom da pele da Celia (unlit, como o resto do corpo).
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin } from "@pixiv/three-vrm";

const DEDOS = ["Index", "Middle", "Ring", "Little"], FAL = ["Proximal", "Intermediate", "Distal"], POL = ["Metacarpal", "Proximal", "Distal"];
const NOMES_MAO = (lado) => [lado + "Hand", ...DEDOS.flatMap((d) => FAL.map((f) => lado + d + f)), ...POL.map((f) => lado + "Thumb" + f)];
const linear = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));

function referencial(h) { // por lado: pulso W, antebraco E, eixos a (antebraco), n (cima), c (frente = lado do indicador)
  const r = {};
  for (const lado of ["left", "right"]) {
    const W = h.getRawBoneNode(lado + "Hand").getWorldPosition(new THREE.Vector3()), E = h.getRawBoneNode(lado + "LowerArm").getWorldPosition(new THREE.Vector3());
    const a = W.clone().sub(E).normalize(), n = new THREE.Vector3(0, 1, 0).addScaledVector(a, -a.y).normalize();
    const c = new THREE.Vector3(0, 0, 1).addScaledVector(a, -a.z); c.addScaledVector(n, -c.dot(n)).normalize();
    r[lado] = { W, E, a, n, c, base: new THREE.Matrix4().makeBasis(a, n, c), ante: W.distanceTo(E) };
  }
  return r;
}
function corMedia(imagem, uvs) { // media (linear) do mapa nos UVs dados
  const cv = document.createElement("canvas"); cv.width = imagem.width; cv.height = imagem.height;
  const g = cv.getContext("2d", { willReadFrequently: true }); g.drawImage(imagem, 0, 0);
  const d = g.getImageData(0, 0, cv.width, cv.height).data, cs = [[], [], []];
  for (const [u, v] of uvs) { const x = Math.min(cv.width - 1, Math.max(0, Math.floor(u * cv.width))), y = Math.min(cv.height - 1, Math.max(0, Math.floor(v * cv.height)));
    const i = (y * cv.width + x) * 4; if (d[i + 3] < 128) continue; for (let k = 0; k < 3; k++) cs[k].push(linear(d[i + k] / 255)); }
  if (!cs[0].length) return null;
  return cs.map((a) => { a.sort((x, y) => x - y); return a[a.length >> 1]; }); // mediana: pega a pele, nao a sombra/boca
}

export async function transplantarMaos(malha, vrm, url, opcoes = {}) {
  const t0 = performance.now(), h = vrm.humanoid, sk = malha.skeleton, g = malha.geometry;
  const stats = { ok: true, removidos: 0, vertices: 0, triangulos: 0, escala: {}, tinta: null };
  // ---- doadora ----
  const L = new GLTFLoader(); L.register((p) => new VRMLoaderPlugin(p));
  const gd = await L.loadAsync(url), dv = gd.userData.vrm; if (!dv) throw new Error("doadora sem VRM: " + url);
  dv.scene.updateMatrixWorld(true); vrm.scene.updateMatrixWorld(true);
  const hd = dv.humanoid, refD = referencial(hd), refC = referencial(h);
  // ---- 1. tira a mao velha da Celia: so a PELE ----
  // Rodada 7 (print do Pai, punho picotado): o punho da manga tambem pesa na mao; tirar todo triangulo "da mao" levava
  // junto o punho e deixava lascas azuis. Agora sai so o triangulo que e pele NA TEXTURA (laranja: r > b); o que e manga
  // (azul/marinho: b >= r) fica e acompanha o pulso, como um punho de verdade.
  const pos = g.attributes.position, si = g.attributes.skinIndex, sw = g.attributes.skinWeight, uv = g.attributes.uv;
  const daMao = new Set();
  for (const lado of ["left", "right"]) { const p = h.getRawBoneNode(lado + "Hand"); sk.bones.forEach((b, j) => { for (let o = b; o; o = o.parent) if (o === p) { daMao.add(j); break; } }); }
  const eh = new Uint8Array(pos.count);
  for (let i = 0; i < pos.count; i++) { let s = 0; for (let c = 0; c < 4; c++) if (daMao.has(si.getComponent(i, c))) s += sw.getComponent(i, c); eh[i] = s >= 0.5 ? 1 : 0; }
  let ehPele = () => true, ehPeleBorda = () => false; // sem textura legivel: comportamento antigo
  try {
    const mc = Array.isArray(malha.material) ? malha.material[0] : malha.material, tx = mc.map || mc.emissiveMap || (mc.uniforms && mc.uniforms.map && mc.uniforms.map.value);
    if (tx && tx.image && uv) {
      const cv = document.createElement("canvas"); cv.width = tx.image.width; cv.height = tx.image.height;
      const gx = cv.getContext("2d", { willReadFrequently: true }); gx.drawImage(tx.image, 0, 0);
      const px = gx.getImageData(0, 0, cv.width, cv.height).data, W0 = cv.width, H0 = cv.height;
      const cor = (u, w) => { const x = Math.min(W0 - 1, Math.max(0, Math.floor(u * W0))), y = Math.min(H0 - 1, Math.max(0, Math.floor(w * H0))), i = (y * W0 + x) * 4; return [px[i], px[i + 1], px[i + 2]]; };
      ehPele = (a, b, c) => { let votos = 0; const us = [[uv.getX(a), uv.getY(a)], [uv.getX(b), uv.getY(b)], [uv.getX(c), uv.getY(c)]];
        us.push([(us[0][0] + us[1][0] + us[2][0]) / 3, (us[0][1] + us[1][1] + us[2][1]) / 3]);
        for (const [u, w] of us) { const k = cor(u, w); if (k[0] > k[2] + 20) votos++; } return votos >= 3; };
      // rodada 15: na boca do punho sobrava triangulo meio pele/meio manga (lasquinha laranja); ali basta metade dos votos
      ehPeleBorda = (a, b, c, minimo = 2) => { let votos = 0; for (const i of [a, b, c]) { const k = cor(uv.getX(i), uv.getY(i)); if (k[0] > k[2] + 20) votos++; } return votos >= minimo; };
      stats.corte = "textura";
    }
  } catch (e) { stats.erroCorte = String(e && e.message || e); }
  // PELE SOLTA ATRAS DO PUNHO (rodada 8, p119 - a seta do print do Pai): sobravam triangulos de pele laranja que pesam no
  // ANTEBRACO (nao na mao), logo o corte acima nao os pega, e ficavam por cima do punho da manga. Perto de cada pulso (no espaco do
  // bind: T-pose, nada encostado), triangulo que e pele NA TEXTURA e nao e mao tambem sai. ?peleantiga = controle A/B.
  const pertoPulso = new Uint8Array(pos.count), bocaPunho = new Uint8Array(pos.count), v1 = new THREE.Vector3();
  if (!opcoes.peleAntiga) for (let i = 0; i < pos.count; i++) {
    v1.fromBufferAttribute(pos, i).applyMatrix4(malha.bindMatrix);
    for (const lado of ["left", "right"]) { const C = refC[lado], q = v1.clone().sub(C.W), s = q.dot(C.a), r = q.addScaledVector(C.a, -s).length();
      if (s > -0.14 && s < 0.03 && r < 0.09) { pertoPulso[i] = 1; if (s > -0.05) bocaPunho[i] = 1; break; } }
  }
  const ind = g.index.array, fica = [], tirados = new Set(); stats.punhoMantido = 0; stats.peleSolta = 0;
  for (let k = 0; k < ind.length; k += 3) {
    if (eh[ind[k]] && eh[ind[k + 1]] && eh[ind[k + 2]]) {
      if (ehPele(ind[k], ind[k + 1], ind[k + 2])) { stats.removidos++; tirados.add(ind[k]); tirados.add(ind[k + 1]); tirados.add(ind[k + 2]); continue; }
      // rodada 15: triangulo que pesa todo na mao e tem UM canto de pele cruza a costura da textura e aparece como espinho laranja na boca do
      // punho (achado por raio na lasquinha: 111 triangulos assim; tirando-os o punho fica fechado). ?semborda = controle A/B.
      if (opcoes.bordaPunho !== false && ehPeleBorda(ind[k], ind[k + 1], ind[k + 2], 1)) { stats.peleBorda = (stats.peleBorda || 0) + 1; continue; }
      stats.punhoMantido++;
    } else if (pertoPulso[ind[k]] && pertoPulso[ind[k + 1]] && pertoPulso[ind[k + 2]] && ehPele(ind[k], ind[k + 1], ind[k + 2])) { stats.peleSolta++; continue; }
    else if (opcoes.bordaPunho !== false && bocaPunho[ind[k]] && bocaPunho[ind[k + 1]] && bocaPunho[ind[k + 2]] && ehPeleBorda(ind[k], ind[k + 1], ind[k + 2])) { stats.peleBorda = (stats.peleBorda || 0) + 1; continue; }
    fica.push(ind[k], ind[k + 1], ind[k + 2]);
  }
  g.setIndex(new THREE.BufferAttribute(new (ind.constructor)(fica), 1));
  // ALVO PELA MALHA, nao pelo osso (p99/p100): alinhada pelo osso do pulso, a mao nova ficava fora do punho. O pulso alvo
  // e o centro do anel onde a PELE original sai da manga; o eixo e o do antebraco desenhado (6-13 cm antes). Perto do
  // pulso a manga pesa nos ossos de torcao (DEF-*_elbow001/002), nao no LowerArm: o antebraco e "o que nao e mao".
  {
    const v0 = new THREE.Vector3();
    for (const lado of ["left", "right"]) {
      const C = refC[lado], pulso = h.getRawBoneNode(lado + "Hand");
      const daM = new Set(); sk.bones.forEach((b, j) => { for (let o = b; o; o = o.parent) if (o === pulso) { daM.add(j); break; } });
      const mao = [], braco = [], manga = [];
      for (let i = 0; i < pos.count; i++) {
        v0.fromBufferAttribute(pos, i).applyMatrix4(malha.bindMatrix); const q = v0.clone().sub(C.W), s = q.dot(C.a);
        if (s < -0.15 || s > 0.12 || q.clone().addScaledVector(C.a, -s).length() > 0.09) continue; // so o braco, nao a saia/corpo encostado
        if (tirados.has(i)) { let sm = 0; for (let c = 0; c < 4; c++) if (daM.has(si.getComponent(i, c))) sm += sw.getComponent(i, c); if (sm >= 0.5) mao.push([s, v0.clone()]); }
        else if (!eh[i] && s > -0.13 && s < -0.06) braco.push(v0.clone());
        if (!tirados.has(i) && s > -0.06 && s < 0.06) manga.push([s, v0.clone()]); // o que sobrou perto do pulso = punho da manga
      }
      if (mao.length < 30 || braco.length < 30) { stats.alvo = "osso (malha insuficiente: " + mao.length + "/" + braco.length + ")"; continue; }
      const ss = mao.map((x) => x[0]).sort((x, y) => x - y), smin = ss[Math.floor(ss.length * 0.03)], anel = mao.filter((x) => x[0] < smin + 0.01).map((x) => x[1]);
      // media aparada (p100): o osso do pulso nao passa pelo meio da mao desenhada e ha pontos soltos no anel; descarta
      // quem esta a mais de 3 cm do centro, 3 vezes
      let Wm = anel.reduce((a, p) => a.add(p), new THREE.Vector3()).multiplyScalar(1 / anel.length), uso = anel;
      for (let r = 0; r < 3; r++) { const bom = uso.filter((p) => p.distanceTo(Wm) < 0.03); if (bom.length < 4) break; uso = bom; Wm = uso.reduce((a, p) => a.add(p), new THREE.Vector3()).multiplyScalar(1 / uso.length); }
      const Em = braco.reduce((a, p) => a.add(p), new THREE.Vector3()).multiplyScalar(1 / braco.length);
      const a = Wm.clone().sub(Em).normalize(), n = new THREE.Vector3(0, 1, 0).addScaledVector(a, -a.y).normalize();
      // CENTRO DO BURACO DA MANGA (rodada 15, "a mao nao ta centralizada com o buraco do casaco"): o anel da pele antiga nao fica no meio do punho.
      // Borda do punho = os pontos de manga mais avancados ao longo do antebraco; a mao vai para o centro DELA (so na direcao radial, a profundidade fica).
      if (opcoes.centroManga !== false && manga.length >= 20) {
        const ms = manga.map(([, p]) => p.clone().sub(Em).dot(a)).sort((x, y) => x - y), smax = ms[Math.floor(ms.length * 0.97)];
        let borda = manga.filter(([, p]) => p.clone().sub(Em).dot(a) > smax - 0.012).map((x) => x[1]);
        let Cm = borda.reduce((s, p) => s.add(p), new THREE.Vector3()).multiplyScalar(1 / borda.length);
        // centro do anel pela caixa radial (a media puxa para o lado com mais vertices)
        const u = n.clone(), w = new THREE.Vector3().crossVectors(a, n).normalize(), lim = [Infinity, -Infinity, Infinity, -Infinity];
        for (const p of borda) { const d = p.clone().sub(Cm); const x = d.dot(u), y = d.dot(w); lim[0] = Math.min(lim[0], x); lim[1] = Math.max(lim[1], x); lim[2] = Math.min(lim[2], y); lim[3] = Math.max(lim[3], y); }
        if (borda.length >= 8 && lim[1] - lim[0] < 0.12 && lim[3] - lim[2] < 0.12) {
          Cm.addScaledVector(u, (lim[0] + lim[1]) / 2).addScaledVector(w, (lim[2] + lim[3]) / 2);
          const d = Cm.clone().sub(Wm); d.addScaledVector(a, -d.dot(a));
          stats.centroManga_mm = stats.centroManga_mm || {}; stats.centroManga_mm[lado] = { desloc: Math.round(d.length() * 1000), larg: Math.round((lim[1] - lim[0]) * 1000), alt: Math.round((lim[3] - lim[2]) * 1000), n: borda.length };
          Wm.add(d.multiplyScalar(opcoes.centroMangaFator ?? 1));
        }
      }
      const c = new THREE.Vector3(0, 0, 1).addScaledVector(a, -a.z); c.addScaledVector(n, -c.dot(n)).normalize();
      stats.desvioPulso_mm = stats.desvioPulso_mm || {}; stats.desvioPulso_mm[lado] = Math.round(Wm.distanceTo(C.W) * 1000);
      { const d = Wm.clone().sub(C.W), sd = d.dot(C.a); stats.anel = stats.anel || {}; stats.anel[lado] = { smin_mm: Math.round(smin * 1000), n: anel.length, nMao: mao.length, nBraco: braco.length,
        s_mm: Math.round(sd * 1000), radial_mm: Math.round(d.addScaledVector(C.a, -sd).length() * 1000), Em_s_mm: Math.round(Em.clone().sub(C.W).dot(C.a) * 1000), Em_r_mm: Math.round(Em.clone().sub(C.W).addScaledVector(C.a, -Em.clone().sub(C.W).dot(C.a)).length() * 1000) }; }
      stats.alvo = "malha"; refC[lado] = { ...C, W: Wm.clone().addScaledVector(a, -(opcoes.dentroManga ?? 0.012)), a, n, c };
    }
  }
  // ---- 2. malha da doadora que pesa nos bracos ----
  let fonte = null; dv.scene.traverse((o) => { if (o.isSkinnedMesh && !fonte) { const n = o.skeleton.bones.indexOf(hd.getRawBoneNode("leftHand")); if (n >= 0) fonte = o; } });
  if (!fonte) throw new Error("doadora sem malha na mao");
  const fg = fonte.geometry, fp = fg.attributes.position, fn = fg.attributes.normal, fuv = fg.attributes.uv, fsi = fg.attributes.skinIndex, fsw = fg.attributes.skinWeight;
  const fsk = fonte.skeleton;
  // osso da doadora -> osso da Celia (nome humanoid; osso extra, como o twist do antebraco, sobe ate o humanoid acima)
  const humD = new Map(Object.keys(hd.humanBones).map((k) => [hd.getRawBoneNode(k), k]).filter(([n]) => n));
  const mapa = fsk.bones.map((b) => { for (let o = b; o; o = o.parent) if (humD.has(o)) { const c = h.getRawBoneNode(humD.get(o)); return c ? sk.bones.indexOf(c) : -1; } return -1; });
  const ladoDe = fsk.bones.map((b) => { for (let o = b; o; o = o.parent) { const k = humD.get(o); if (k === "leftLowerArm" || k === "leftHand") return "left"; if (k === "rightLowerArm" || k === "rightHand") return "right"; if (k === "leftUpperArm" || k === "rightUpperArm") return null; } return null; });
  const CORTE = opcoes.corte || 0.3; // fracao do antebraco atras do pulso que vem junto (entra na manga)
  const novo = new Map(), P3 = [], N3 = [], UV = [], SI = [], SW = [], uvsMao = [], v = new THREE.Vector3(), nr = new THREE.Vector3();
  const matN = new THREE.Matrix3().getNormalMatrix(fonte.bindMatrix);
  const leva = (i) => { // devolve indice novo ou -1
    if (novo.has(i)) return novo.get(i);
    let lado = null, mx = 0; for (let c = 0; c < 4; c++) { const w = fsw.getComponent(i, c); if (w > mx) { mx = w; lado = ladoDe[fsi.getComponent(i, c)]; } }
    if (!lado) { novo.set(i, -1); return -1; }
    const D = refD[lado], C = refC[lado];
    v.fromBufferAttribute(fp, i).applyMatrix4(fonte.bindMatrix).sub(D.W);
    const s = v.dot(D.a); if (s < -CORTE * D.ante) { novo.set(i, -1); return -1; }
    const k = (C.ante / D.ante) * (opcoes.escala || 1); stats.escala[lado] = +k.toFixed(3);
    // coordenadas no referencial da doadora -> mesmas coordenadas no da Celia
    const q = new THREE.Vector3(v.dot(D.a), v.dot(D.n), v.dot(D.c)).multiplyScalar(k);
    const p = C.W.clone().addScaledVector(C.a, q.x).addScaledVector(C.n, q.y).addScaledVector(C.c, q.z);
    nr.fromBufferAttribute(fn, i).applyMatrix3(matN).normalize();
    const qn = new THREE.Vector3(nr.dot(D.a), nr.dot(D.n), nr.dot(D.c)), pn = C.a.clone().multiplyScalar(qn.x).addScaledVector(C.n, qn.y).addScaledVector(C.c, qn.z);
    const pesos = new Map();
    for (let c = 0; c < 4; c++) { const w = fsw.getComponent(i, c); if (w <= 0) continue; const j = mapa[fsi.getComponent(i, c)]; if (j >= 0) pesos.set(j, (pesos.get(j) || 0) + w); }
    if (!pesos.size) { novo.set(i, -1); return -1; }
    const l = [...pesos.entries()].sort((x, y) => y[1] - x[1]).slice(0, 4), tot = l.reduce((x, e) => x + e[1], 0);
    P3.push(p.x, p.y, p.z); N3.push(pn.x, pn.y, pn.z); UV.push(fuv.getX(i), fuv.getY(i));
    for (let c = 0; c < 4; c++) { SI.push(l[c] ? l[c][0] : 0); SW.push(l[c] ? l[c][1] / tot : 0); }
    if (s >= 0) uvsMao.push([fuv.getX(i), fuv.getY(i)]);
    const id = P3.length / 3 - 1; novo.set(i, id); return id;
  };
  // so os triangulos do material de pele (o grupo do contorno MToon repete os mesmos triangulos)
  const fi = fg.index.array, grupos = fg.groups.length ? fg.groups : [{ start: 0, count: fi.length, materialIndex: 0 }];
  const vistos = new Set(), IDX = [];
  for (const gr of grupos) {
    const mats = Array.isArray(fonte.material) ? fonte.material : [fonte.material], m = mats[gr.materialIndex];
    if (m && /outline/i.test(m.name || "")) continue;
    for (let k = gr.start; k < gr.start + gr.count; k += 3) {
      const tri = [fi[k], fi[k + 1], fi[k + 2]], chave = tri.slice().sort((x, y) => x - y).join(",");
      if (vistos.has(chave)) continue; vistos.add(chave);
      const n = tri.map(leva); if (n.some((x) => x < 0)) continue; IDX.push(n[0], n[1], n[2]);
    }
  }
  // ---- 3. ossos dos dedos da Celia nas juntas da doadora ----
  const movidos = [];
  for (const lado of ["left", "right"]) {
    const D = refD[lado], C = refC[lado], k = (C.ante / D.ante) * (opcoes.escala || 1);
    for (const nome of NOMES_MAO(lado).slice(1)) {
      const od = hd.getRawBoneNode(nome), oc = h.getRawBoneNode(nome); if (!od || !oc) continue;
      const q = od.getWorldPosition(new THREE.Vector3()).sub(D.W), p = C.W.clone().addScaledVector(C.a, q.dot(D.a) * k).addScaledVector(C.n, q.dot(D.n) * k).addScaledVector(C.c, q.dot(D.c) * k);
      oc.parent.updateWorldMatrix(true, false); oc.position.copy(oc.parent.worldToLocal(p)); oc.updateWorldMatrix(false, true); movidos.push(oc);
    }
  }
  malha.parent.updateWorldMatrix(true, true);
  for (const b of movidos) { const j = sk.bones.indexOf(b); if (j >= 0) sk.boneInverses[j].copy(b.matrixWorld).invert(); }
  // rig normalizado: posicao = mundo(osso) - mundo(pai humanoid) (o retarget dos clipes usa estas posicoes)
  const humC = new Map(Object.keys(h.humanBones).map((k) => [h.getRawBoneNode(k), k]).filter(([n]) => n));
  for (const b of movidos) { const nome = humC.get(b), nn = nome && h.getNormalizedBoneNode(nome); let pai = b.parent; while (pai && !humC.has(pai)) pai = pai.parent;
    if (nn && pai) nn.position.copy(b.getWorldPosition(new THREE.Vector3()).sub(pai.getWorldPosition(new THREE.Vector3()))); }
  // ---- 4. material: pele da doadora tingida para a pele da Celia ----
  const matD = (Array.isArray(fonte.material) ? fonte.material : [fonte.material]).find((m) => !/outline/i.test(m.name || ""));
  const mapa2 = matD && (matD.map || (matD.uniforms && matD.uniforms.map && matD.uniforms.map.value));
  // tom: a pele do ROSTO dela (a do corpo e laranja chapado; o rosto e o que o olho compara). Sombreado de borda por
  // matcap (centro 1.0 -> borda 0.72): os dedos leem como volume sem precisar de luz (o corpo e unlit).
  const cvm = document.createElement("canvas"); cvm.width = cvm.height = 128;
  { const gm = cvm.getContext("2d"), rg = gm.createRadialGradient(56, 52, 6, 64, 64, 64); rg.addColorStop(0, "#ffffff"); rg.addColorStop(0.62, "#f2f2f2"); rg.addColorStop(1, "#b8b8b8"); gm.fillStyle = rg; gm.fillRect(0, 0, 128, 128); }
  const matcap = new THREE.CanvasTexture(cvm); matcap.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.MeshMatcapMaterial({ map: mapa2 || null, matcap });
  try {
    const corD = mapa2 && mapa2.image ? corMedia(mapa2.image, uvsMao) : null;
    let rosto = null; vrm.scene.traverse((o) => { if (o.isMesh && o.name === (opcoes.malhaPele || "FACE_VRM") && !rosto) rosto = o; });
    let corC = null;
    if (rosto) { const mr = Array.isArray(rosto.material) ? rosto.material[0] : rosto.material, tx = mr.map || mr.emissiveMap || (mr.uniforms && mr.uniforms.map && mr.uniforms.map.value);
      const uvr = rosto.geometry.attributes.uv, lista = []; for (let i = 0; i < uvr.count; i += 2) lista.push([uvr.getX(i), uvr.getY(i)]);
      if (tx && tx.image) corC = corMedia(tx.image, lista); stats.peleDe = rosto.name; }
    if (!corC) { const mc = Array.isArray(malha.material) ? malha.material[0] : malha.material, emi = mc.emissiveMap || mc.map;
      corC = emi && emi.image ? corMedia(emi.image, [opcoes.uvPele || [0.9542534351348877, 0.291090726852417]]) : null; stats.peleDe = "corpo"; }
    if (corD && corC) { const t = corC.map((x, i) => Math.min(1.5, x / Math.max(1e-3, corD[i]))); mat.color.setRGB(t[0], t[1], t[2]); stats.tinta = { doadora: corD.map((x) => +x.toFixed(3)), celia: corC.map((x) => +x.toFixed(3)) }; }
  } catch (e) { stats.erroTinta = String(e && e.message || e); }
  // ---- geometria no espaco do bind da malha do corpo ----
  const inv = malha.bindMatrix.clone().invert(), nInv = new THREE.Matrix3().getNormalMatrix(inv);
  for (let i = 0; i < P3.length; i += 3) {
    v.set(P3[i], P3[i + 1], P3[i + 2]).applyMatrix4(inv); P3[i] = v.x; P3[i + 1] = v.y; P3[i + 2] = v.z;
    v.set(N3[i], N3[i + 1], N3[i + 2]).applyMatrix3(nInv).normalize(); N3[i] = v.x; N3[i + 1] = v.y; N3[i + 2] = v.z;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(P3, 3)); geo.setAttribute("normal", new THREE.Float32BufferAttribute(N3, 3));
  geo.setAttribute("uv", new THREE.Float32BufferAttribute(UV, 2));
  geo.setAttribute("skinIndex", new THREE.Uint16BufferAttribute(SI, 4)); geo.setAttribute("skinWeight", new THREE.Float32BufferAttribute(SW, 4));
  geo.setIndex(IDX);
  const nova = new THREE.SkinnedMesh(geo, mat); nova.name = "MAOS_DOADAS"; nova.frustumCulled = false;
  nova.position.copy(malha.position); nova.quaternion.copy(malha.quaternion); nova.scale.copy(malha.scale);
  malha.parent.add(nova); nova.bind(sk, malha.bindMatrix);
  stats.vertices = P3.length / 3; stats.triangulos = IDX.length / 3; stats.ms = Math.round(performance.now() - t0);
  dv.scene.traverse((o) => { if (o.isMesh && o !== fonte) { o.geometry.dispose(); } });
  return stats;
}
