// celia-3d-cauda.js — cauda com ossos e mola (Kurt, 2026-10-06, rodada 6).
// O Pai: "o cabelo ainda ta passando no corpo, atravessando como fantasma". Na imagem dele, a forma azul que corta as
// pernas e a CAUDA: malha 'other' (26.632 vertices) pesada 100% em DEF-Spine (p78/p81) — um bloco rigido de 94 cm,
// torto para a direita dela, que a perna atravessa a cada passo para tras. Aqui, na carga (o .vrm nao muda):
//  1. cadeia de 6 ossos ao longo do eixo da cauda (centros das fatias), filha do quadril;
//  2. pesos novos: cada vertice vai para os dois ossos vizinhos da sua posicao ao longo da cadeia;
//  3. repouso girado para BAIXO e para TRAS, centralizado (em vez de torto para o lado);
//  4. juntas de mola do three-vrm (como o cabelo); o Corpo3D poe nelas os mesmos colisores do corpo (pernas, quadril).
import * as THREE from "three";
import { VRMSpringBoneJoint } from "@pixiv/three-vrm";

export function criarCauda(vrm, opcoes = {}) {
  const t0 = performance.now(), h = vrm.humanoid, sb = vrm.springBoneManager;
  let m = null; vrm.scene.traverse((o) => { if (o.isSkinnedMesh && o.name === (opcoes.malha || "other") && !m) m = o; });
  if (!m || !sb) return { ok: false, erro: "sem malha da cauda ou sem springBoneManager" };
  vrm.scene.updateMatrixWorld(true);
  const pos = m.geometry.attributes.position, N = pos.count, P = new Float32Array(N * 3), v = new THREE.Vector3();
  for (let i = 0; i < N; i++) { v.fromBufferAttribute(pos, i).applyMatrix4(m.bindMatrix); P[3 * i] = v.x; P[3 * i + 1] = v.y; P[3 * i + 2] = v.z; }
  // eixo principal (PCA por iteracao de potencia) e fatias
  const ctr = new THREE.Vector3(); for (let i = 0; i < N; i++) ctr.add(v.set(P[3 * i], P[3 * i + 1], P[3 * i + 2])); ctr.multiplyScalar(1 / N);
  let e = new THREE.Vector3(0, -1, 0);
  for (let it = 0; it < 40; it++) { const n = new THREE.Vector3(); for (let i = 0; i < N; i++) { v.set(P[3 * i], P[3 * i + 1], P[3 * i + 2]).sub(ctr); n.addScaledVector(v, v.dot(e)); } e = n.normalize(); }
  if (e.y > 0) e.negate();
  const s = new Float32Array(N); let smin = Infinity, smax = -Infinity;
  for (let i = 0; i < N; i++) { s[i] = v.set(P[3 * i], P[3 * i + 1], P[3 * i + 2]).sub(ctr).dot(e); smin = Math.min(smin, s[i]); smax = Math.max(smax, s[i]); }
  const NB = 6, juntas = [];
  for (let k = 0; k < NB; k++) { // junta k no centro da fatia em u = k/(NB-1) (raiz no topo, ultima na ponta)
    const u = smin + (smax - smin) * (k / (NB - 1)), meia = (smax - smin) / (NB * 2), c = new THREE.Vector3(); let n = 0;
    for (let i = 0; i < N; i++) if (Math.abs(s[i] - u) < meia) { c.x += P[3 * i]; c.y += P[3 * i + 1]; c.z += P[3 * i + 2]; n++; }
    juntas.push(n ? c.multiplyScalar(1 / n) : ctr.clone().addScaledVector(e, u));
  }
  // ---- 1. ossos ----
  const pai = h.getRawBoneNode("hips"), ossos = [];
  for (let k = 0; k < NB; k++) {
    const b = new THREE.Bone(); b.name = "KURT_cauda_" + k; const dono = k ? ossos[k - 1] : pai;
    dono.updateWorldMatrix(true, false); dono.add(b); b.position.copy(dono.worldToLocal(juntas[k].clone())); b.updateMatrixWorld(true); ossos.push(b);
  }
  // ---- 2. pesos: parametro ao longo da cadeia (projecao no segmento mais proximo) ----
  const L = [0]; for (let k = 1; k < NB; k++) L.push(L[k - 1] + juntas[k].distanceTo(juntas[k - 1]));
  const sk0 = m.skeleton, base = sk0.bones.length;
  const sk = new THREE.Skeleton([...sk0.bones, ...ossos], [...sk0.boneInverses, ...ossos.map((b) => b.matrixWorld.clone().invert())]);
  m.bind(sk, m.bindMatrix);
  const si = m.geometry.attributes.skinIndex, sw = m.geometry.attributes.skinWeight, ab = new THREE.Vector3();
  for (let i = 0; i < N; i++) {
    v.set(P[3 * i], P[3 * i + 1], P[3 * i + 2]);
    let melhor = Infinity, al = 0;
    for (let k = 0; k + 1 < NB; k++) {
      ab.copy(juntas[k + 1]).sub(juntas[k]); const t = Math.max(0, Math.min(1, v.clone().sub(juntas[k]).dot(ab) / ab.lengthSq()));
      const d = v.distanceTo(juntas[k].clone().addScaledVector(ab, t)); if (d < melhor) { melhor = d; al = L[k] + t * ab.length(); }
    }
    let k = 0; while (k + 1 < NB - 1 && al > L[k + 1]) k++;
    const u = Math.max(0, Math.min(1, (al - L[k]) / (L[k + 1] - L[k])));
    si.setXYZW(i, base + k, base + k + 1, 0, 0); sw.setXYZW(i, 1 - u, u, 0, 0);
  }
  si.needsUpdate = true; sw.needsUpdate = true;
  // ---- 3. repouso novo: para baixo e para tras (VRM 1.0 olha +Z: tras e -Z) ----
  const dirAntes = juntas[NB - 1].clone().sub(juntas[0]).normalize();
  // varredura p83 (rodada 6): (-0.15,-0.7,-0.7) com raio 0.15 zerou a cauda na perna em andar/correr/charleston/backflip/
  // polichinelo (rigida: andar 52 quadros, correr 44); mantem um pouco da diagonal do desenho original
  const dirNova = (opcoes.direcao || new THREE.Vector3(-0.15, -0.7, -0.7)).clone().normalize();
  const R = new THREE.Quaternion().setFromUnitVectors(dirAntes, dirNova);
  const qp = pai.getWorldQuaternion(new THREE.Quaternion());
  ossos[0].quaternion.premultiply(qp.clone().invert().multiply(R).multiply(qp));
  ossos[0].updateMatrixWorld(true);
  // ---- 4. mola (o three-vrm exige matrixAutoUpdate = false nos ossos com junta) ----
  const juntasMola = [], cfg = { hitRadius: opcoes.raio || 0.15, stiffness: opcoes.rigidez || 1.6, gravityPower: opcoes.gravidade || 0.35, gravityDir: new THREE.Vector3(0, -1, 0), dragForce: opcoes.arrasto || 0.45 };
  for (let k = 0; k + 1 < NB; k++) {
    ossos[k].updateMatrix();
    const j = new VRMSpringBoneJoint(ossos[k], ossos[k + 1], { ...cfg, stiffness: cfg.stiffness * (k === 0 ? 3 : 1) }, []);
    j.setInitState(); sb.addJoint(j); juntasMola.push(j);
  }
  ossos[NB - 1].updateMatrix();
  return { ok: true, vertices: N, comprimento: +(smax - smin).toFixed(3), juntas: juntas.map((p) => p.toArray().map((x) => +x.toFixed(3))),
    giroGraus: +THREE.MathUtils.radToDeg(dirAntes.angleTo(dirNova)).toFixed(1), molas: juntasMola.length, ms: Math.round(performance.now() - t0) };
}
