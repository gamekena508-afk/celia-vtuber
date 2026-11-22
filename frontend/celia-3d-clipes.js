// celia-3d-clipes.js — clipes de animacao de verdade na Celia 3D (Kurt, 2026-10-06, rodada 6).
// Duas fontes, as duas convertidas no MESMO formato assado (quaternions locais dos ossos NORMALIZADOS, 30 qps):
//  - GLB do Mesh2Motion (CC0): retarget aqui, mundo -> mundo, por osso:  W = (Q_anim * Q_rep^-1) * C
//    Q_* = rotacao de mundo do osso da fonte (animado / repouso); C = F_fonte * F_vrm^-1 alinha o quadro de repouso
//    da fonte (A-pose) ao da T-pose normalizada do VRM. Cada quadro F sai de dois vetores medidos nos dois esqueletos:
//    osso -> filho e uma referencia (atravessando a palma nas maos e dedos; frente ou cima no resto).
//  - .vrma (Clawatar, Mixamo NON-COMMERCIAL: so experimento): createVRMAnimationClip do three-vrm-animation e amostragem.
// O corpo (celia-3d-corpo.js) aplica o clipe por cima do procedural, com o mesmo peso de entrada/saida das acoes.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from "./libs/three-vrm/three-vrm-animation.module.min.js";

export const QPS = 30;
const DEDOS_M2M = { index: "Index", middle: "Middle", ring: "Ring", pinky: "Little" };

// fonte (Mesh2Motion, esqueleto estilo UE) -> osso humanoid do VRM; filho usado para a direcao do osso
function mapaM2M() {
  const m = { pelvis: ["hips", "spine_01"], spine_01: ["spine", "spine_02"], spine_02: ["chest", "spine_03"],
    spine_03: ["upperChest", "neck_01"], neck_01: ["neck", "head"], head: ["head", "head_leaf"] };
  for (const [s, L] of [["l", "left"], ["r", "right"]]) {
    m["clavicle_" + s] = [L + "Shoulder", "upperarm_" + s];
    m["upperarm_" + s] = [L + "UpperArm", "lowerarm_" + s];
    m["lowerarm_" + s] = [L + "LowerArm", "hand_" + s];
    m["hand_" + s] = [L + "Hand", "middle_01_" + s];
    m["thigh_" + s] = [L + "UpperLeg", "calf_" + s];
    m["calf_" + s] = [L + "LowerLeg", "foot_" + s];
    m["foot_" + s] = [L + "Foot", "ball_" + s];
    m["ball_" + s] = [L + "Toes", "ball_leaf_" + s];
    const F = ["Proximal", "Intermediate", "Distal"];
    for (const [d, D] of Object.entries(DEDOS_M2M)) for (let i = 1; i <= 3; i++) m[`${d}_0${i}_${s}`] = [L + D + F[i - 1], `${d}_0${i + 1}${i === 3 ? "_leaf" : ""}_${s}`];
    const T = ["Metacarpal", "Proximal", "Distal"];
    for (let i = 1; i <= 3; i++) m[`thumb_0${i}_${s}`] = [L + "Thumb" + T[i - 1], `thumb_0${i + 1}${i === 3 ? "_leaf" : ""}_${s}`];
  }
  return m;
}
// filho humanoid de cada osso do VRM, para a direcao do osso na T-pose normalizada
function filhoVRM(b) {
  const f = { hips: "spine", spine: "chest", chest: "upperChest", upperChest: "neck", neck: "head" };
  if (f[b]) return f[b];
  const m = /^(left|right)(.*)$/.exec(b); if (!m) return null;
  const [, L, r] = m;
  const g = { Shoulder: "UpperArm", UpperArm: "LowerArm", LowerArm: "Hand", Hand: "MiddleProximal", UpperLeg: "LowerLeg", LowerLeg: "Foot", Foot: "Toes",
    ThumbMetacarpal: "ThumbProximal", ThumbProximal: "ThumbDistal" };
  if (g[r]) return L + g[r];
  const d = /^(Index|Middle|Ring|Little)(Proximal|Intermediate)$/.exec(r);
  if (d) return L + d[1] + (d[2] === "Proximal" ? "Intermediate" : "Distal");
  return null;
}
const CIMA = new THREE.Vector3(0, 1, 0), FRENTE = new THREE.Vector3(0, 0, 1);
function quadro(prim, ref) {
  const x = prim.clone().normalize();
  let r = ref.clone().normalize();
  if (Math.abs(x.dot(r)) > 0.9) r = Math.abs(x.dot(FRENTE)) > 0.9 ? CIMA.clone() : FRENTE.clone();
  const z = r.addScaledVector(x, -r.dot(x)).normalize(), y = new THREE.Vector3().crossVectors(z, x);
  return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(x, y, z));
}

// posicoes de repouso (T-pose normalizada) em espaco da cena do VRM; zera a pose normalizada (o corpo reescreve no quadro seguinte)
function restoVRM(vrm) {
  const h = vrm.humanoid; h.resetNormalizedPose();
  vrm.scene.updateMatrixWorld(true);
  const inv = vrm.scene.matrixWorld.clone().invert(), pos = {};
  for (const b of Object.keys(h.humanBones)) { const n = h.getNormalizedBoneNode(b); if (n) pos[b] = n.getWorldPosition(new THREE.Vector3()).applyMatrix4(inv); }
  return pos;
}
// pai humanoid mais proximo que esta no clipe (osso intermediario sem trilha tem local = identidade)
function paiNoClipe(vrm, b, noClipe) {
  const h = vrm.humanoid, nos = new Map(Object.keys(h.humanBones).map((k) => [h.getNormalizedBoneNode(k), k]));
  let n = h.getNormalizedBoneNode(b).parent;
  while (n) { const k = nos.get(n); if (k && noClipe.has(k)) return k; n = n.parent; }
  return null;
}
function referencia(b, P, lado, semDedos = false) { // P: posicoes por nome VRM (fonte ou alvo, mesmo criterio nos dois)
  const m = /^(left|right)(Hand|Thumb|Index|Middle|Ring|Little)/.exec(b);
  if (!semDedos && m && P[lado + "IndexProximal"] && P[lado + "LittleProximal"]) return P[lado + "LittleProximal"].clone().sub(P[lado + "IndexProximal"]);
  if (/Foot|Toes/.test(b)) return CIMA.clone();
  return FRENTE.clone();
}

// Esqueletos de fonte conhecidos. mapa: osso da fonte -> [osso humanoid do VRM, filho da fonte para a direcao do osso]; filho "^nome" =
// direcao de "nome" ate o osso (osso de ponta, sem filho). n: nomes do quadril e dos pes (para escala, frente e trava de pe).
const CFG_M2M = { mapa: mapaM2M, n: { pelvis: "pelvis", footL: "foot_l", footR: "foot_r", ballL: "ball_l", ballR: "ball_r" }, extras: [], semDedos: false, prefixo: "" };
// KayKit Character Animations 1.1 (CC0, Rig_Medium): hips/spine/chest/head, braco com wrist+hand, SEM dedos, SEM pescoco/upperChest/ombro.
// Os ossos que o VRM tem a mais (upperChest, neck) ficam em identidade; o osso filho de wrist e hand.X (direcao da palma).
function mapaKayKit() {
  const m = { hips: ["hips", "spine"], spine: ["spine", "chest"], chest: ["chest", "head"], head: ["head", "^chest"] };
  for (const [s, L] of [["l", "left"], ["r", "right"]]) {
    m["upperarm." + s] = [L + "UpperArm", "lowerarm." + s]; m["lowerarm." + s] = [L + "LowerArm", "wrist." + s]; m["wrist." + s] = [L + "Hand", "hand." + s];
    m["upperleg." + s] = [L + "UpperLeg", "lowerleg." + s]; m["lowerleg." + s] = [L + "LowerLeg", "foot." + s]; m["foot." + s] = [L + "Foot", "toes." + s]; m["toes." + s] = [L + "Toes", "^foot." + s];
  }
  return m;
}
export const CFG_KAYKIT = { mapa: mapaKayKit, n: { pelvis: "hips", footL: "foot.l", footR: "foot.r", ballL: "toes.l", ballR: "toes.r" }, extras: ["hand.l", "hand.r"], semDedos: true, prefixo: "kk:" };

// Mesh2Motion GLB -> { nome: clipe }. `nomes`: lista dos clipes a converter (todos se omitida)
export async function carregarM2M(url, vrm, nomes = null) { return carregarMocap(url, vrm, nomes, CFG_M2M); }
export async function carregarKayKit(url, vrm, nomes = null) { return carregarMocap(url, vrm, nomes, CFG_KAYKIT); }
async function carregarMocap(url, vrm, nomes, cfg) {
  const t0 = performance.now(), N = cfg.n;
  const g = await new GLTFLoader().loadAsync(url);
  const raiz = g.scene, MAPA = cfg.mapa(), src = {};
  // GLTFLoader tira . [ ] : / dos nomes (foot.l -> footl); o nome original fica em userData.name
  raiz.traverse((o) => { const nm = (o.userData && o.userData.name) || o.name; if (MAPA[nm] || cfg.extras.includes(nm) || /_leaf/.test(nm)) src[nm] = o; });
  const h = vrm.humanoid, alvo = Object.entries(MAPA).filter(([s, [b]]) => src[s] && h.getNormalizedBoneNode(b));
  // repouso da fonte: TRS dos nos como vieram no GLB
  raiz.updateMatrixWorld(true);
  const wp = (o) => o.getWorldPosition(new THREE.Vector3()), wq = (o) => o.getWorldQuaternion(new THREE.Quaternion());
  const fr = wp(src[N.ballL]).sub(wp(src[N.footL])); fr.y = 0; fr.normalize();
  const G = new THREE.Quaternion().setFromUnitVectors(fr, FRENTE); // a fonte olha para fr; o VRM 1.0 olha +Z
  const Psrc = {}, PsrcNome = {}, Qrep = {};
  for (const [s, o] of Object.entries(src)) { PsrcNome[s] = wp(o).applyQuaternion(G); }
  for (const [s, [b]] of alvo) { Psrc[b] = PsrcNome[s]; Qrep[s] = G.clone().multiply(wq(src[s])); }
  const Pvrm = restoVRM(vrm);
  // C por osso
  const C = {};
  for (const [s, [b, filhoS]] of alvo) {
    const lado = b.startsWith("left") ? "left" : "right";
    const dS = filhoS && filhoS[0] === "^" ? PsrcNome[s].clone().sub(PsrcNome[filhoS.slice(1)] || PsrcNome[s]) : (PsrcNome[filhoS] || PsrcNome[s]).clone().sub(PsrcNome[s]);
    const fv = filhoVRM(b);
    let dV = fv && Pvrm[fv] ? Pvrm[fv].clone().sub(Pvrm[b]) : null;
    if (!dV || dV.lengthSq() < 1e-8) dV = dS.clone(); // sem filho no VRM (cabeca, ponta do dedo): mesma direcao da fonte
    if (dS.lengthSq() < 1e-8) { C[b] = new THREE.Quaternion(); continue; }
    const Fs = quadro(dS, referencia(b, Psrc, lado, cfg.semDedos)), Fv = quadro(dV, referencia(b, Pvrm, lado, cfg.semDedos));
    C[b] = Fs.multiply(Fv.invert());
  }
  const noClipe = new Set(alvo.map(([, [b]]) => b)), pai = {};
  for (const b of noClipe) pai[b] = paiNoClipe(vrm, b, noClipe);
  // escala: altura do quadril acima do pe, VRM / fonte
  const altV = Pvrm.hips.y - Math.min(Pvrm.leftFoot.y, Pvrm.rightFoot.y);
  const altS = PsrcNome[N.pelvis].y - Math.min(PsrcNome[N.footL].y, PsrcNome[N.footR].y);
  const esc = altV / altS, quadrilRep = PsrcNome[N.pelvis].clone();
  // "pe" = ponta do pe (ball): e ela que fica plantada no apoio; o tornozelo rola com o calcanhar (medido, p70)
  const PE_S = src[N.ballL] && src[N.ballR] ? [N.ballL, N.ballR] : [N.footL, N.footR];
  const pesRep = [PsrcNome[PE_S[0]].y, PsrcNome[PE_S[1]].y];
  const mixer = new THREE.AnimationMixer(raiz), saida = {};
  const W = {}, Q1 = new THREE.Quaternion(), Q2 = new THREE.Quaternion();
  for (const clip of g.animations) {
    if (nomes && !nomes.includes(clip.name)) continue;
    const dur = clip.duration, n = Math.max(2, Math.round(dur * QPS) + 1);
    const ossos = {}; for (const b of noClipe) ossos[b] = new Float32Array(n * 4);
    const quadril = new Float32Array(n * 3), pes = [new Float32Array(n * 3), new Float32Array(n * 3)];
    mixer.stopAllAction(); const ac = mixer.clipAction(clip);
    ac.setLoop(THREE.LoopOnce, 1); ac.clampWhenFinished = true; ac.reset().play(); // o ultimo quadro nao volta ao 0
    for (let i = 0; i < n; i++) {
      mixer.setTime(Math.min(dur - 1e-4, (i / (n - 1)) * dur)); raiz.updateMatrixWorld(true);
      for (const [s, [b]] of alvo) W[b] = G.clone().multiply(wq(src[s])).multiply(Q1.copy(Qrep[s]).invert()).multiply(C[b]);
      for (const b of noClipe) {
        const q = pai[b] ? Q2.copy(W[pai[b]]).invert().multiply(W[b]) : Q2.copy(W[b]);
        ossos[b].set([q.x, q.y, q.z, q.w], i * 4);
      }
      const p = wp(src[N.pelvis]).applyQuaternion(G).sub(quadrilRep).multiplyScalar(esc); quadril.set([p.x, p.y, p.z], i * 3);
      for (const [k, f] of [[0, PE_S[0]], [1, PE_S[1]]]) { const q = wp(src[f]).applyQuaternion(G).multiplyScalar(esc); pes[k].set([q.x, q.y - pesRep[k] * esc, q.z], i * 3); }
    }
    ac.stop(); mixer.uncacheAction(clip);
    // velocidade de caminhada no proprio clipe: o pe de apoio anda para tras em relacao ao quadril.
    // Janela de apoio por pe: o maior trecho continuo (ciclico) com a ponta do pe ate 1,2 cm do seu ponto mais baixo;
    // vel = recuo total da ponta / duracao do trecho. A mediana quadro a quadro errava na
    // corrida (apoio de poucos quadros) e deixava o pe esquerdo escorregando 2x o direito no andar (p70).
    const piso = [0, 1].map((k) => { let m = Infinity; for (let i = 0; i < n; i++) m = Math.min(m, pes[k][i * 3 + 1]); return m; });
    const velPe = [0, 1].map((k) => {
      const chao = (i) => pes[k][(((i % n) + n) % n) * 3 + 1] <= piso[k] + 0.012;
      let melhor = [0, -1];
      for (let i = 0; i < n; i++) {
        if (!chao(i) || chao(i - 1)) continue; // comeco de um trecho
        let j = i; while (j - i < n - 1 && chao(j + 1)) j++;
        if (j - i > melhor[1] - melhor[0]) melhor = [i, j];
      }
      if (melhor[1] <= melhor[0]) return 0;
      // z ABSOLUTO no espaco do clipe (nao relativo ao quadril): o corpo aplica o balanco do quadril do clipe E a
      // translacao da rota, entao o pe fica parado no mundo quando a rota anda o que a ponta recua no clipe
      const zr = (i) => { const m = ((i % n) + n) % n; return pes[k][m * 3 + 2]; };
      // trecho que cruza o fim do clipe: o clipe em laco recomeca no mesmo lugar, entao soma por quadro
      let dz = 0; for (let i = melhor[0] + 1; i <= melhor[1]; i++) { const a = ((i - 1) % n + n) % n, b = (i % n + n) % n; if (b === 0) continue; dz += zr(b) - zr(a); }
      return -dz / ((melhor[1] - melhor[0]) * dur / (n - 1));
    });
    const vz = [(velPe[0] + velPe[1]) / 2];
    const desloc = Math.hypot(quadril[(n - 1) * 3] - quadril[0], quadril[(n - 1) * 3 + 2] - quadril[2]);
    // fase em que os pes se cruzam (menor distancia em z): o andar comeca dai, perto da pose parada, sem arrastar o pe na entrada
    let fase0 = 0, menor = Infinity;
    for (let i = 0; i < n; i++) { const dz = Math.abs(pes[0][i * 3 + 2] - pes[1][i * 3 + 2]); if (dz < menor) { menor = dz; fase0 = (i / (n - 1)) * dur; } }
    saida[cfg.prefixo + clip.name] = { nome: cfg.prefixo + clip.name, fonte: cfg.prefixo ? "kaykit" : "m2m", dur, n, ossos, quadril, pes, vel: vz[0], velPe, desloc, fase0, pisos: piso };
  }
  saida._ms = performance.now() - t0; saida._esc = esc;
  return saida;
}

// .vrma (Clawatar) -> clipe no mesmo formato
export async function carregarVRMA(url, vrm, nome = null) {
  const loader = new GLTFLoader(); loader.register((p) => new VRMAnimationLoaderPlugin(p));
  const g = await loader.loadAsync(url);
  const va = g.userData.vrmAnimations && g.userData.vrmAnimations[0]; if (!va) throw new Error("sem vrmAnimations: " + url);
  const clip = createVRMAnimationClip(va, vrm), h = vrm.humanoid;
  const porNome = {}; for (const b of Object.keys(h.humanBones)) { const n = h.getNormalizedBoneNode(b); if (n) porNome[n.name] = b; }
  const restHips = h.getNormalizedBoneNode("hips").position.clone();
  const dur = clip.duration, n = Math.max(2, Math.round(dur * QPS) + 1), ossos = {}; let quadril = null;
  for (const tr of clip.tracks) {
    const [no, prop] = tr.name.split("."), b = porNome[no]; if (!b) continue;
    const it = tr.createInterpolant();
    if (prop === "quaternion") {
      const a = (ossos[b] = new Float32Array(n * 4));
      for (let i = 0; i < n; i++) { const v = it.evaluate((i / (n - 1)) * dur); const q = new THREE.Quaternion().fromArray(v).normalize(); a.set([q.x, q.y, q.z, q.w], i * 4); }
    } else if (prop === "position" && b === "hips") {
      quadril = new Float32Array(n * 3);
      for (let i = 0; i < n; i++) { const v = it.evaluate((i / (n - 1)) * dur); quadril.set([v[0] - restHips.x, v[1] - restHips.y, v[2] - restHips.z], i * 3); }
    }
  }
  return { nome: nome || url.split("/").pop(), fonte: "vrma", dur, n, ossos, quadril, pes: null, vel: 0, desloc: 0 };
}

// amostra o clipe no tempo tc (s): escreve em qOut (por osso) e devolve o deslocamento do quadril
const _a = new THREE.Quaternion(), _b = new THREE.Quaternion();
export function amostrar(clipe, tc, cada) {
  const u = THREE.MathUtils.clamp(tc / clipe.dur, 0, 1) * (clipe.n - 1), i = Math.min(clipe.n - 2, Math.floor(u)), f = u - i;
  for (const [b, arr] of Object.entries(clipe.ossos)) {
    _a.fromArray(arr, i * 4); _b.fromArray(arr, (i + 1) * 4); cada(b, _a.slerp(_b, f));
  }
  if (!clipe.quadril) return null;
  const q = clipe.quadril, j = i * 3, k = (i + 1) * 3;
  return [q[j] + (q[k] - q[j]) * f, q[j + 1] + (q[k + 1] - q[j + 1]) * f, q[j + 2] + (q[k + 2] - q[j + 2]) * f];
}
