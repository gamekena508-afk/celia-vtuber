// celia-3d-oculos.js — oculos redondo da Celia 3D (ordem do Pai, 2026-10-18: "o oculosinho que tem no modelo dela, tambem no 3D").
// Geometria propria (torus + ponte + hastes), presa ao osso CRU da cabeca: gira junto com ela, sem tocar em osso, shape key ou material do .vrm.
// Medidas saem dos olhos do proprio VRM (distancia interpupilar), entao acompanham a escala do modelo. Dono unico: este modulo; ligar/desligar
// so por setVisivel(). Nada aqui escreve em ossos normalizados (o Corpo3D continua sendo o unico escritor deles).
import * as THREE from "three";

export function criarOculos(vrm, opcoes = {}) {
  const h = vrm.humanoid;
  const cabeca = h.getRawBoneNode("head");
  const olhoE = h.getRawBoneNode("leftEye"), olhoD = h.getRawBoneNode("rightEye");
  if (!cabeca) return { ok: false, motivo: "sem osso head", setVisivel() {}, visivel: false };
  vrm.scene.updateMatrixWorld(true);
  const pE = new THREE.Vector3(), pD = new THREE.Vector3();
  // Medido: este VRM NAO tem ossos de olho (humanoid.leftEye/rightEye = null). Os olhos estao nas malhas _EYE_L/_EYE_R; usa o centro da caixa
  // delas (com pele aplicada). Sem nenhuma das duas fontes, desiste e diz por que (ok:false), em vez de pregar o oculos na junta do pescoco.
  const acharMalha = (nome) => { let m = null; vrm.scene.traverse((o) => { if (!m && o.name === nome && (o.isMesh || o.isSkinnedMesh)) m = o; }); return m; };
  const centro = (o, alvo) => new THREE.Box3().setFromObject(o, true).getCenter(alvo);
  let fonte = "";
  if (olhoE && olhoD) { olhoE.getWorldPosition(pE); olhoD.getWorldPosition(pD); fonte = "ossos"; }
  else {
    const mE = acharMalha("_EYE_L"), mD = acharMalha("_EYE_R");
    if (mE && mD) { centro(mE, pE); centro(mD, pD); fonte = "malhas _EYE_L/_EYE_R"; }
  }
  if (!fonte) return { ok: false, motivo: "sem ossos de olho nem malhas _EYE_L/_EYE_R", setVisivel() {}, visivel: false };
  // O osso da cabeca pode estar girado em relacao ao mundo (medido: a frente do rosto nao e +Z local do osso). Por isso o grupo nasce com a
  // rotacao INVERSA da cabeca e todas as medidas abaixo ficam em eixos do MUNDO no repouso: +X lado, +Y cima, +Z frente do VRM 1.0.
  const meioMundo = pE.clone().add(pD).multiplyScalar(0.5);
  const d = Math.max(0.01, pE.distanceTo(pD));           // distancia entre os olhos (mundo, escala 1 medida)
  const raio = d * (opcoes.raio ?? 0.36);                // raio da lente
  const fio = raio * 0.085;                              // espessura do aro
  const frente = d * (opcoes.frente ?? 0.28);            // quanto sai do plano dos olhos para a frente
  const qCabeca = new THREE.Quaternion(); cabeca.getWorldQuaternion(qCabeca);
  const grupo = new THREE.Group(); grupo.name = "celia-oculos"; grupo.visible = false;
  grupo.position.copy(cabeca.worldToLocal(meioMundo.clone()));
  grupo.quaternion.copy(qCabeca.clone().invert());
  const moldura = new THREE.MeshBasicMaterial({ color: opcoes.cor ?? 0x1b2a4d, toneMapped: false });
  const lenteMat = new THREE.MeshBasicMaterial({ color: 0xbfe6ff, transparent: true, opacity: 0.14, depthWrite: false, toneMapped: false });
  const lados = [pE, pD].map((p) => p.clone().sub(meioMundo));  // posicoes dos olhos relativas ao meio, em eixos do mundo
  for (const l of lados) {
    const aro = new THREE.Mesh(new THREE.TorusGeometry(raio, fio, 10, 40), moldura);
    aro.position.set(l.x, l.y, frente);
    const lente = new THREE.Mesh(new THREE.CircleGeometry(raio, 32), lenteMat);
    lente.position.copy(aro.position);
    grupo.add(aro, lente);
  }
  const ponte = new THREE.Mesh(new THREE.CylinderGeometry(fio, fio, Math.max(0.001, d - 2 * raio), 8), moldura);
  ponte.rotation.z = Math.PI / 2; ponte.position.set(0, raio * 0.25, frente);
  grupo.add(ponte);
  const comp = d * 1.5;                                  // hastes de volta para as orelhas
  for (const l of lados) {
    const sinal = Math.sign(l.x) || 1;
    const haste = new THREE.Mesh(new THREE.CylinderGeometry(fio * 0.8, fio * 0.8, comp, 8), moldura);
    haste.rotation.x = Math.PI / 2;
    haste.position.set(l.x + sinal * (raio + fio), l.y, frente - comp / 2);
    grupo.add(haste);
  }
  cabeca.add(grupo);
  const api = {
    ok: true, fonte, grupo, raio, d, visivel: false,
    setVisivel(v) { api.visivel = Boolean(v); grupo.visible = api.visivel; },
  };
  return api;
}
