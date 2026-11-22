// celia-3d-corpo.js — corpo animado da Celia 3D (VRM 1.0 via three-vrm). Kurt, 2026-10-06.
// Herda do 2D (celia-motion-director.js) a MESMA tabela PRESETS e os MESMOS perfis de emocao (teste de paridade
// compara os nomes). Ganha o que o 2D nao tem: bracos, maos, pernas, gestos e acoes de corpo inteiro (ate o mortal).
// Dono unico: este modulo e o UNICO escritor dos ossos normalizados e das keys do rosto fora do bind.
// Ordem por quadro: corpo.update(dt) -> vrm.update(dt) -> corpo.aplicarRosto().
import * as THREE from "three";
import { VRMSpringBoneCollider, VRMSpringBoneColliderShapeCapsule, VRMSpringBoneColliderShapePlane } from "@pixiv/three-vrm";
import { amostrar } from "./celia-3d-clipes.js?v=clipes20261006i";

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const rnd = (a, b) => a + Math.random() * (b - a);
const DEG = Math.PI / 180;
const minimumJerk = (v) => { const t = clamp(v, 0, 1); return t * t * t * (10 + t * (-15 + t * 6)); };
const smoothStep = (v) => { const t = clamp(v, 0, 1); return t * t * (3 - 2 * t); };
const PROCEDURAL_SPEED = 0.82;
const VERTICAL_PROCEDURAL_SPEED = 0.62; // igual ao 2D
const FALA_CORPO_TAU = 0.35;   // s: o corpo segue a energia da fala suavizada, nunca silaba a silaba (tremia)
const CABECA_OLHAR_TAU = 0.4;  // s: os olhos saltam rapido; a cabeca vai atras devagar (antes 70 ms: ~200 graus/s)
// BOCA (web/p27_lingua.py, p29_boca_opcoes.py): no unlit a lingua e uma mancha rosa chapada; com a boca aberta (aa
// 0.85, target_17) ela enche a boca e passa do labio -> "lingua pra fora" (o Pai reprovou). A fala abre ate BOCA_MAX
// e a lingua fica no tom do interior da boca (LINGUA_TOM), como boca de anime.
const BOCA_MAX = 0.5;
// keys que DESENHAM boca (atlas p48/p51). Cada uma ja e uma boca inteira: nunca duas ao mesmo tempo.
// target_101 (sorriso pequeno) desenha boquinha + curva = duas bocas: banido; no lugar, target_105 (sorriso aberto).
const BOCAS_EMOCAO = ["target_93", "target_105", "target_91", "target_62", "target_35", "target_17", "target_58", "target_2", "target_21", "target_11", "target_56"];
const LINGUA_TOM = 0.28;
const PRESET_TRANSITION_S = 0.9;
const EMOTION_TRANSITION_S = 1.45;

// ---- Tabela herdada do 2D, valores identicos (fonte: celia-motion-director.js, PRESETS) ----
function mp(category, breathAmp, breathFreq, nodAmp, nodFreq, swayAmp, swayFreq,
            rollAmp, rollFreq, bobAmp, shoulderAmp, hairAmp, hairFreq, chaos = 0) {
  return { category, breathAmp, breathFreq, nodAmp, nodFreq, swayAmp, swayFreq,
    rollAmp, rollFreq, bobAmp, shoulderAmp, hairAmp, hairFreq, chaos };
}
export const PRESETS = Object.freeze({
  "Idle Base": mp("Idle",3.4,.90,1.5,1.10,5.5,.55,1.9,.36,10,7,7,.90),
  "Idle Suave": mp("Idle",2.4,.70,1,.85,3.8,.42,1.4,.28,7.5,5,5,.70),
  "Idle Lento": mp("Idle",3,.45,1.2,.60,5,.30,1.7,.20,9,6,6,.45),
  "Idle Ativo": mp("Idle",4.2,1.20,2.2,1.40,6.8,.75,2.5,.48,13,9,9.5,1.20,.15),
  "Idle Amplo": mp("Idle",6,.85,2.8,1.05,8.5,.60,3.5,.38,15,10,11,.85,.10),
  "Idle Float": mp("Idle",2.6,.62,1.4,.78,4.5,.48,2.2,.31,9.5,5.5,7.5,.62),
  "Idle Desequilibrado": mp("Idle",3.2,.77,1.8,1.33,6,.53,2.6,.67,11,6.5,7,.88,.30),
  "Idle Maré": mp("Idle",3.8,.58,1.5,.72,7.5,.40,2.8,.25,12.5,9,8.5,.58),
  "Idle Arena": mp("Idle",4.5,1.10,2.5,1.35,7.5,.82,2.8,.52,14,10,10.5,1.35,.20),
  "Idle Âncora": mp("Idle",2.8,.65,1.2,.80,7,.45,2.5,.28,11.5,8.5,7,.65),
  "Nod Bounce": mp("Aceno",2.6,.85,7,1.35,5,.62,1.8,.40,13,7.5,12,1.35,.15),
  "Nod Entusiasta": mp("Aceno",2.6,.80,7.5,1.60,5,.55,1.6,.35,13.5,7,13,1.60,.15),
  "Nod Duplo": mp("Aceno",1.6,.60,5.5,2.10,3,.42,1.2,.28,9.5,4.5,8.5,2.10),
  "Nod Triplo": mp("Aceno",2,.65,6,2.80,3.5,.45,1.3,.30,10,5,9.5,2.80,.10),
  "Nod Ultra": mp("Aceno",3,.90,9,1.80,5.5,.65,2,.42,15,8,14,1.80,.20),
  "Nod Groove": mp("Aceno",2.5,1,6.5,1.50,5.5,.78,1.8,.50,12,7,11,1.50,.18),
  "Nod Trance": mp("Aceno",2.5,.70,6,1.10,5,.52,3,.35,11,6.5,10,1.10,.10),
  "Nod Pesado": mp("Aceno",2.8,.60,8.5,.90,4.5,.45,2.2,.28,14,7,12,.90,.10),
  "Sway Relaxado": mp("Balanço",3.2,.50,1.4,.62,10,.38,3.5,.24,12.5,10,7.5,.50),
  "Sway Dançante": mp("Balanço",2.6,.90,2,1.15,9.5,.72,2.8,.48,12,10,9.5,1.15,.15),
  "Sway Metrônomo": mp("Balanço",1.4,.70,.7,.70,9,.70,1.5,.35,7.5,9.5,5,.70),
  "Sway Clube": mp("Balanço",2.5,.95,1.8,1.20,9.5,.85,2.5,.55,11.5,10,9,1.20,.15),
  "Sway Arco Amplo": mp("Balanço",2.8,.68,1.5,.85,13,.52,4,.34,13.5,13,9.5,.85,.10),
  "Sway Navio": mp("Balanço",3.5,.45,1,.55,12,.32,5,.20,14,12,8.5,.45),
  "Sway Acalanto": mp("Balanço",2.5,.40,.9,.50,11,.28,4,.18,12,11,8,.40),
  "Sway Lento Dramático": mp("Balanço",3.5,.55,1.5,.68,12.5,.40,5,.25,14,12.5,9,.55,.12),
  "Roll Fluida": mp("Rotação",2.6,.65,1.6,.80,4.8,.45,7.5,.32,10,5,7,.80),
  "Roll Dramática": mp("Rotação",3.5,.72,2.2,.88,5.5,.50,11,.38,12.5,5.5,9,.88,.15),
  "Roll Tilt Extremo": mp("Rotação",2,.55,1.5,.68,3.5,.40,12,.25,8,3.5,6,.68),
  "Roll Bebum": mp("Rotação",4,.68,2.5,.85,7,.50,9,.42,12,7.5,8.5,.85,.40),
  "Roll Pescoço": mp("Rotação",2,.85,1.5,1.05,4,.62,9,.52,9,4,7,1.05,.15),
  "Hair Sacudindo": mp("Cabelo",3.2,1.10,3.2,1.35,6.5,.80,2.4,.52,12,6.5,16,1.35,.20),
  "Hair Pesado": mp("Cabelo",3,.55,2.8,.70,6,.40,2.5,.25,11,6,13,.55),
  "Hair Tempestade": mp("Cabelo",4,1.20,4,1.50,7.5,.90,3,.60,14,7.5,18,1.50,.25),
  "Hair Cascata": mp("Cabelo",2.4,.48,2,.60,5.5,.35,2.8,.22,10.5,6,13.5,.48),
  "Hair Chicote": mp("Cabelo",3.5,1.30,4.5,1.60,6.5,.95,2.5,.62,13,6.5,17.5,1.60,.25),
  "Hair Ultra": mp("Cabelo",4.5,1.25,5,1.55,8,.95,3,.62,16,8,20,1.55,.30),
  "Bob Ondular": mp("Corpo",2.6,.65,1.4,.80,5.5,.48,2.8,.30,14,7,7.5,.65),
  "Bob Saltitante": mp("Corpo",3.6,1.05,2.8,1.25,6,.78,2.2,.50,18,9,11,1.25,.12),
  "Bob Lento": mp("Corpo",2.4,.42,1.2,.52,4,.30,2,.19,14.5,8,6,.42),
  "Bob Terremoto": mp("Corpo",5,1.10,4,1.35,7,.82,3,.55,22,12,14,1.35,.30),
  "Bob Pistão": mp("Corpo",3,1.15,2,1.40,4.5,.85,1.6,.58,17,8,9,1.40,.10),
  "Excited Pop": mp("Combo",4,1.20,5,1.50,9,.90,3,.60,17,9.5,15,1.50,.22),
  "Confident Stride": mp("Combo",3,.90,4,1.15,7.5,.68,2.5,.45,14.5,8.5,10.5,1.15,.10),
  "Dance Light": mp("Combo",3.5,1,3.5,1.25,8.5,.78,2.8,.50,15,9.5,13,1.25,.18),
  "Drunk Mode": mp("Combo",4.5,.65,3.5,.83,10,.50,6,.40,14.5,10.5,10,.83,.40),
  "Dramatic Enter": mp("Combo",5,.78,5.5,.95,9.5,.58,4.5,.38,18,11,16.5,.95,.25),
  "Nervous Wreck": mp("Combo",2,1.50,1.8,1.80,3.5,1.10,1.5,.80,7.5,3.5,4.5,1.80,.30),
  "Arena Hype": mp("Combo",5,1.25,6,1.55,10,.95,3.5,.62,20,11.5,17,1.55,.30),
  "Possessão": mp("Combo",4,.90,3.5,1.15,8,.68,7,.55,13,7.5,12,1.15,.45),
  "Ultra Vibe": mp("Combo",4.5,1.15,5.5,1.40,9.5,.88,3.5,.58,19,11,16,1.40,.25),
  "Tempestade": mp("Combo",5.5,1.10,5,1.35,9,.82,4.5,.55,20,11.5,17.5,1.35,.35),
  "Full Send": mp("Combo",6,1.30,7,1.60,10.5,1,4,.65,22,13,18.5,1.60,.35),
  "Onda Total": mp("Combo",4,.85,4.5,1.05,9.5,.65,4.5,.42,17.5,10.5,14.5,1.05,.20),
  "Final Épico": mp("Combo",6.5,1,6.5,1.25,11,.75,5,.50,22,13,20,1.25,.30),
});

// ---- Perfis herdados do 2D (mesmos nomes, amp/energy/pools/posture identicos). "corpo" e "rosto" sao do 3D. ----
// rosto: keys do rosto (dicionario_v1.json, poses NAO aprovadas pelo Pai — D5). corpo: postura de bracos/maos.
export const PROFILES = Object.freeze({
  neutral: { amp:1.18, energy:.60, idle:["Idle Ativo","Idle Amplo","Sway Dançante","Bob Ondular","Roll Fluida","Hair Sacudindo","Dance Light"], speaking:["Sway Dançante","Bob Saltitante","Nod Bounce","Roll Pescoço","Hair Sacudindo","Dance Light"], reaction:["Idle Ativo","Roll Fluida","Hair Sacudindo"], posture:{x:0,y:0,z:0,body:0},
    rosto:{}, corpo:{braco:0, cotovelo:0, punho:.25, frente:0} },
  happy: { amp:1.65, energy:.96, idle:["Ultra Vibe","Excited Pop","Dance Light","Bob Saltitante","Sway Dançante","Full Send"], speaking:["Ultra Vibe","Excited Pop","Nod Ultra","Dance Light","Full Send"], reaction:["Excited Pop","Nod Ultra","Full Send","Final Épico"], posture:{x:0,y:3,z:0,body:1.2},
    rosto:{target_93:.75, target_46:.45}, corpo:{braco:.12, cotovelo:.15, punho:.2, frente:.1} },
  in_love: { amp:.68, energy:.58, idle:["Sway Acalanto","Sway Relaxado","Idle Float","Roll Fluida"], speaking:["Sway Acalanto","Roll Fluida","Idle Float"], reaction:["Roll Fluida"], posture:{x:0,y:1.5,z:1.2,body:.6},
    rosto:{target_105:.55, target_74:.35}, corpo:{braco:-.05, cotovelo:.5, punho:.3, frente:.35} },
  sad: { amp:.44, energy:.18, idle:["Idle Lento","Sway Lento Dramático","Bob Lento","Hair Cascata"], speaking:["Idle Lento","Bob Lento","Nod Pesado"], reaction:["Nod Pesado"], posture:{x:0,y:-4,z:-.8,body:-1},
    rosto:{target_74:.65, target_99:.35, target_62:.75, target_116:.35}, corpo:{braco:-.12, cotovelo:-.1, punho:.15, frente:.05} },
  angry: { amp:.62, energy:.72, idle:["Idle Âncora","Nod Pesado","Confident Stride"], speaking:["Idle Âncora","Nod Pesado","Confident Stride"], reaction:["Nod Pesado"], posture:{x:0,y:1,z:0,body:1.5},
    rosto:{target_31:.95, target_58:.6, target_91:.55}, corpo:{braco:.1, cotovelo:.25, punho:1.1, frente:0} },
  surprised: { amp:1.00, energy:.86, idle:["Idle Ativo","Idle Base"], speaking:["Nod Ultra","Bob Saltitante","Excited Pop"], reaction:["Dramatic Enter","Nod Ultra"], posture:{x:0,y:4,z:0,body:-.5},
    rosto:{target_43:.85, target_108:.85, target_35:.35}, corpo:{braco:.22, cotovelo:.35, punho:.05, frente:.2} },
  confused: { amp:.58, energy:.42, idle:["Idle Desequilibrado","Roll Tilt Extremo","Idle Suave"], speaking:["Roll Bebum","Roll Tilt Extremo","Nervous Wreck"], reaction:["Roll Tilt Extremo"], posture:{x:0,y:0,z:1.4,body:.2},
    rosto:{target_31:.35, target_108:.3}, corpo:{braco:0, cotovelo:.2, punho:.3, frente:.1} },
  scared: { amp:.55, energy:.62, idle:["Nervous Wreck","Idle Desequilibrado","Bob Lento"], speaking:["Nervous Wreck","Hair Chicote","Bob Pistão"], reaction:["Nervous Wreck"], posture:{x:0,y:2,z:-.7,body:-1.5},
    rosto:{target_43:.6, target_108:.6, target_91:.45}, corpo:{braco:-.2, cotovelo:.9, punho:.8, frente:.5} },
  teasing: { amp:1.45, energy:.90, idle:["Confident Stride","Sway Dançante","Roll Pescoço","Dance Light","Ultra Vibe"], speaking:["Confident Stride","Roll Pescoço","Dance Light","Ultra Vibe"], reaction:["Roll Pescoço","Dramatic Enter"], posture:{x:0,y:1,z:1.4,body:1.4},
    rosto:{target_105:.6, target_46:.25, target_74:.25}, corpo:{braco:.05, cotovelo:.1, punho:.3, frente:0} },
  funny: { amp:1.55, energy:.94, idle:["Bob Saltitante","Sway Dançante","Nod Bounce","Ultra Vibe","Full Send"], speaking:["Bob Saltitante","Nod Bounce","Dance Light","Excited Pop","Full Send"], reaction:["Bob Saltitante","Nod Ultra","Final Épico"], posture:{x:0,y:1,z:.8,body:.9},
    rosto:{target_93:.85, target_48:.6}, corpo:{braco:.1, cotovelo:.2, punho:.25, frente:.1} },
  holding_back_laughter: { amp:.65, energy:.55, idle:["Idle Desequilibrado","Bob Ondular","Nod Duplo"], speaking:["Nod Duplo","Bob Ondular","Sway Relaxado"], reaction:["Nod Duplo"], posture:{x:0,y:0,z:.7,body:.3},
    rosto:{target_105:.4, target_74:.3}, corpo:{braco:0, cotovelo:.4, punho:.5, frente:.25} },
  passionate: { amp:.90, energy:.72, idle:["Idle Amplo","Onda Total","Roll Dramática"], speaking:["Onda Total","Dramatic Enter","Roll Dramática"], reaction:["Dramatic Enter"], posture:{x:0,y:2,z:.6,body:1.2},
    rosto:{target_105:.55, target_46:.5}, corpo:{braco:.15, cotovelo:.3, punho:.2, frente:.25} },
});
export const EMOTION_ALIASES = Object.freeze({
  trol_idle: "teasing", trol_speaking: "teasing", excited: "happy",
  calm: "neutral", nervous: "scared", confident: "teasing",
});

// keys que ja tem bind de expressao VRM: o three-vrm e o dono delas; aqui viram peso de expressao.
const BOUND = { target_6:"blink", target_2:"aa", target_3:"ih", target_67:"ou", target_12:"ee", target_35:"oh",
  target_113:"lookLeft", target_114:"lookRight", target_115:"lookUp", target_116:"lookDown" };

const OSSOS = ["hips","spine","chest","upperChest","neck","head",
  "leftUpperArm","leftLowerArm","leftHand","rightUpperArm","rightLowerArm","rightHand",
  "leftUpperLeg","leftLowerLeg","leftFoot","rightUpperLeg","rightLowerLeg","rightFoot"];
const DEDOS = ["Index","Middle","Ring","Little"];
const FALANGES = ["Proximal","Intermediate","Distal"];

// ---- convencoes do rig normalizado (VRM 1.0: frente +Z, esquerda dela em +X), provadas por medicao na bancada ----
// leftUpperArm.z < 0 baixa o braco; rightUpperArm.z > 0 baixa. lowerArm.y: esquerdo < 0 / direito > 0 dobra p/ frente.
// head.x > 0 olha p/ baixo; head.y > 0 vira p/ esquerda dela; spine.x > 0 inclina p/ frente.
// upperLeg.x < 0 joelho p/ frente; lowerLeg.x > 0 dobra o joelho; hips: rotacao em X negativa = mortal PRA TRAS.

// pose = { osso: [x,y,z] } em radianos, somada por camadas.
function vazio() { const p = {}; for (const o of OSSOS) p[o] = [0, 0, 0]; return p; }
function soma(p, osso, x, y, z, w = 1) { const v = p[osso] || (p[osso] = [0, 0, 0]); v[0] += x * w; v[1] += y * w; v[2] += z * w; }

// Agachar mantendo os pes no chao: dada a descida d (m) devolve os angulos de coxa, joelho e pe.
const COXA = 0.41, CANELA = 0.40;
function agachar(d) {
  const D = clamp(COXA + CANELA - d, 0.35, COXA + CANELA - 1e-4);
  const cb = clamp((D * D - COXA * COXA - CANELA * CANELA) / (2 * COXA * CANELA), -1, 1);
  const beta = Math.acos(cb);
  const alfa = Math.asin(clamp(CANELA * Math.sin(beta) / D, -1, 1));
  return { coxa: -alfa, joelho: beta, pe: alfa - beta };
}
function pernas(p, d, w = 1, lado = 0) {
  const a = agachar(Math.max(0, d));
  for (const s of ["left", "right"]) {
    const extra = lado && ((s === "left") === (lado > 0)) ? 1.25 : (lado ? 0.8 : 1);
    soma(p, s + "UpperLeg", a.coxa * extra, 0, 0, w);
    soma(p, s + "LowerLeg", a.joelho * extra, 0, 0, w);
    soma(p, s + "Foot", a.pe * extra, 0, 0, w);
  }
}
// DEDOS: os ossos do gerador ficavam ~6 cm acima da mao e o dedo medio nem estava no esqueleto (p57/p59/p60); dobrar
// rasgava a malha. celia-3d-maos.js reencaixa os ossos dentro dos dedos e refaz os pesos na carga. Medido depois
// (p58, arestas da mao): curva 0.2 -> maior esticada 4.4 mm (antes 29.5); 0.45 -> 11.7 (antes 69.6); 0.8 ainda rasga a
// ponta do mindinho (20 mm). Teto DEDOS_LIMITE. Mao relaxada e escalonada (indicador menos, mindinho mais), com um
// mexer lento quando `t` vem (vida); `retos` deixa dedos esticados (o V da paz), `polegar` fecha o polegar.
// Rodada 6: a mao e a TRANSPLANTADA da doadora pixiv (celia-3d-mao-doada.js, aneis nas juntas): o punho fecha inteiro sem rasgar (p71).
// (A mao modelada em codigo, celia-3d-mao-nova.js, foi reprovada pelo Pai e esta em quarentena.)
// O teto sobe para 1.5; com ?maoantiga (controle) a mao velha volta a rasgar acima de ~0.8, como medido.
const DEDOS_LIMITE = 1.5;
const ESCALONADO = { Index: -0.06, Middle: 0, Ring: 0.05, Little: 0.1 };
function maos(dedos, lado, curva, retos = [], polegar = null, t = null, teto = DEDOS_LIMITE) {
  const s = lado === "left" ? -1 : 1;
  DEDOS.forEach((d, k) => {
    let c = retos.includes(d) ? 0 : curva + (curva > 0.05 ? ESCALONADO[d] : 0);
    if (t !== null && !retos.includes(d)) c += 0.04 * Math.sin(t * 0.8 + k * 1.1 + (s > 0 ? 0.7 : 0));
    c = clamp(c, 0, teto);
    FALANGES.forEach((f, i) => { dedos[lado + d + f] = [0, 0, s * c * (i === 0 ? 1.0 : 1.2)]; });
  });
  const pc = clamp(polegar === null ? curva * 0.5 : polegar, 0, teto);
  dedos[lado + "ThumbMetacarpal"] = [0, s * pc * 0.3, s * pc * 0.2];
  dedos[lado + "ThumbProximal"] = [0, s * pc * 0.5, s * pc * 0.3]; dedos[lado + "ThumbDistal"] = [0, s * pc * 0.5, s * pc * 0.3];
}
// Punho: girar a mao estica o antebraco (0.3 rad em x/z = 21-29 mm; em y so 7 mm). Teto aplicado no fim de update().
const PUNHO_XZ = 0.1, PUNHO_Y = 0.3;
const OLHAR_LADO = 0.1, OLHAR_BAIXO = 0.25; // ver update(): tetos medidos do olhar (p18)

// Braco em valor ABSOLUTO a partir do repouso relaxado: r 0 = solto ao lado, 1 = acima da cabeca;
// tras > 0 leva o braco para tras (impulso), frente > 0 para a frente. Nunca cai em T-pose.
// 62 graus abaixo da horizontal: com 70 o ombro esticava 42 mm (p14); aqui fica perto de 37 mm, ainda bracos soltos.
let BRACO_SOLTO = 1.08; // so a bancada muda (opcoes.bracoSolto), para comparar o ombro em prova
function bracoAbs(p, lado, r, tras = 0, frente = 0, w = 1) {
  const s = lado === "left" ? -1 : 1;
  soma(p, lado + "UpperArm", tras * 0.7 - frente * 0.9, s * (0.12 + frente * 0.35), s * (BRACO_SOLTO - r * 2.75), w);
}

// ---- Acoes e gestos: funcoes de tempo (s) -> pose. Cada uma declara duracao, ossos que usa e envelope. ----
// u = progresso 0..1; e = envelope de entrada/saida (crossfade com o procedural).
const env = (t, dur, ent = 0.3, sai = 0.4) => minimumJerk(t / ent) * (1 - minimumJerk((t - (dur - sai)) / sai));
// Angulos de braco ACHADOS por busca no rig COM a regua de pele (web/p15_busca_limpa.py): so a dobra real do cotovelo
// (eixo y) e antebraco_z no lado limpo (<= 0.4). Torcer o antebraco "para cima" entortava a pele ate 55 mm (p14).
// Valores para o lado DIREITO; o esquerdo espelha y e z.
function bracoLimpo(p, lado, ang, w) {
  const s = lado === "right" ? 1 : -1;
  soma(p, lado + "UpperArm", ang[0], s * ang[1], s * ang[2], w);
  soma(p, lado + "LowerArm", 0, s * ang[3], s * ang[4], w);
}
function bracoAcenando(p, dd, t, w, lado = "right") {
  // mao erguida ao lado da cabeca (erro 24 mm; antebraco 7 mm, ombro = repouso). O tchau balanca o braco inteiro.
  const onda = Math.sin(t * Math.PI * 2 * 2.4), s = lado === "right" ? 1 : -1;
  // rodada 15 ("no tchauzinho o braco distorce todinho"): a antiga [-1.4, 0.2, -0.218, 1.4, 0.4] torcia o umero 77 graus; esta 49, mao 1,8 cm do lugar antigo
  bracoLimpo(p, lado, [-0.9, 0.2, -0.418 + onda * 0.12, 1.2 + onda * 0.15, 0.15], w);
  soma(p, lado + "Hand", 0, s * onda * 0.25, 0, w);
  maos(dd, lado, 0);
}
export const ACOES = {
  acenar: { dur: 2.8, tipo: "gesto", ossos: ["rightUpperArm","rightLowerArm","rightHand"], rosto: {target_93:.7, target_46:.5},
    pose(p, dd, t, w) {
      bracoAcenando(p, dd, t, w);
      soma(p, "head", 0.05, -0.12, -0.12, w); soma(p, "upperChest", 0, -0.08, 0.04, w);
    } },
  paz: { dur: 2.8, tipo: "gesto", ossos: ["rightUpperArm","rightLowerArm","rightHand"], rosto: {target_105:.6, target_46:.35},
    pose(p, dd, t, w) {
      // V de paz do lado do rosto, mao de pe e palma pra camera (busca web/p62_busca_v.py: dedos 86% pra cima, palma 87%,
      // 12 mm do alvo ao lado do rosto, pele do braco 55 mm contra 67 da pose antiga). Dedos reencaixados: o V nao rasga (p58).
      // rodada 15 ("no V o braco fica todo afinado"): a pose antiga [-2.4, -0.6, 0, 2.8, 0.4] girava o umero 137 graus no proprio eixo (embrulho de bala).
      // Nova, achada por busca na bancada: giro do umero 74 graus, mao 2,6 cm do lugar antigo, dedos 1,2 graus da direcao antiga.
      bracoLimpo(p, "right", [-1.3, -0.1, 0.75, 2.55, -0.35], w); soma(p, "rightHand", 0, -0.3, 0, w);
      maos(dd, "right", 1.3, ["Index", "Middle"], 1.0); // anelar e mindinho fechados de verdade, polegar por cima (mao nova)
      soma(p, "head", 0.06, -0.1, -0.22, w); soma(p, "spine", 0, 0, -0.06, w);
    } },
  // "pensativa" SEM encostar a mao: quatro poses de mao no queixo medidas (p6/p15) e todas atravessavam o busto ou o
  // pescoco nesta malha (manga entrando no peito). Fica a cabeca, o olhar e a boca; bracos so mais juntos ao corpo.
  pensar: { dur: 3.4, tipo: "gesto", ossos: ["leftUpperArm","rightUpperArm","leftLowerArm","rightLowerArm"], rosto: {target_108:.35, target_31:.2}, olhar: [0.6, 0.25],
    pose(p, dd, t, w) {
      const k = Math.sin(t * 1.6);
      bracoAbs(p, "left", -0.03, 0, 0.18, w); bracoAbs(p, "right", -0.03, 0, 0.18, w);
      soma(p, "leftLowerArm", 0, -0.55, 0, w); soma(p, "rightLowerArm", 0, 0.55, 0, w);
      soma(p, "head", -0.06, 0.16 + 0.04 * k, 0.2, w); soma(p, "neck", 0, 0.06, 0.05, w); soma(p, "spine", 0, 0.06 * k, 0.04, w);
    } },
  comemorar: { dur: 2.8, tipo: "gesto", ossos: ["leftUpperArm","rightUpperArm","leftLowerArm","rightLowerArm","leftHand","rightHand"], rosto: {target_93:.9, target_48:.9},
    pose(p, dd, t, w) {
      // "eba!": punhos fechados na altura do ombro e socos alternados para o alto, com pulinhos no ritmo.
      // So o eixo real do cotovelo (y) e o braco em valor absoluto (p14/p15); o punho fecha ate o teto medido (p58).
      const f = t * Math.PI * 2 * 1.3, a = 0.5 + 0.5 * Math.sin(f), b = 0.5 + 0.5 * Math.sin(f + Math.PI);
      bracoAbs(p, "left", 0.42 + 0.42 * a, 0, 0.3, w); bracoAbs(p, "right", 0.42 + 0.42 * b, 0, 0.3, w);
      soma(p, "leftLowerArm", 0, -(1.55 - 1.15 * a), 0, w); soma(p, "rightLowerArm", 0, 1.55 - 1.15 * b, 0, w);
      maos(dd, "left", 0.55, [], 0.55); maos(dd, "right", 0.55, [], 0.55);
      const pulo = Math.max(0, Math.sin(f * 2));
      soma(p, "head", -0.1, 0.12 * Math.sin(f), 0.06 * Math.sin(f), w); soma(p, "upperChest", -0.06, 0.08 * Math.sin(f), 0, w);
      this._quadril = { y: pulo * 0.035 * w, agacha: (1 - pulo) * 0.02 * w };
    } },
  timida: { dur: 3.2, tipo: "gesto", ossos: ["leftUpperArm","rightUpperArm","leftLowerArm","rightLowerArm","leftHand","rightHand"], rosto: {target_105:.4, target_74:.45}, olhar: [-0.5, -0.6],
    pose(p, dd, t, w) {
      bracoAbs(p, "left", -0.04, 0, 0.32, w); bracoAbs(p, "right", -0.04, 0, 0.32, w);
      soma(p, "leftUpperArm", 0, -0.2, 0, w); soma(p, "rightUpperArm", 0, 0.2, 0, w);
      soma(p, "leftLowerArm", 0, -1.25, 0, w); soma(p, "rightLowerArm", 0, 1.25, 0, w);
      maos(dd, "left", 0.5); maos(dd, "right", 0.5);
      soma(p, "head", 0.22, -0.2, 0.18, w); soma(p, "spine", 0.06, 0.1, 0, w);
      soma(p, "hips", 0, 0.12 * Math.sin(t * 2.2), 0, w);
    } },
  ajeitar_cabelo: { dur: 2.4, tipo: "micro", ossos: ["leftUpperArm","leftLowerArm","leftHand"], rosto: {}, olhar: [-0.3, 0.1],
    pose(p, dd, t, w) {
      const u = clamp((t - 0.5) / 1.2, 0, 1);
      // mao na lateral da cabeca (p15: erro 19 mm, antebraco 12 mm; a versao antiga torcia o antebraco: 54 mm). Desliza pela dobra.
      bracoLimpo(p, "left", [-1.4, 0.4, -0.409, 2.2 - Math.sin(u * Math.PI) * 0.2, 0], w);
      soma(p, "leftHand", 0, -0.2, 0, w); maos(dd, "left", 0);
      soma(p, "head", 0.04, 0.1, 0.16, w);
    } },
  olhar_em_volta: { dur: 3.0, tipo: "micro", ossos: [], rosto: {},
    pose(p, dd, t, w) {
      const k = Math.sin(t / 3.0 * Math.PI * 2);
      soma(p, "head", 0, 0.45 * k, 0.05 * k, w); soma(p, "neck", 0, 0.15 * k, 0, w); soma(p, "upperChest", 0, 0.1 * k, 0, w);
      this._olhar = [k * 0.9, 0.1];
    } },
  pulinho: { dur: 1.5, tipo: "acao", cabelo: 0.6, ossos: ["hips","leftUpperLeg","rightUpperLeg","leftLowerLeg","rightLowerLeg","leftUpperArm","rightUpperArm"], rosto: {target_93:.8, target_46:.6},
    pose(p, dd, t, w) {
      // 0-.35 agacha, .35-.75 voo, .75-1.1 pouso, 1.1-1.5 levanta
      let ag = 0, voo = 0;
      if (t < 0.35) ag = 0.12 * minimumJerk(t / 0.35);
      else if (t < 0.75) { const u = (t - 0.35) / 0.4; voo = 0.22 * Math.sin(u * Math.PI); ag = 0.12 * (1 - minimumJerk(u * 3)); }
      else if (t < 1.1) ag = 0.1 * Math.sin(((t - 0.75) / 0.35) * Math.PI / 2);
      else ag = 0.1 * (1 - minimumJerk((t - 1.1) / 0.4));
      const bracos = t < 0.35 ? -minimumJerk(t / 0.35) : (t < 0.75 ? 1 : 1 - minimumJerk((t - 0.75) / 0.5));
      bracoAbs(p, "left", 0.18 * Math.max(0, bracos), 0.75 * Math.max(0, -bracos), 0.55 * Math.max(0, bracos), w);
      bracoAbs(p, "right", 0.18 * Math.max(0, bracos), 0.75 * Math.max(0, -bracos), 0.55 * Math.max(0, bracos), w);
      soma(p, "spine", 0.25 * (ag / 0.12), 0, 0, w);
      this._quadril = { y: voo * w, agacha: ag * w };
    } },
  giro: { dur: 1.8, tipo: "acao", cabelo: 0.7, ossos: ["hips","leftUpperArm","rightUpperArm"], rosto: {target_93:.7, target_48:.5},
    pose(p, dd, t, w) {
      const u = clamp((t - 0.25) / 1.25, 0, 1);
      bracoAbs(p, "left", 0.14, 0, 0.1, w); bracoAbs(p, "right", 0.14, 0, 0.1, w);
      maos(dd, "left", 0.15); maos(dd, "right", 0.15);
      const pulo = Math.sin(u * Math.PI);
      this._quadril = { y: 0.07 * pulo * w, agacha: (t < 0.25 ? 0.06 * minimumJerk(t / 0.25) : 0.06 * (1 - minimumJerk(u * 4))) * w,
        giro: Math.PI * 2 * minimumJerk(u) };
    } },
  // ANDAR (pedido do Pai): vira de lado, anda 0,8 m, meia-volta passando de frente pra camera, volta e vira de frente.
  // Passo casado com a distancia percorrida (fase = distancia / passo): o pe nao escorrega. Nas viradas, passinhos.
  andar: { dur: 6.4, tipo: "acao", cabelo: 0.3, ossos: ["hips","spine","upperChest","leftUpperLeg","rightUpperLeg","leftLowerLeg","rightLowerLeg","leftFoot","rightFoot","leftUpperArm","rightUpperArm","leftLowerArm","rightLowerArm"], rosto: {target_105:.35},
    pose(p, dd, t, w) {
      // apoio: a perna varre LINEAR com a distancia (o pe fica parado no chao): 1.6*ALFA = PASSO (perna ~0.8 m).
      // balanco: joelho dobra e a perna volta para frente. p61 mede o pe apoiado escorregando.
      const D = 0.8, PASSO = 0.48, ALFA = PASSO / 1.6, VIRA = 0.16;
      const sm = (x) => smoothStep(clamp(x, 0, 1));
      // amplitude A continua: 1 no trecho reto (apoio linear so casa com o chao em A=1) e rampa so nas viradas,
      // onde o corpo nao translada. Degrau de A na troca de segmento fazia a perna pular 13 cm num quadro (p61).
      let yaw, x, dist, A;
      if (t < 0.6) { const u = t / 0.6; yaw = Math.PI / 2 * minimumJerk(u); x = 0; dist = VIRA * u; A = 0.35 + 0.65 * sm(u); }
      else if (t < 2.6) { const u = (t - 0.6) / 2.0; yaw = Math.PI / 2; x = D * sm(u); dist = VIRA + x; A = 1; }
      else if (t < 3.6) { const u = (t - 2.6) / 1.0; yaw = Math.PI / 2 - Math.PI * minimumJerk(u); x = D; dist = VIRA + D + VIRA * u; A = 1 - 0.65 * Math.sin(Math.PI * u) ** 2; }
      else if (t < 5.6) { const u = (t - 3.6) / 2.0; yaw = -Math.PI / 2; x = D * (1 - sm(u)); dist = 2 * VIRA + D + D * sm(u); A = 1; }
      else { const u = (t - 5.6) / 0.8; yaw = -Math.PI / 2 * (1 - minimumJerk(u)); x = 0; dist = 2 * VIRA + 2 * D + VIRA * u; A = 1 - sm(u); }
      const f = Math.PI * dist / PASSO, L = Math.sin(f), R = -L;
      for (const [lado, fase] of [["left", f], ["right", f + Math.PI]]) {
        const u = ((fase % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI) / Math.PI; // 0..1 apoio, 1..2 balanco
        let th, joelho;
        if (u < 1) { th = ALFA * (1 - 2 * u); joelho = 0.06; }
        else { const q = u - 1; th = ALFA * (-1 + 2 * smoothStep(q)); joelho = 0.06 + 0.75 * Math.sin(Math.PI * q); }
        th *= A; joelho *= A;
        soma(p, lado + "UpperLeg", -th, 0, 0, w);
        soma(p, lado + "LowerLeg", joelho, 0, 0, w);
        soma(p, lado + "Foot", th - joelho * 0.8, 0, 0, w); // sola paralela ao chao no apoio
      }
      // bracos em contra-fase com as pernas; cotovelo levemente dobrado
      bracoAbs(p, "left", 0.02, 0.25 * A * Math.max(0, L), 0.25 * A * Math.max(0, -L), w);
      bracoAbs(p, "right", 0.02, 0.25 * A * Math.max(0, R), 0.25 * A * Math.max(0, -R), w);
      soma(p, "leftLowerArm", 0, -0.35, 0, w); soma(p, "rightLowerArm", 0, 0.35, 0, w);
      maos(dd, "left", 0.25); maos(dd, "right", 0.25);
      soma(p, "hips", 0, 0.06 * A * L, 0.03 * A * L, w); soma(p, "spine", 0.03 * A, -0.05 * A * L, 0, w);
      soma(p, "upperChest", 0, -0.03 * A * L, 0, w);
      this._quadril = { giro: yaw * w, x: x * w, y: 0, agacha: (0.012 + 0.012 * A * (0.5 - 0.5 * Math.cos(2 * f))) * w };
    } },
  // ESPREGUICAR: bracos sobem por cima da cabeca, corpo arqueia para tras, olhos fechados e boquinha de bocejo.
  espreguicar: { dur: 3.6, tipo: "gesto", ossos: ["spine","upperChest","neck","head","leftUpperArm","rightUpperArm","leftLowerArm","rightLowerArm","leftHand","rightHand"], rosto: {target_48:.9, target_35:.3},
    pose(p, dd, t, w) {
      const e = minimumJerk(clamp(t / 1.2, 0, 1)) * (1 - minimumJerk(clamp((t - 2.6) / 1.0, 0, 1)));
      const treme = 0.03 * Math.sin(t * 9) * e; // o tremidinho do fim do espreguicar
      bracoAbs(p, "left", 0.92 * e, 0, 0.05 * e, w); bracoAbs(p, "right", 0.92 * e, 0, 0.05 * e, w);
      soma(p, "leftLowerArm", 0, -0.25 * e, 0, w); soma(p, "rightLowerArm", 0, 0.25 * e, 0, w);
      maos(dd, "left", 0.1 * e); maos(dd, "right", 0.1 * e);
      soma(p, "spine", -0.1 * e + treme, 0, 0, w); soma(p, "upperChest", -0.12 * e, 0, 0, w);
      soma(p, "neck", -0.1 * e, 0, 0, w); soma(p, "head", -0.12 * e, 0, 0.05 * e, w);
    } },
  dancinha: { dur: 5.2, tipo: "acao", cabelo: 0.3, ossos: ["hips","spine","leftUpperArm","rightUpperArm","leftLowerArm","rightLowerArm","leftUpperLeg","rightUpperLeg","leftLowerLeg","rightLowerLeg"], rosto: {target_93:.8, target_46:.4},
    pose(p, dd, t, w) {
      const bpm = 2.1, f = t * Math.PI * 2 * bpm / 2;
      const lado = Math.sin(f), bate = Math.abs(Math.sin(f));
      soma(p, "hips", 0, 0.25 * lado, 0.08 * lado, w);
      soma(p, "spine", 0, -0.18 * lado, -0.1 * lado, w);
      soma(p, "head", 0.08 * bate, 0.15 * lado, 0.12 * lado, w);
      const fase2 = Math.sin(f * 2);
      bracoAbs(p, "left", 0.12 + 0.38 * Math.max(0, lado), 0, 0.35 + 0.2 * fase2, w);
      bracoAbs(p, "right", 0.12 + 0.38 * Math.max(0, -lado), 0, 0.35 - 0.2 * fase2, w);
      soma(p, "leftLowerArm", 0, -1.35 - 0.3 * fase2, 0, w); soma(p, "rightLowerArm", 0, 1.35 - 0.3 * fase2, 0, w);
      maos(dd, "left", 0.9); maos(dd, "right", 0.9);
      this._quadril = { y: 0, agacha: 0.035 * bate * w, ladoPerna: lado };
    } },
  // Mortal pra tras: agacha, impulsa, gira 360 graus em X (negativo = pra tras) com as pernas grupadas, pousa.
  mortal: { dur: 2.3, tipo: "acao", corpoInteiro: true, cabelo: 1, ossos: ["hips","spine","chest","upperChest","neck","head","leftUpperArm","rightUpperArm","leftLowerArm","rightLowerArm","leftUpperLeg","rightUpperLeg","leftLowerLeg","rightLowerLeg","leftFoot","rightFoot"], rosto: {target_43:.5, target_108:.7},
    pose(p, dd, t, w) {
      const T0 = 0.45, T1 = 0.62, T2 = 1.42, T3 = 1.75; // agacha | impulso | voo | pouso | levanta
      let ag = 0, alt = 0, giro = 0, grupo = 0, bracos = 0;
      if (t < T0) { const u = minimumJerk(t / T0); ag = 0.2 * u; bracos = -0.9 * u; }
      else if (t < T1) { const u = (t - T0) / (T1 - T0); ag = 0.2 * (1 - minimumJerk(u)); bracos = -0.9 + 3.1 * minimumJerk(u); alt = 0.08 * u; }
      else if (t < T2) {
        const u = (t - T1) / (T2 - T1);
        alt = 0.08 + 0.5 * Math.sin(u * Math.PI) - 0.08 * u;
        giro = -Math.PI * 2 * smoothStep(u);
        grupo = Math.sin(clamp(u * 1.15, 0, 1) * Math.PI);
        bracos = 2.2 - 1.2 * grupo;
      } else if (t < T3) { const u = (t - T2) / (T3 - T2); giro = -Math.PI * 2; ag = 0.22 * Math.sin(u * Math.PI / 2); bracos = 1.0 - 1.4 * u; }
      else { const u = minimumJerk((t - T3) / (this.dur - T3)); giro = -Math.PI * 2; ag = 0.22 * (1 - u); bracos = -0.4 * (1 - u); }
      // bracos: -0.9 = para tras (impulso), 2.2 = acima da cabeca, grupado = abraca os joelhos
      const r = clamp(bracos / 2.2, 0, 1), tras = Math.max(0, -bracos);
      bracoAbs(p, "left", r * (1 - 0.55 * grupo), tras, grupo * 0.9, w); bracoAbs(p, "right", r * (1 - 0.55 * grupo), tras, grupo * 0.9, w);
      soma(p, "leftLowerArm", 0, -0.6 * grupo, 0, w); soma(p, "rightLowerArm", 0, 0.6 * grupo, 0, w);
      for (const s of ["left", "right"]) {
        soma(p, s + "UpperLeg", -1.75 * grupo, 0, 0, w); soma(p, s + "LowerLeg", 2.1 * grupo, 0, 0, w); soma(p, s + "Foot", -0.3 * grupo, 0, 0, w);
      }
      soma(p, "spine", 0.35 * (ag / 0.2) + 0.3 * grupo, 0, 0, w); soma(p, "head", -0.2 * grupo, 0, 0, w);
      maos(dd, "left", 0.6 * grupo + 0.2); maos(dd, "right", 0.6 * grupo + 0.2);
      this._quadril = { y: alt * w, agacha: ag * w, mortal: giro };
    } },
};

// ---- rodada 6: acoes por CLIPE (Mesh2Motion CC0; retarget em celia-3d-clipes.js) ----
// O Pai: "o andado dela ta muito ruim". O andar procedural vira fallback; com o clipe registrado (registrarClipes),
// a acao toca o clipe por cima do procedural. Sem o clipe, as acoes soClipe nao tocam; andar/comemorar caem no procedural.
// ROTA (andar/correr): oval fechado — vira, reta p/ direita, meia-volta por TRAS, reta p/ esquerda, meia-volta pela
// FRENTE, reta de volta, vira de frente. Arcos de mesmo raio: termina em x=z=0. O tempo do clipe sai dos "passos"
// (distancia percorrida + giro parado), entao o pe acompanha o chao em vez de escorregar.
function rotaAndar(vel, R) {
  const dt = 1 / 120, D = 0.9, tab = [[0, 0, 0, 0, 0]];
  let x = 0, z = 0, yaw = 0, passos = 0;
  const empurra = (s, dy) => {
    // virada parada nao avanca o clipe: avancar fazia o pe de apoio "andar" para tras sem o corpo andar (p70)
    x += Math.sin(yaw) * s * dt; z += Math.cos(yaw) * s * dt; passos += s * dt; yaw += dy;
    tab.push([tab.length * dt, x, z, yaw, passos]);
  };
  const vira = (para, dur) => { const de = yaw, n = Math.round(dur / dt); let a = de; for (let i = 1; i <= n; i++) { const b = de + (para - de) * minimumJerk(i / n); empurra(0, b - a); a = b; } };
  const reta = (dist, entra, sai) => {
    let feito = 0;
    while (feito < dist - 1e-4) {
      let s = vel; const r = dist - feito;
      if (entra) s *= Math.max(0.12, smoothStep(feito / 0.25));
      if (sai) s *= Math.max(0.12, smoothStep(r / 0.3));
      s = Math.min(s, r / dt); empurra(s, 0); feito += s * dt;
    }
  };
  const arco = (ang) => { const n = Math.max(1, Math.round(Math.abs(ang) * R / vel / dt)); for (let i = 0; i < n; i++) empurra(vel, ang / n); };
  vira(Math.PI / 2, 0.55); reta(D, true, false); arco(Math.PI); reta(2 * D, false, false); arco(Math.PI); reta(D, false, true);
  vira(2 * Math.PI, 0.55);
  return { dt, tab, dur: tab.length * dt + 0.1 };
}
function amostraRota(r, t) {
  const u = clamp(t / r.dt, 0, r.tab.length - 1.001), i = Math.floor(u), f = u - i, a = r.tab[i], b = r.tab[i + 1];
  let yaw = (a[3] + (b[3] - a[3]) * f) % (2 * Math.PI); if (yaw > Math.PI) yaw -= 2 * Math.PI;
  return { x: a[1] + (b[1] - a[1]) * f, z: a[2] + (b[2] - a[2]) * f, yaw, passos: a[4] + (b[4] - a[4]) * f };
}
const ACOES_CLIPE = {
  correr: { clipe: "Run_Female", tipo: "acao", rota: 0.8, cabelo: 0.5, rosto: { target_93: .5 } },
  vitoria: { clipe: "Victory Fist Pump", tipo: "gesto", rosto: { target_93: .8, target_46: .6 } },
  pulo_vitoria: { clipe: "Victory", tipo: "acao", cabelo: 0.6, rosto: { target_93: .8, target_48: .6 } },
  polichinelo: { clipe: "Jumping Jacks", tipo: "acao", vezes: 3, cabelo: 0.5, rosto: { target_93: .6, target_46: .4 } },
  charleston: { clipe: "Dance Charleston", tipo: "acao", vezes: 2, cabelo: 0.3, rosto: { target_93: .8, target_46: .4 } },
  danca_quadril: { clipe: "Dance Reach Hip", tipo: "acao", cabelo: 0.3, rosto: { target_105: .6, target_46: .35 } },
  // alt: o clipe sobe o quadril 1,84 m e os pes 3,4 m (p113) e saia do quadro em 29 de 151 quadros (p111); o Pai: "tao alto, saindo da
  // camera". Escala so a SUBIDA do quadril (a agachada e o resto ficam): pico ~0,83 m, pe ~2,1 m, um mortal de verdade.
  backflip: { clipe: "Backflip", tipo: "acao", cabelo: 1, alt: 0.45, rosto: { target_43: .5, target_108: .7 } },
  confusa: { clipe: "Confused", tipo: "gesto", rosto: { target_108: .35, target_31: .2 } },
  sim: { clipe: "Yes", tipo: "gesto", rosto: { target_105: .5 } },
  cumprimentar: { clipe: "Greeting", tipo: "gesto", rosto: { target_93: .7, target_46: .5 } },
  reverencia: { clipe: "Bow", tipo: "gesto", rosto: { target_105: .5, target_48: .5 } },
  continencia: { clipe: "Salute", tipo: "gesto", rosto: { target_105: .6, target_46: .35 } },
  tonta: { clipe: "Dizzy", tipo: "gesto", vezes: 2, rosto: { target_108: .5, target_31: .3 } },
  recusar: { clipe: "Reject", tipo: "gesto", rosto: { target_108: .35 } },
};
for (const [k, d] of Object.entries(ACOES_CLIPE)) ACOES[k] = { dur: 3, ossos: OSSOS, soClipe: true, pose() {}, ...d };
Object.assign(ACOES.andar, { clipe: "Walk_Female", rota: 0.5 });
Object.assign(ACOES.comemorar, { clipe: "Cheering_Two_Hands", vezes: 2 }); // o Pai: "comemoracao e muito ruim"
const EIXO_Y = new THREE.Vector3(0, 1, 0);

// ---- O corpo ----
export class Corpo3D {
  constructor(vrm, opcoes = {}) {
    this.vrm = vrm;
    this.h = vrm.humanoid;
    this.em = vrm.expressionManager;
    if (vrm.lookAt) vrm.lookAt.autoUpdate = false; // o olhar e escrito aqui, por expressao (sem disputa com o applier)
    this.no = {};
    for (const o of OSSOS) this.no[o] = this.h.getNormalizedBoneNode(o);
    this.dedosNos = {};
    for (const lado of ["left", "right"]) {
      for (const d of DEDOS) for (const f of FALANGES) this.dedosNos[lado + d + f] = this.h.getNormalizedBoneNode(lado + d + f);
      for (const f of ["Metacarpal", "Proximal", "Distal"]) this.dedosNos[lado + "Thumb" + f] = this.h.getNormalizedBoneNode(lado + "Thumb" + f);
    }
    this.quadrilRepouso = this.no.hips.position.clone();
    this.t = 0;
    this.emocao = "neutral"; this.emocaoAnterior = "neutral"; this.emocaoT = 99;
    this.preset = "Idle Ativo"; this.presetAnterior = "Idle Ativo"; this.presetT = 99; this.presetAte = 6;
    this.fase = Math.random() * 100;
    this.acao = null; this.acaoT = 0; this.fila = [];
    this.acoesPermitidas = null; // modo app (interface): Set das acoes aprovadas (V); null = sem restricao (bancada). Micro-gestos de idle passam.
    this.piscar = { proxima: rnd(1.2, 3), t: -1, dupla: false };
    this.olhar = { x: 0, y: 0, alvoX: 0, alvoY: 0, proxima: 1 };
    this.microEm = rnd(6, 10);
    this.fala = { ativa: false, t: 0, dur: 0, silabas: [], energia: 0 };
    this.energiaExterna = 0;
    this.rostoAtual = {}; this.rostoEscrito = new Set();
    this.mapaKeys = this._mapearKeys();
    vrm.scene.traverse((o) => { // lingua no tom do interior da boca (BOCA, acima); material clonado, o .vrm nao muda
      if (!o.isMesh || !o.name.startsWith("_TONGUE") || o.userData.linguaEscura) return;
      o.material = o.material.clone(); if (o.material.color) o.material.color.multiplyScalar(LINGUA_TOM); o.userData.linguaEscura = true;
    });
    this.estat = { piscadas: 0, acoes: 0, quadros: 0, donos: {}, giroMortal: 0 };
    if (opcoes.bracoSolto) BRACO_SOLTO = opcoes.bracoSolto;
    this.colisores = opcoes.colisores === false ? 0 : this._colisoresCabelo();
    this.grupoBarriga = opcoes.colisores === false ? null : this._colisoresBarriga(); this.barrigaLigada = false;
    if (this.grupoBarriga && opcoes.barriga !== false) this.ligarBarriga(true);
    this.cabeloAtras = opcoes.colisores === false || opcoes.cabeloAtras === false ? null : this._paredeCabelo(opcoes.paredeZ);
    // cabelo firme nas acrobacias (ACOES[*].cabelo): guarda a mola original de cada junta para voltar a ela
    this.cabeloFirme = opcoes.cabeloFirme !== false;
    this.cabeloBase = this.vrm.springBoneManager ? [...this.vrm.springBoneManager.joints].map((j) => [j, { ...j.settings }]) : [];
    this.firmeza = 0;
    this.clipes = {}; this.extrasClipe = new Map(); // ossos que so o clipe move (ombro, dedao do pe): voltam a identidade
    this.ik = { left: { chao: false, peso: 0, ancora: new THREE.Vector3() }, right: { chao: false, peso: 0, ancora: new THREE.Vector3() } };
    this.estat.travaPe = 0;
    // APOIO NO CHAO (rodada 8, Passo 4): altura do dedo do pe no repouso (rig normalizado, espaco da cena). Nos gestos por clipe
    // o quadril do clipe fica 2,5-4,7 cm abaixo do repouso (ate o Idle_A: -4,7) e a perna nao compensa: a sola afundava 2-4 cm
    // (p109). _pisarChao() levanta o quadril so o que o dedo passou abaixo desta altura.
    this.no.hips.updateWorldMatrix(true, true);
    this.pisoPe = {}; for (const lado of ["left", "right"]) { const n = this.h.getNormalizedBoneNode(lado + "Toes") || this.no[lado + "Foot"]; this.pisoPe[lado] = n.getWorldPosition(new THREE.Vector3()).y; }
    // SONDAS DA SOLA: o dedo (osso) nao enxerga o calcanhar. No fim do correr (p114) o dedo ficava 1,6 cm ACIMA do repouso e o
    // tornozelo 6,6 cm acima, mas o calcanhar afundava 5,6 cm. Sondas = os vertices mais baixos de cada pe no repouso (ate 24),
    // presos ao osso normalizado do pe/dedo que domina o vertice; a altura delas no quadro e a altura real da sola.
    this.sondas = this._sondasPe();
    this.chaoLift = 0; this.semChao = false; this.semDeriva = false;
    this.abducao = { left: 0, right: 0 }; this.semAfastar = false; this.estat.afasta = 0;
    // VOLUMES PROIBIDOS AOS BRACOS (rodada 10): perfil do tronco (raio por altura x angulo, do repouso) e elipsoide da cabeca; ver _empurrarBracos()
    this.semEmpurrar = false; this.empurra = { left: new THREE.Quaternion(), right: new THREE.Quaternion() }; this.estat.empurra = 0;
    this.vol = this._perfilVolumes();
    this.semSaia = true; this.volSaia = // DESLIGADA por padrao (rodada 15b): o empurrao pela saia fazia o braco direito dar trancos ao andar; ?saia liga
this._perfilVolumes((Q) => this._quadroSaia(Q), /corpo|body|other/i); // rodada 15: a mao nao entra na saia ao andar
    // MAO FANTASMA (rodada 10, p144): o transplante moveu os ossos RAW dos dedos para a mao doada (medio proximal a 9,3 cm do pulso), mas o rig NORMALIZADO volta a
    // posicao original (5,0 cm) a cada resetNormalizedPose() do three-vrm: os dedos normalizados ficam 9-12 cm fora da mao que se ve. So a ROTACAO e copiada
    // (por isso nada aparece torto), mas qualquer medida de POSICAO nos dedos normalizados mede a mao errada. Aqui: deslocamento da mao DESENHADA no referencial do pulso.
    this.maoOff = {}; for (const lado of ["left", "right"]) { const hn = this.no[lado + "Hand"]; hn.updateWorldMatrix(true, false);
      this.maoOff[lado] = ["MiddleDistal", "MiddleProximal"].map((n) => { const r = this.h.getRawBoneNode(lado + n); return r ? hn.worldToLocal(r.getWorldPosition(new THREE.Vector3())) : null; }); }
    this.torcao = false; this.torcaoFator = 1; this.torcaoCortes = [1 / 3, 2 / 3];   // distribuirTorcao(): opt-in (?torcao=1); fator 1 = toda a torcao sai do ombro
    // CORRETIVA DO BRACO LEVANTADO (rodada 15): o "cima do tronco" no referencial do peito RAW, medido no repouso (o no raw nao tem rotacao identidade)
    this.semCorretiva = false;
    { const p = this.h.getRawBoneNode("upperChest") || this.h.getRawBoneNode("chest"), q = new THREE.Quaternion();
      if (p) { p.updateWorldMatrix(true, false); p.getWorldQuaternion(q); this._upPeito = new THREE.Vector3(0, 1, 0).applyQuaternion(q.invert()); this._peitoRaw = p; } }
  }

  _sondasPe() {
    const raw = new Map(), r = { left: [], right: [], piso: { left: 0, right: 0 } }, p = new THREE.Vector3();
    for (const lado of ["left", "right"]) for (const n of ["Foot", "Toes"]) { const b = this.h.getRawBoneNode(lado + n); if (b) raw.set(b, [lado, this.h.getNormalizedBoneNode(lado + n)]); }
    this.vrm.scene.updateMatrixWorld(true);
    const cand = { left: [], right: [] };
    this.vrm.scene.traverse((m) => {
      if (!m.isSkinnedMesh) return;
      m.skeleton.update(); // boneMatrices ainda nao existem antes do 1o desenho: sem isto getVertexPosition le lixo
      const si = m.geometry.attributes.skinIndex, sw = m.geometry.attributes.skinWeight;
      for (let i = 0; i < si.count; i++) {
        let mx = 0, dono = null; for (let c = 0; c < 4; c++) { const w = sw.getComponent(i, c); if (w > mx) { mx = w; dono = raw.get(m.skeleton.bones[si.getComponent(i, c)]) || null; } }
        if (!dono || mx < 0.5) continue; m.getVertexPosition(i, p).applyMatrix4(m.matrixWorld); cand[dono[0]].push([p.y, p.clone(), dono[1]]);
      }
    });
    for (const lado of ["left", "right"]) {
      const c = cand[lado].sort((a, b) => a[0] - b[0]); if (!c.length) continue;
      const baixos = c.filter((x) => x[0] <= c[0][0] + 0.012), passo = Math.max(1, Math.floor(baixos.length / 24));
      r.piso[lado] = c[0][0];
      for (let k = 0; k < baixos.length; k += passo) { const [, pos, no] = baixos[k]; no.updateWorldMatrix(true, false); r[lado].push({ no, local: no.worldToLocal(pos.clone()) }); }
    }
    return r;
  }

  // TORCAO DO BRACO ESPALHADA (rodada 9, pedido do Pai: "no comemorar o braco afina na ligacao com o tronco"). O Cheering_Two_Hands gira o
  // umero 118-122 graus em torno do PROPRIO eixo; com toda a torcao no osso do ombro o ombro esmaga (espessura 0,72 do repouso, p135/p136).
  // O rig ja tem a cadeia de torcao arm -> arm001 (s=0,33) -> arm002 (0,67) -> elbow, so que rigida. Depois do vrm.update: a torcao em X do UpperArm sai do
  // ombro, 1/3 vai para cada um de arm001 e arm002 e o resto no elbow (a rotacao de mundo do antebraco/mao fica EXATAMENTE igual). Opt-in: ?torcao=1.
  distribuirTorcao() {
    // rodada 15: arm001/arm002 nao sao ossos do humanoide (o three-vrm nao os reseta); desligada, a torcao velha ficava neles e a mao saia do lugar
    if (!this.torcao) { if (this._tw && !this._twZerado) { for (const { A1, A2 } of this._tw) { A1.quaternion.identity(); A2.quaternion.identity(); } this._twZerado = true; } return; }
    this._twZerado = false;
    // eixo = a reta ombro -> cotovelo no repouso (esta 4,4 graus fora do X do osso; girar em torno do X deslocava o cotovelo 14 mm). A cadeia e reta (desvio <= 0,5 mm).
    const T = this._tw || (this._tw = ["left", "right"].map((lado) => {
      const U = this.h.getRawBoneNode(lado + "UpperArm"), L = this.h.getRawBoneNode(lado + "LowerArm"), A1 = U && U.children.find((c) => /arm0?01$/i.test(c.name)), A2 = A1 && A1.children.find((c) => /arm0?02$/i.test(c.name));
      return U && L && A1 && A2 && L.parent === A2 ? { U, A1, A2, L, eixo: A1.position.clone().add(A2.position).add(L.position).normalize() } : null; }).filter(Boolean));
    const q = this._tq || (this._tq = { sw: new THREE.Quaternion(), tw: new THREE.Quaternion(), r: new THREE.Quaternion(), lo: new THREE.Quaternion() });
    for (const { U, A1, A2, L, eixo } of T) {
      const u = U.quaternion; if (u.w < 0) { u.x = -u.x; u.y = -u.y; u.z = -u.z; u.w = -u.w; }
      const d = u.x * eixo.x + u.y * eixo.y + u.z * eixo.z, n = Math.hypot(d, u.w); if (n < 1e-6) continue;
      q.tw.set(eixo.x * d / n, eixo.y * d / n, eixo.z * d / n, u.w / n);                       // torcao em torno do eixo do braco
      const fi = 2 * Math.atan2(d, u.w), f = fi * this.torcaoFator, [c1, c2] = this.torcaoCortes;
      const giro = (ang, alvo) => alvo.set(eixo.x * Math.sin(ang / 2), eixo.y * Math.sin(ang / 2), eixo.z * Math.sin(ang / 2), Math.cos(ang / 2));
      q.sw.copy(u).multiply(q.r.copy(q.tw).invert());                                          // swing = q * twist^-1
      q.lo.copy(L.quaternion);
      U.quaternion.copy(q.sw).multiply(giro(fi - f, q.r));                                     // o que NAO foi redistribuido (fator < 1) fica no ombro
      giro(f * c1, A1.quaternion); giro(f * (c2 - c1), A2.quaternion);                         // fracao ACUMULADA em A1 e A2; o resto vai para o elbow
      L.quaternion.copy(giro(f * (1 - c2), q.r)).multiply(q.lo);
      U.updateMatrixWorld(true);
    }
  }

  // BRACO NA COXA (rodada 8, Fase B-2; p104: andar -1,1 cm, danca do quadril -6,9, recusar -2,3, tonta -1,4, backflip -3,0). O clipe vem de um
  // esqueleto de ombros estreitos e a Celia tem casaco e saia largos: a mao e o meio do antebraco entram no volume da coxa. Se o ponto
  // entra na coxa (raio medido 8,5 cm do eixo + 0,5 cm), o braco abre em torno do eixo z do TORAX (abducao) o MINIMO que liberta, em passos
  // de 3 graus, no maximo 26 graus; o valor sobe na hora e desce com tau 90 ms (sem tremedeira na saida). ?semafastar = controle A/B.
  _afastarBracos(dt) {
    if (this.semAfastar) return;
    const V = this._av || (this._av = Array.from({ length: 6 }, () => new THREE.Vector3())), [A, B, M, P, Q, S] = V;
    const R = 0.09, PASSO = 3 * DEG, MAXIMO = 26 * DEG, eixoZ = this._eixoZ || (this._eixoZ = new THREE.Vector3(0, 0, 1));
    this.no.hips.updateWorldMatrix(true, true);
    const dentro = (lado) => { // menor folga (m) da mao e do meio do antebraco contra o eixo das duas coxas; < 0 = dentro
      this.no[lado + "Hand"].getWorldPosition(P); this.no[lado + "LowerArm"].getWorldPosition(Q); Q.add(P).multiplyScalar(0.5);
      let pior = Infinity;
      for (const pt of [P, Q]) for (const l of ["left", "right"]) {
        this.no[l + "UpperLeg"].getWorldPosition(A); this.no[l + "LowerLeg"].getWorldPosition(B); B.sub(A);
        const t = clamp(M.copy(pt).sub(A).dot(B) / B.lengthSq(), 0, 1); S.copy(A).addScaledVector(B, t);
        pior = Math.min(pior, pt.distanceTo(S) - R);
      }
      return pior;
    };
    for (const lado of ["left", "right"]) {
      const osso = this.no[lado + "UpperArm"], q0 = osso.quaternion.clone(), sinal = lado === "left" ? 1 : -1;
      let n = 0, rot = new THREE.Quaternion();
      while (n * PASSO < MAXIMO) {
        osso.quaternion.copy(q0); if (n) osso.quaternion.premultiply(rot.setFromAxisAngle(eixoZ, sinal * n * PASSO));
        osso.updateWorldMatrix(false, true);
        if (dentro(lado) >= 0) break; n++;
      }
      const alvo = Math.min(n * PASSO, MAXIMO), a = this.abducao[lado];
      this.abducao[lado] = alvo > a ? alvo : a + (alvo - a) * (1 - Math.exp(-dt / 0.09));
      osso.quaternion.copy(q0); if (this.abducao[lado] > 1e-4) osso.quaternion.premultiply(rot.setFromAxisAngle(eixoZ, sinal * this.abducao[lado]));
      osso.updateWorldMatrix(false, true);
    }
    this.estat.afasta = Math.max(this.abducao.left, this.abducao.right) / DEG;
  }

  // ---- BRACO DENTRO DO CORPO (rodada 10, pedido do Pai: "andar: bracos dentro do corpo"; "continencia: a mao entra no corpo/cabeca").
  // O clipe vem de um esqueleto estreito e a Celia tem casaco e peito largos. p138: andar = antebraco 0 a 4,2 cm DENTRO da casca do tronco o ciclo
  // todo; continencia = 2,5 cm. A casca vem dos VERTICES do corpo no repouso: raio maximo por (altura do eixo quadril->pescoco x angulo em torno
  // dele), no referencial do peito. A cabeca e um elipsoide dos vertices do rosto no referencial do osso da cabeca.
  _quadroTronco(Q) {
    const V3 = THREE.Vector3; Q.hips = Q.hips || new V3(); Q.ax = Q.ax || new V3(); Q.lat = Q.lat || new V3(); Q.fwd = Q.fwd || new V3(); Q.tmp = Q.tmp || new V3();
    this.no.hips.updateWorldMatrix(true, true);
    this.no.hips.getWorldPosition(Q.hips); this.no.neck.getWorldPosition(Q.ax); Q.ax.sub(Q.hips); Q.L = Q.ax.length(); Q.ax.divideScalar(Q.L);
    Q.q = Q.q || new THREE.Quaternion(); this.no.chest.getWorldQuaternion(Q.q); Q.lat.set(1, 0, 0).applyQuaternion(Q.q);
    Q.lat.addScaledVector(Q.ax, -Q.lat.dot(Q.ax)).normalize(); Q.fwd.crossVectors(Q.lat, Q.ax).normalize(); return Q;
  }
  _celaTronco(Q, P, NH, NA) { // [indice, t, r, dir radial] do ponto P no quadro Q; indice -1 = fora da altura do tronco
    const d = Q.tmp.copy(P).sub(Q.hips), t = d.dot(Q.ax) / Q.L; d.addScaledVector(Q.ax, -t * Q.L);
    const r = d.length(); if (t < 0.02 || t > 0.98 || r < 1e-5) return { i: -1, t, r, x: 0, y: 0 };
    const th = Math.atan2(d.dot(Q.fwd), d.dot(Q.lat)), hb = Math.min(NH - 1, Math.floor(t * NH)), ab = ((Math.floor((th + Math.PI) / (2 * Math.PI) * NA) % NA) + NA) % NA;
    return { i: hb * NA + ab, t, r, x: d.dot(Q.lat) / r, y: d.dot(Q.fwd) / r };
  }
  // SAIA (rodada 15, "na hora que ela anda, a mao atravessa as coxas"): a perna em _afastarBracos e um cilindro de 9 cm, mas a saia chega a 12,7 cm
  // do eixo da coxa onde a mao passa. Segunda casca com o mesmo formato da do tronco, eixo dos joelhos ate o quadril, no referencial do quadril.
  _quadroSaia(Q) {
    const V3 = THREE.Vector3; Q.hips = Q.hips || new V3(); Q.ax = Q.ax || new V3(); Q.lat = Q.lat || new V3(); Q.fwd = Q.fwd || new V3(); Q.tmp = Q.tmp || new V3(); Q.top = Q.top || new V3();
    this.no.hips.updateWorldMatrix(true, true);
    this.no.leftLowerLeg.getWorldPosition(Q.hips); this.no.rightLowerLeg.getWorldPosition(Q.tmp); Q.hips.add(Q.tmp).multiplyScalar(0.5); // base = meio dos joelhos
    this.no.hips.getWorldPosition(Q.top); Q.ax.copy(Q.top).sub(Q.hips); Q.L = Q.ax.length(); Q.ax.divideScalar(Q.L);
    Q.q = Q.q || new THREE.Quaternion(); this.no.hips.getWorldQuaternion(Q.q); Q.lat.set(1, 0, 0).applyQuaternion(Q.q);
    Q.lat.addScaledVector(Q.ax, -Q.lat.dot(Q.ax)).normalize(); Q.fwd.crossVectors(Q.lat, Q.ax).normalize(); return Q;
  }
  _perfilVolumes(quadro = (Q) => this._quadroTronco(Q), malhas = /corpo|body/i) {
    const V3 = THREE.Vector3, NH = 14, NA = 24, R = new Float32Array(NH * NA), Q = {}, p = new V3();
    this.vrm.scene.updateMatrixWorld(true); quadro(Q);
    const braco = new Set(); for (const k of ["leftShoulder", "rightShoulder"]) { const b = this.h.getRawBoneNode(k); if (b) b.traverse((o) => braco.add(o)); }
    this.vrm.scene.traverse((m) => {
      if (!m.isSkinnedMesh || !malhas.test(m.name)) return;
      m.skeleton.update(); const si = m.geometry.attributes.skinIndex, sw = m.geometry.attributes.skinWeight;
      for (let i = 0; i < si.count; i++) {
        m.getVertexPosition(i, p).applyMatrix4(m.matrixWorld);
        let mx = 0, dono = null; for (let c = 0; c < 4; c++) { const w = sw.getComponent(i, c); if (w > mx) { mx = w; dono = m.skeleton.bones[si.getComponent(i, c)]; } }
        if (!dono || braco.has(dono)) continue;
        const c = this._celaTronco(Q, p, NH, NA); if (c.i < 0 || c.r > 0.3) continue; if (c.r > R[c.i]) R[c.i] = c.r;
      }
    });
    for (let it = 0; it < 6; it++) for (let h = 0; h < NH; h++) for (let a = 0; a < NA; a++) { // buracos: media dos vizinhos que existem
      if (R[h * NA + a] > 0) continue; let s = 0, n = 0; for (const [dh, da] of [[0, 1], [0, -1], [1, 0], [-1, 0]]) { const v = R[Math.min(NH - 1, Math.max(0, h + dh)) * NA + ((a + da + NA) % NA)]; if (v > 0) { s += v; n++; } }
      if (n) R[h * NA + a] = s / n; }
    for (let i = 0; i < R.length; i++) if (!(R[i] > 0)) R[i] = 0.08;
    return { NH, NA, R, Q };
  }
  _empurrarBracos(dt) {
    const vol = this.vol; if (this.semEmpurrar || !vol) { this.estat.empurra = 0; return; }
    const V3 = THREE.Vector3, W = this._ev || (this._ev = Array.from({ length: 9 }, () => new V3())), [S, Pm, Ph, Pt, N, A, Rv, Mv, Tm] = W;
    const qa = this._eq || (this._eq = Array.from({ length: 4 }, () => new THREE.Quaternion())), [pq, rot, qinv, dq] = qa;
    // rodada 15: cumprimentar batia no teto de 20 graus com a mao 3,4 cm dentro do casaco; paz/continencia/reverencia ficavam 1-1,5 cm dentro (= TOL)
    const PASSO = 3 * DEG, MAXIMO = (this.empurraMax || 40) * DEG, TOL = this.empurraTol ?? 0.005, { NH, NA, R, Q } = vol;
    const saia = this.semSaia ? null : this.volSaia; if (saia) this._quadroSaia(saia.Q);
    // pior (mais fundo) ponto do braco no volume proibido; devolve {gap (m, <0 = dentro), P, n}
    const pior = (lado) => {
      this.no[lado + "LowerArm"].getWorldPosition(Tm); this.no[lado + "Hand"].getWorldPosition(Ph);
      // a mao DESENHADA fica 5-10 cm alem do osso do pulso e os dedos NORMALIZADOS estao no lugar da mao antiga (p144): usar os deslocamentos dos ossos raw
      const off = this.maoOff && this.maoOff[lado], hn = this.no[lado + "Hand"]; hn.updateWorldMatrix(true, false);
      if (off && off[0]) { Pt.copy(off[0]).applyMatrix4(hn.matrixWorld); Pt.sub(Ph).multiplyScalar(1.15).add(Ph); } else Pt.copy(Ph).sub(Tm).normalize().multiplyScalar(0.08).add(Ph);
      const pts = (this._ept || (this._ept = Array.from({ length: 7 }, () => new V3())));   // cotovelo, 25/50/75% do antebraco, pulso, ponta da mao, meio da mao
      pts[0].copy(Tm); pts[1].lerpVectors(Tm, Ph, 0.25); pts[2].lerpVectors(Tm, Ph, 0.5); pts[3].lerpVectors(Tm, Ph, 0.75); pts[4].copy(Ph); pts[5].copy(Pt); if (off && off[1]) pts[6].copy(off[1]).applyMatrix4(hn.matrixWorld); else pts[6].lerpVectors(Ph, Pt, 0.5);
      let r = { gap: Infinity, P: null, n: new V3() };
      for (const P of pts) {
        const c = this._celaTronco(Q, P, NH, NA);
        if (c.i >= 0) { const g = c.r - R[c.i]; if (g < r.gap) { r.gap = g; r.P = P; r.n.copy(Q.lat).multiplyScalar(c.x).addScaledVector(Q.fwd, c.y); } }
        if (saia) { const s = this._celaTronco(saia.Q, P, saia.NH, saia.NA);
          if (s.i >= 0) { const g = s.r - saia.R[s.i]; if (g < r.gap) { r.gap = g; r.P = P; r.n.copy(saia.Q.lat).multiplyScalar(s.x).addScaledVector(saia.Q.fwd, s.y); } } }
      }
      return r;
    };
    this._quadroTronco(Q);
    let maior = 0;
    for (const lado of ["left", "right"]) {
      const osso = this.no[lado + "UpperArm"], q1 = osso.quaternion.clone(); let ang = 0;
      osso.updateWorldMatrix(true, true);
      while (ang < MAXIMO) {
        const w = pior(lado); if (w.gap >= -TOL || !w.P) break;
        osso.getWorldPosition(S); Rv.copy(w.P).sub(S); A.crossVectors(Rv, w.n); if (A.lengthSq() < 1e-10) break; A.normalize();
        Mv.crossVectors(A, Rv); if (Mv.dot(w.n) < 0) A.negate();
        rot.setFromAxisAngle(A, PASSO); osso.parent.getWorldQuaternion(pq); qinv.copy(pq).invert();
        osso.quaternion.premultiply(dq.copy(qinv).multiply(rot).multiply(pq)); osso.updateWorldMatrix(false, true); ang += PASSO;
      }
      this.estat.empurraGap = this.estat.empurraGap || {}; this.estat.empurraGap[lado] = +(pior(lado).gap * 100).toFixed(1); this.estat.empurraAng = this.estat.empurraAng || {}; this.estat.empurraAng[lado] = +(ang / DEG).toFixed(0);
      // alvo = correcao em relacao ao pose do clipe; sobe na hora, desce com tau 90 ms
      const alvo = osso.quaternion.clone().multiply(q1.clone().invert()), atual = this.empurra[lado];
      // rodada 15: subir NA HORA fazia o braco pular 2,5-5 cm por quadro ao andar (o alvo liga/desliga); sobe com tau curto (empurraSobe, s)
      const subir = 2 * Math.acos(Math.min(1, Math.abs(alvo.w))) > 2 * Math.acos(Math.min(1, Math.abs(atual.w)));
      if (subir && !(this.empurraSobe > 0)) atual.copy(alvo); else atual.slerp(alvo, 1 - Math.exp(-dt / (subir ? this.empurraSobe : 0.09)));
      osso.quaternion.copy(q1).premultiply(atual); osso.updateWorldMatrix(false, true);
      maior = Math.max(maior, 2 * Math.acos(Math.min(1, Math.abs(atual.w))) / DEG);
    }
    this.estat.empurra = maior;
  }

  // Levanta o quadril o quanto a sola mais baixa (sondas) passou abaixo do repouso (nunca baixa, nunca empurra salto: o pe no ar fica
  // ACIMA do repouso). Filtro de 60 ms: sem degrau na entrada/saida. Roda ANTES da trava de pe (o IK parte da altura corrigida).
  _pisarChao(dt) {
    const P = this._pv || (this._pv = new THREE.Vector3());
    this.no.hips.updateWorldMatrix(true, true);
    let menor = Infinity;
    for (const lado of ["left", "right"]) {
      const sd = this.sondas && this.sondas[lado];
      if (sd && sd.length) { for (const s of sd) menor = Math.min(menor, P.copy(s.local).applyMatrix4(s.no.matrixWorld).y - this.sondas.piso[lado]); }
      else { const n = this.h.getNormalizedBoneNode(lado + "Toes") || this.no[lado + "Foot"]; menor = Math.min(menor, n.getWorldPosition(P).y - this.pisoPe[lado]); }
    }
    const alvo = clamp(-menor, 0, 0.14); // teto 14 cm (era 8: as posturas baixas do KayKit - guarda de luta, arco, giro - pedem ate 12,5 cm, p130); sem "* w": a sola ja foi medida na pose MISTURADA com o peso do clipe (w duas vezes sub-corrigia a entrada/saida)
    // ataque INSTANTANEO (subir nunca atrasa: o atraso do filtro deixava a sola afundar 2 cm no 0,2 s da entrada, p109); so o relaxar e suave
    this.chaoLift = alvo > this.chaoLift ? alvo : this.chaoLift + (alvo - this.chaoLift) * (1 - Math.exp(-dt / 0.06));
    this.no.hips.position.y += this.chaoLift; this.estat.chaoLift = this.chaoLift;
  }

  // TRAVA DO PE (rodada 6): no andar/correr por clipe, a ponta do pe que o clipe diz estar no chao fica presa onde
  // pousou; IK de dois ossos (coxa/canela) leva o tornozelo ate la e o pe guarda a rotacao do clipe. O proprio clipe
  // tem os pes assimetricos (0,72 x 1,09 m/s) e nas curvas nenhuma velocidade serve aos dois pes (p70).
  _travarPes(clipe, tc, w, dt) {
    if (!clipe.pes || !clipe.pisos) return;
    const V = (this._ikV || (this._ikV = Array.from({ length: 8 }, () => new THREE.Vector3())));
    const Q = (this._ikQ || (this._ikQ = Array.from({ length: 6 }, () => new THREE.Quaternion())));
    const [A, B, C, T, P, ba, bc, n] = V, [qu, ql, qf, qp, r, r2] = Q;
    this.no.hips.updateWorldMatrix(true, true);
    const u = clamp(tc / clipe.dur, 0, 1) * (clipe.n - 1), i = Math.min(clipe.n - 2, Math.floor(u)), f = u - i;
    ["left", "right"].forEach((lado, k) => {
      const st = this.ik[lado], pe = clipe.pes[k];
      const chao = pe[i * 3 + 1] * (1 - f) + pe[(i + 1) * 3 + 1] * f <= clipe.pisos[k] + 0.012;
      const U = this.no[lado + "UpperLeg"], L = this.no[lado + "LowerLeg"], F = this.no[lado + "Foot"];
      const ponta = this.h.getNormalizedBoneNode(lado + "Toes") || F;
      ponta.getWorldPosition(P);
      if (chao && !st.chao) st.ancora.copy(P);
      st.chao = chao; st.peso = chao ? 1 : Math.max(0, st.peso - dt / 0.12);
      const p = st.peso * w; if (p < 1e-3) return;
      U.getWorldPosition(A); L.getWorldPosition(B); F.getWorldPosition(C);
      // alvo do tornozelo: o quanto a ponta escorregou, desfeito (so no chao, x/z; a altura e do clipe)
      T.copy(C); T.x += (st.ancora.x - P.x) * p; T.z += (st.ancora.z - P.z) * p;
      const a = A.distanceTo(B), b = B.distanceTo(C), d = clamp(A.distanceTo(T), Math.abs(a - b) + 1e-4, a + b - 1e-4);
      U.getWorldQuaternion(qu); L.getWorldQuaternion(ql); F.getWorldQuaternion(qf); U.parent.getWorldQuaternion(qp);
      ba.copy(A).sub(B); bc.copy(C).sub(B); n.crossVectors(ba, bc);
      if (n.lengthSq() > 1e-10) { // joelho: lei dos cossenos no plano atual da perna
        n.normalize(); r.setFromAxisAngle(n, Math.acos(clamp((a * a + b * b - d * d) / (2 * a * b), -1, 1)) - ba.angleTo(bc));
        ql.premultiply(r); bc.applyQuaternion(r);
      }
      C.copy(B).add(bc).sub(A).normalize(); T.sub(A).normalize(); r2.setFromUnitVectors(C, T);
      qu.premultiply(r2); ql.premultiply(r2);
      U.quaternion.copy(qp.invert().multiply(qu)); L.quaternion.copy(qu.clone().invert().multiply(ql)); F.quaternion.copy(ql.invert().multiply(qf));
      U.updateWorldMatrix(false, true);
      this.estat.travaPe += 1;
    });
  }

  // clipes ja convertidos (celia-3d-clipes.js). Acerta a duracao de cada acao pelo clipe e monta a rota do andar.
  registrarClipes(dic) {
    for (const [k, c] of Object.entries(dic || {})) if (c && c.ossos) this.clipes[k] = c;
    for (const def of Object.values(ACOES)) {
      const c = def.clipe && this.clipes[def.clipe]; if (!c) continue;
      if (def.rota) { def._rota = rotaAndar(c.vel > 0.2 ? c.vel : 0.9, def.rota); def.dur = def._rota.dur; }
      else def.dur = c.dur * (def.vezes || 1);
    }
    return Object.keys(this.clipes).length;
  }

  // k=0: mola original. k=1: mais rigida, sem gravidade e com arrasto alto -> a mecha guarda a forma de repouso em
  // relacao a cabeca e gira junto com o corpo, em vez de ficar pendurada no mundo e o corpo passar por dentro dela.
  _firmarCabelo(k) {
    if (!this.cabeloFirme || Math.abs(k - this.firmeza) < 1e-3) return;
    this.firmeza = k;
    for (const [j, b] of this.cabeloBase) {
      j.settings.stiffness = b.stiffness * (1 + 4 * k);
      j.settings.gravityPower = b.gravityPower * (1 - k);
      j.settings.dragForce = b.dragForce + (0.9 - b.dragForce) * k;
    }
  }

  // O .vrm veio com 0 colliders: no mortal o cabelo entrava no corpo em ~70 de 156 quadros, ate 99 mm (p32).
  // Capsulas do tronco e das pernas, criadas na T-pose (construtor), filhas dos ossos raw: seguem o corpo.
  _colisoresCabelo() {
    const sb = this.vrm.springBoneManager; if (!sb || !sb.joints.size) return 0;
    const h = this.vrm.humanoid, raw = (n) => h.getRawBoneNode(n);
    this.vrm.scene.updateMatrixWorld(true);
    // [osso, ate, raio, recuo para tras em m]. O casaco vai a 166 mm atras do eixo da coluna e 131 mm a frente (busto),
    // e as mechas da frente repousam a 73 mm do eixo (p40): a capsula do tronco fica recuada para as costas, onde o
    // cabelo encosta, sem empurrar as mechas da frente. Capsula centrada no eixo deixava a mecha cortar o casaco (p41).
    const TRECHOS = [["hips", "spine", 0.085, 0.06], ["spine", "chest", 0.085, 0.07], ["chest", "upperChest", 0.085, 0.07],
      ["upperChest", "neck", 0.07, 0.05],
      // giro (p55): o cabelo chicoteava pela frente e pelos bracos. Eixo central fino (alcanca 72 mm com o hitRadius, as
      // mechas da frente repousam a 73 mm) e as mangas.
      ["hips", "upperChest", 0.05, 0], ["upperChest", "neck", 0.05, 0],
      ["leftUpperArm", "leftLowerArm", 0.065, 0], ["rightUpperArm", "rightLowerArm", 0.065, 0],
      ["leftLowerArm", "leftHand", 0.05, 0], ["rightLowerArm", "rightHand", 0.05, 0],
      ["leftUpperLeg", "leftLowerLeg", 0.065, 0], ["rightUpperLeg", "rightLowerLeg", 0.065, 0],
      ["leftLowerLeg", "leftFoot", 0.05, 0], ["rightLowerLeg", "rightFoot", 0.05, 0]];
    const colliders = [], tras = new THREE.Vector3(0, 0, -1); // VRM 1.0 olha +Z: costas em -Z (na T-pose do construtor)
    for (const [a, b, r, recuo] of TRECHOS) {
      const A = raw(a), B = raw(b); if (!A || !B) continue;
      const pa = A.getWorldPosition(new THREE.Vector3()).addScaledVector(tras, recuo);
      const pb = B.getWorldPosition(new THREE.Vector3()).addScaledVector(tras, recuo);
      const offset = A.worldToLocal(pa), tail = A.worldToLocal(pb);
      const col = new VRMSpringBoneCollider(new VRMSpringBoneColliderShapeCapsule({ offset, tail, radius: r }));
      col.name = "colisor_" + a; A.add(col); colliders.push(col);
    }
    const grupo = { colliders, name: "corpo-celia3d" };
    for (const j of sb.joints) j.colliderGroups = [...j.colliderGroups, grupo];
    sb._isSortedJointsDirty = true; // o gerenciador recalcula a ordem com os colliders como dependencia
    return colliders.length;
  }

  // BARRIGA E PELVE (rodada 10; p107: levar golpe +1483, arremessar +1593, levar golpe 2 +1028 vertices de cabelo dentro do corpo; a olho, a
  // massa do cabelo sai pela barriga). Os colisores acima so cobrem 5 cm a frente do eixo e o tronco tem ~13 cm: a mecha atravessava o tronco
  // inteiro de tras para a frente e de baixo para cima. Este grupo e SEPARADO e centrado (sem recuo) so da cintura para baixo, onde nenhuma
  // mecha repousa; ligarBarriga(false) volta ao comportamento antigo (?semcolisorbarriga = controle A/B).
  _colisoresBarriga() {
    const sb = this.vrm.springBoneManager; if (!sb || !sb.joints.size) return null;
    const raw = (n) => this.h.getRawBoneNode(n);
    this.vrm.scene.updateMatrixWorld(true);
    const TRECHOS = [["hips", "spine", 0.11], ["spine", "chest", 0.125], ["chest", "upperChest", 0.10], ["leftUpperLeg", "rightUpperLeg", 0.085]], colliders = [];
    for (const [a, b, r] of TRECHOS) {
      const A = raw(a), B = raw(b); if (!A || !B) continue;
      const offset = A.worldToLocal(A.getWorldPosition(new THREE.Vector3())), tail = A.worldToLocal(B.getWorldPosition(new THREE.Vector3()));
      const col = new VRMSpringBoneCollider(new VRMSpringBoneColliderShapeCapsule({ offset, tail, radius: r }));
      col.name = "colisor_barriga_" + a; A.add(col); colliders.push(col);
    }
    return { colliders, name: "barriga-celia3d" };
  }

  // CABELO SO ATRAS (rodada 15, pedido do Pai: "nenhuma parte atravessa ela pra frente, fique so atras, se movendo atras"). Plano preso ao peito
  // (raw upperChest), vertical, passando pelo eixo do tronco (+paredeZ m a frente), com a normal para as costas: junta de mecha que passa para a
  // frente dele volta para tras. So vale para as mechas que DESCEM ate o tronco (ponta abaixo da linha dos ombros); franja e mechas do rosto ficam livres
  // (o plano infinito as jogaria para dentro da cabeca). A cauda nao entra. ?cabelofrente = controle A/B (sem a parede).
  _paredeCabelo(paredeZ = 0) {
    const sb = this.vrm.springBoneManager, peito = this.h.getRawBoneNode("upperChest") || this.h.getRawBoneNode("chest"); if (!sb || !sb.joints.size || !peito) return null;
    this.vrm.scene.updateMatrixWorld(true);
    const juntas = [...sb.joints], filhoDe = new Map(juntas.map((j) => [j.bone, j]));
    const ombro = peito.getWorldPosition(new THREE.Vector3()).y + 0.03; // linha dos ombros: franja/mechas do rosto acabam acima dela
    const raizes = juntas.filter((j) => !juntas.some((k) => k.child === j.bone) && !/cauda/i.test(j.bone.name));
    const escolhidas = new Set(), nomes = [];
    for (const r of raizes) {
      const cadeia = []; let j = r; while (j && cadeia.length < 32) { cadeia.push(j); j = j.child && filhoDe.get(j.child); }
      const ponta = (cadeia[cadeia.length - 1].child || cadeia[cadeia.length - 1].bone).getWorldPosition(new THREE.Vector3());
      const naFrenteDoPescoco = Math.abs(ponta.x - peito.getWorldPosition(new THREE.Vector3()).x) < 0.1 && ponta.z > 0.03; // empurrada, entraria no pescoco
      if (ponta.y < ombro && !naFrenteDoPescoco) { cadeia.forEach((x) => escolhidas.add(x)); nomes.push(r.bone.name); }
    }
    const P = peito.getWorldPosition(new THREE.Vector3()); P.z += paredeZ; // VRM 1.0 olha +Z
    const q = peito.getWorldQuaternion(new THREE.Quaternion()).invert();
    const col = new VRMSpringBoneCollider(new VRMSpringBoneColliderShapePlane({ normal: new THREE.Vector3(0, 0, -1).applyQuaternion(q) }));
    col.name = "colisor_parede_cabelo"; peito.add(col); col.position.copy(peito.worldToLocal(P.clone())); // o plano usa a posicao do proprio colisor
    const grupo = { colliders: [col], name: "parede-cabelo-celia3d" };
    for (const j of escolhidas) j.colliderGroups = [...j.colliderGroups, grupo];
    sb._isSortedJointsDirty = true;
    return { grupo, mechas: nomes, juntas: escolhidas.size };
  }

  ligarBarriga(ligado) {
    const sb = this.vrm.springBoneManager, g = this.grupoBarriga; if (!sb || !g) return false;
    for (const j of sb.joints) { j.colliderGroups = j.colliderGroups.filter((x) => x !== g); if (ligado) j.colliderGroups = [...j.colliderGroups, g]; }
    sb._isSortedJointsDirty = true; this.barrigaLigada = !!ligado; return this.barrigaLigada;
  }

  _mapearKeys() {
    const m = {};
    this.vrm.scene.traverse((o) => {
      if (!o.isMesh || !o.morphTargetDictionary) return;
      for (const [k, i] of Object.entries(o.morphTargetDictionary)) (m[k] || (m[k] = [])).push([o, i]);
    });
    return m;
  }

  setEmotion(nome) {
    const n = EMOTION_ALIASES[nome] || nome;
    if (!PROFILES[n] || n === this.emocao) return this.emocao;
    this.emocaoAnterior = this.emocao; this.emocao = n; this.emocaoT = 0;
    this._trocarPreset(true);
    return n;
  }
  // imediato (botao do usuario): interrompe o que estiver tocando, com transicao de 0,3 s a partir da pose atual.
  // O Pai: "aperto os botoes e nao troca" — a acao longa (andar ~10 s) so deixava a nova na fila.
  play(nome, imediato = false) {
    if (!ACOES[nome]) return false;
    if (this.acoesPermitidas && ACOES[nome].tipo !== "micro" && !this.acoesPermitidas.has(nome)) return false; // so as aprovadas aparecem na interface
    if (ACOES[nome].soClipe && !this.clipes[ACOES[nome].clipe]) return false; // clipe ainda nao carregou
    if (this.acao && this.acao.nome === nome) return false; // nao reinicia a mesma acao no meio
    if (this.acao && ACOES[this.acao.nome].tipo === "acao" && !imediato) { this.fila = [nome]; return "fila"; }
    if (this.acao) this._fotografarPose();
    this.fila = [];
    for (const s of Object.values(this.ik)) { s.chao = false; s.peso = 0; }
    this.acao = { nome, t: 0 }; this.estat.acoes += 1; return true;
  }
  _fotografarPose() {
    const q = new Map(), nos = [...Object.values(this.no), ...Object.values(this.dedosNos), ...this.extrasClipe.values()];
    for (const n of nos) if (n) q.set(n, n.quaternion.clone());
    this.transicao = { t: 0, dur: 0.3, q, p: this.no.hips.position.clone() };
  }
  _aplicarTransicao(dt) {
    const tr = this.transicao; if (!tr) return;
    tr.t += dt; const u = smoothStep(tr.t / tr.dur);
    for (const [n, q0] of tr.q) n.quaternion.copy(q0.clone().slerp(n.quaternion, u));
    this.no.hips.position.lerpVectors(tr.p, this.no.hips.position, u);
    if (u >= 1) this.transicao = null;
  }
  falar(segundos = 3.2) {
    const vis = ["aa", "ee", "oh", "ih", "ou", "aa", "ee", "aa"];
    const sil = []; let t = 0.05;
    while (t < segundos) { const d = rnd(0.13, 0.24); sil.push({ t, d, v: vis[(Math.random() * vis.length) | 0], a: rnd(0.55, 1) }); t += d + (Math.random() < 0.12 ? rnd(0.15, 0.3) : 0); }
    this.fala = { ativa: true, t: 0, dur: segundos, silabas: sil, energia: 0 };
  }
  setSpeechEnergy(e) { this.energiaExterna = clamp(Number(e) || 0, 0, 1); }

  _trocarPreset(forca = false) {
    const prof = PROFILES[this.emocao];
    const estado = this.fala.ativa || this.energiaExterna > 0.15 ? "speaking" : "idle";
    const pool = (prof[estado] || prof.idle).filter((n) => PRESETS[n] && n !== this.preset);
    if (!pool.length && !forca) return;
    this.presetAnterior = this.preset;
    this.preset = pool.length ? pool[(Math.random() * pool.length) | 0] : this.preset;
    this.presetT = 0; this.presetAte = rnd(6, 10);
  }

  _perfilMisturado() {
    const a = PROFILES[this.emocaoAnterior], b = PROFILES[this.emocao];
    const k = smoothStep(this.emocaoT / EMOTION_TRANSITION_S);
    const mix = (x, y) => x + (y - x) * k;
    const rosto = {};
    for (const [n, v] of Object.entries(a.rosto)) rosto[n] = (rosto[n] || 0) + v * (1 - k);
    for (const [n, v] of Object.entries(b.rosto)) rosto[n] = (rosto[n] || 0) + v * k;
    return { amp: mix(a.amp, b.amp), energy: mix(a.energy, b.energy), rosto,
      posture: { y: mix(a.posture.y, b.posture.y), z: mix(a.posture.z, b.posture.z), body: mix(a.posture.body, b.posture.body) },
      corpo: { braco: mix(a.corpo.braco, b.corpo.braco), cotovelo: mix(a.corpo.cotovelo, b.corpo.cotovelo), punho: mix(a.corpo.punho, b.corpo.punho), frente: mix(a.corpo.frente, b.corpo.frente) } };
  }
  // Sinais do preset, iguais ao presetSignals() do 2D: frequencias em rad/s (sin(T*freq)), harmonicos 1.618/2.414.
  // A troca de preset mistura os SINAIS (como o 2D), nunca a frequencia: misturar freq dentro de sin(T*freq) com T
  // grande varre a fase e a cabeca chacoalhou a ~300 graus/s em toda troca (medido, web/p31_trancos.py).
  _sinais(pr, T, Tv) {
    const f = this.fase;
    const breath = Math.sin(T * pr.breathFreq + f), breath2 = Math.sin(T * pr.breathFreq * 1.618 + 1.57 + f) * 0.28;
    const nod = Math.sin(T * pr.nodFreq + f * 0.7), nod2 = Math.sin(T * pr.nodFreq * 1.618 + 1.2) * 0.40;
    const sway = Math.sin(T * pr.swayFreq + f * 0.4), sway2 = Math.sin(T * pr.swayFreq * 1.618 + 0.78) * 0.45;
    const roll = Math.sin(T * pr.rollFreq + f * 0.9), roll2 = Math.sin(T * pr.rollFreq * 2.414 + 1.04) * 0.38;
    const ch = pr.chaos || 0;
    return { respira: (breath + breath2) * pr.breathAmp, aceno: (nod + nod2) * pr.nodAmp, balanco: (sway + sway2) * pr.swayAmp,
      balancoN: (sway + sway2) / 1.45, rola: (roll + roll2) * pr.rollAmp, ombro: (breath * 0.5 + 0.5) * pr.shoulderAmp,
      quique: (0.5 + 0.5 * Math.sin(Tv * pr.breathFreq + f)) * pr.bobAmp, caos: Math.sin(T * 1.7 + ch * 10) * ch * 1.25 };
  }
  _sinaisMisturados(T, Tv) {
    const b = this._sinais(PRESETS[this.preset], T, Tv);
    if (this.presetT >= PRESET_TRANSITION_S || this.presetAnterior === this.preset) return b;
    const a = this._sinais(PRESETS[this.presetAnterior], T, Tv), q = minimumJerk(this.presetT / PRESET_TRANSITION_S), r = {};
    for (const k of Object.keys(b)) r[k] = a[k] * (1 - q) + b[k] * q;
    return r;
  }

  update(dt) {
    dt = clamp(dt, 0, 0.1);
    this.t += dt; this.emocaoT += dt; this.presetT += dt; this.estat.quadros += 1;
    if (this.presetT > this.presetAte) this._trocarPreset();
    const prof = this._perfilMisturado();
    const amp = prof.amp, T = this.t * PROCEDURAL_SPEED, Tv = this.t * VERTICAL_PROCEDURAL_SPEED;
    const sg = this._sinaisMisturados(T, Tv);
    const pose = vazio(); const dedos = {};

    // fala: silabas -> visemas + energia
    let energia = this.energiaExterna, visemas = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
    if (this.fala.ativa) {
      const f = this.fala; f.t += dt;
      for (const s of f.silabas) {
        const u = (f.t - s.t) / s.d;
        if (u > -0.4 && u < 1.4) { const g = Math.sin(clamp((u + 0.4) / 1.8, 0, 1) * Math.PI) * s.a; visemas[s.v] = Math.max(visemas[s.v], g * BOCA_MAX); }
      }
      energia = Math.max(energia, Math.max(...Object.values(visemas)));
      if (f.t > f.dur + 0.3) { f.ativa = false; this._trocarPreset(); }
    }
    this.fala.energia = energia;
    // o corpo usa a energia suavizada (o 2D faz o mesmo com speechDrive); a boca continua silaba a silaba
    this.energiaCorpo = (this.energiaCorpo || 0) + (energia - (this.energiaCorpo || 0)) * (1 - Math.exp(-dt / FALA_CORPO_TAU));
    const ec = this.energiaCorpo;

    // ---- camada 1: procedural herdado do 2D (mesmos sinais do presetSignals; graus -> radianos com ganho do 3D) ----
    const fala = 1 + ec * 0.9;
    soma(pose, "chest", sg.respira * 0.30 * DEG * amp, 0, 0);
    soma(pose, "upperChest", sg.respira * 0.25 * DEG * amp, 0, 0);
    soma(pose, "head", (sg.aceno * 0.55 * fala + sg.caos * 2) * DEG * amp, 0, 0);
    soma(pose, "neck", sg.aceno * 0.2 * DEG * amp, 0, 0);
    soma(pose, "spine", 0, sg.balanco * 0.28 * DEG * amp, sg.balanco * 0.12 * DEG * amp);
    soma(pose, "head", 0, -sg.balanco * 0.2 * DEG * amp, sg.rola * 0.5 * DEG * amp);
    soma(pose, "hips", 0, sg.balanco * 0.1 * DEG * amp, -sg.balanco * 0.08 * DEG * amp);
    // ombros (nao ha osso de ombro: o braco inteiro sobe um pouco com a respiracao)
    const ombro = sg.ombro * 0.12 * DEG * amp;
    // postura da emocao (mesma semantica do 2D: y = queixo, z = inclinacao, body = peito)
    soma(pose, "head", -prof.posture.y * 1.1 * DEG, 0, prof.posture.z * 2.2 * DEG);
    soma(pose, "spine", -prof.posture.body * 1.6 * DEG, 0, 0);
    soma(pose, "upperChest", -prof.posture.body * 1.2 * DEG, 0, 0);

    // ---- camada 1b: bracos relaxados (fim da T-pose) + postura do corpo por emocao + gesticulacao da fala ----
    const c = prof.corpo, gest = ec * Math.sin(this.t * 2.6) * 0.18, gest2 = ec * Math.sin(this.t * 2.1 + 1) * 0.16;
    const balancoBraco = sg.balancoN * 0.04;
    soma(pose, "leftUpperArm", 0, -0.12 - c.frente * 0.6 - gest2, -BRACO_SOLTO + c.braco + ombro + balancoBraco + gest * 0.5);
    soma(pose, "rightUpperArm", 0, 0.12 + c.frente * 0.6 + gest, BRACO_SOLTO - c.braco - ombro + balancoBraco - gest2 * 0.5);
    soma(pose, "leftLowerArm", 0, -0.32 - c.cotovelo - ec * 0.35 - gest, 0);
    soma(pose, "rightLowerArm", 0, 0.32 + c.cotovelo + ec * 0.35 + gest2, 0);
    // vida na mao so pelo giro barato do punho (eixo y: 0.3 rad = 7 mm; x/z entortam o antebraco, p14)
    soma(pose, "leftHand", 0, -0.08 - gest * 0.4, 0); soma(pose, "rightHand", 0, 0.08 + gest2 * 0.4, 0);
    maos(dedos, "left", c.punho, [], null, this.t); maos(dedos, "right", c.punho, [], null, this.t);
    // pernas: peso levemente para um lado e quique do preset (agachamento real, pe no chao)
    let agacha = 0.006 + sg.quique * 0.0006 * amp;
    let alturaExtra = 0, giro = 0, mortal = 0, ladoPerna = 0, dx = 0, dz = 0;

    // ---- camada 2/3: gesto ou acao, com crossfade; o procedural cede nos ossos que a acao usa ----
    let rostoAcao = null, olharAcao = null, firmeza = 0, clipeAtivo = null;
    if (this.acao) {
      const def = ACOES[this.acao.nome];
      this.acao.t += dt;
      // rota (andar/correr): entrada longa, durante a virada parada, para a perna sair da pose parada sem arrastar o pe
      const t = this.acao.t, w = env(t, def.dur, def._rota ? 0.45 : (def.tipo === "acao" ? 0.2 : 0.35), def._rota ? 0.45 : (def.tipo === "acao" ? 0.25 : 0.45));
      def._quadril = null; def._olhar = null;
      const clipe = (def.clipe && this.clipes[def.clipe]) || null;
      const ceder = def.corpoInteiro ? OSSOS : def.ossos;
      // com clipe o procedural NAO e atenuado: o slerp para o clipe (abaixo) ja faz a mistura; atenuar os dois
      // subiria o braco para a T-pose no meio da transicao
      if (!clipe) for (const o of ceder) if (pose[o]) { pose[o][0] *= 1 - w; pose[o][1] *= 1 - w; pose[o][2] *= 1 - w; }
      const dedosAcao = {};
      if (clipe) {
        let tc = def.vezes || def.loop ? t % clipe.dur : Math.min(t, clipe.dur - 1e-3);
        if (def._rota) {
          const r = amostraRota(def._rota, t); tc = ((clipe.fase0 || 0) + r.passos / clipe.vel) % clipe.dur;
          def._quadril = { giro: r.yaw * w, x: r.x * w, z: r.z * w, y: 0, agacha: 0 };
        }
        clipeAtivo = { c: clipe, w, tc, rota: !!def._rota, gesto: def.tipo === "gesto", alt: def.alt || 1 };
      } else def.pose.call(def, pose, dedosAcao, t, w);
      for (const [k, v] of Object.entries(dedosAcao)) { const a = dedos[k] || [0, 0, 0]; dedos[k] = [a[0] * (1 - w) + v[0] * w, a[1] * (1 - w) + v[1] * w, a[2] * (1 - w) + v[2] * w]; }
      if (def._quadril) {
        alturaExtra = def._quadril.y || 0; agacha = agacha * (1 - w) + (def._quadril.agacha || 0);
        giro = def._quadril.giro || 0; mortal = def._quadril.mortal || 0; ladoPerna = def._quadril.ladoPerna || 0;
        dx = def._quadril.x || 0; dz = def._quadril.z || 0;
      }
      firmeza = (def.cabelo || 0) * w;
      rostoAcao = def.rosto ? Object.fromEntries(Object.entries(def.rosto).map(([k, v]) => [k, v * w])) : null;
      olharAcao = def._olhar || def.olhar || null;
      for (const o of ceder) this.estat.donos[o] = "acao:" + this.acao.nome;
      if (mortal) this.estat.giroMortal = Math.min(this.estat.giroMortal, mortal);
      if (t >= def.dur) {
        this.acao = null;
        if (this.vrm.springBoneManager && def.corpoInteiro) this.vrm.springBoneManager.reset();
        if (this.fila.length) this.play(this.fila.shift());
      }
    } else {
      this.microEm -= dt;
      if (this.microEm <= 0 && !this.fala.ativa) { this.play(Math.random() < 0.5 ? "ajeitar_cabelo" : "olhar_em_volta"); this.microEm = rnd(9, 16); }
    }
    pernas(pose, agacha, 1, ladoPerna);
    this._firmarCabelo(firmeza);

    // teto do punho (medido): girar a mao alem disso entorta o antebraco
    for (const m of ["leftHand", "rightHand"]) { const v = pose[m]; v[0] = clamp(v[0], -PUNHO_XZ, PUNHO_XZ); v[1] = clamp(v[1], -PUNHO_Y, PUNHO_Y); v[2] = clamp(v[2], -PUNHO_XZ, PUNHO_XZ); }

    // ---- escreve os ossos (dono unico) ----
    for (const o of OSSOS) { const n = this.no[o]; if (!n) continue; const v = pose[o]; n.rotation.set(v[0], v[1], v[2]); }
    for (const [k, n] of Object.entries(this.dedosNos)) { if (!n) continue; const v = dedos[k] || [0, 0, 0]; n.rotation.set(v[0], v[1], v[2]); }
    // clipe por cima, com o peso da acao (dedos inclusive: o Mesh2Motion anima as falanges)
    for (const n of this.extrasClipe.values()) n.quaternion.identity();
    let cq = null;
    if (clipeAtivo) {
      const wc = clipeAtivo.w;
      const d = amostrar(clipeAtivo.c, clipeAtivo.tc, (b, q) => {
        let n = this.no[b] || this.dedosNos[b];
        if (!n) { n = this.extrasClipe.get(b) || this.h.getNormalizedBoneNode(b); if (!n) return; this.extrasClipe.set(b, n); }
        n.quaternion.slerp(q, wc);
      });
      // gesto parado: o quadril do mocap anda ate 10 cm de lado sem sentido (Salute: +7,7..10,2 cm constante); tira o x/z do 1o quadro
      if (d && clipeAtivo.gesto && !this.semDeriva && clipeAtivo.c.quadril) { d[0] -= clipeAtivo.c.quadril[0]; d[2] -= clipeAtivo.c.quadril[2]; }
      if (d && clipeAtivo.alt !== 1 && d[1] > 0) d[1] *= clipeAtivo.alt; // teto do voo (ACOES_CLIPE.alt): so a subida
      if (d) cq = new THREE.Vector3(d[0], d[1], d[2]).multiplyScalar(wc).applyAxisAngle(EIXO_Y, giro);
    }
    const qh = this.no.hips.quaternion;
    if (giro) qh.premultiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), giro));
    if (mortal) qh.premultiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), mortal));
    const agFinal = agacha;
    this.no.hips.position.set(this.quadrilRepouso.x + dx, this.quadrilRepouso.y - agFinal + alturaExtra, this.quadrilRepouso.z + dz);
    if (cq) this.no.hips.position.add(cq);
    if (clipeAtivo && !this.semChao) this._pisarChao(dt); else this.chaoLift = 0;
    // trava de pe: rota (andar/correr) E gestos parados; dancas/saltos (acao sem rota) soltam o pe de verdade e ficam de fora
    if (clipeAtivo && (clipeAtivo.rota || clipeAtivo.gesto) && !this.semTrava) this._travarPes(clipeAtivo.c, clipeAtivo.tc, clipeAtivo.w, dt);
    this._afastarBracos(dt);
    this._empurrarBracos(dt);
    this._aplicarTransicao(dt);

    // ---- piscar sozinho (com dupla de vez em quando), olhar que passeia ----
    const pis = this.piscar; let blink = 0;
    pis.proxima -= dt;
    if (pis.proxima <= 0 && pis.t < 0) { pis.t = 0; pis.dupla = Math.random() < 0.18; this.estat.piscadas += 1; }
    if (pis.t >= 0) {
      pis.t += dt; const d = 0.075, h = 0.045, a = 0.12, tot = d + h + a;
      const u = pis.t; blink = u < d ? u / d : (u < d + h ? 1 : 1 - (u - d - h) / a);
      if (u > tot) { if (pis.dupla) { pis.dupla = false; pis.t = -0.0001; pis.proxima = 0.12; this.estat.piscadas += 1; } else { pis.t = -1; pis.proxima = rnd(2.6, 5.8) * (this.emocao === "sad" ? 1.4 : 1); } blink = 0; }
    }
    const ol = this.olhar; ol.proxima -= dt;
    if (ol.proxima <= 0) { const forte = Math.random() < 0.3; ol.alvoX = forte ? rnd(-0.9, 0.9) : rnd(-0.35, 0.35); ol.alvoY = rnd(-0.3, 0.35); ol.proxima = rnd(0.9, 3.2); if (!forte) ol.alvoX *= 0.6; }
    const alvo = olharAcao || [ol.alvoX, ol.alvoY];
    const kk = 1 - Math.exp(-dt * 14); ol.x += (alvo[0] - ol.x) * kk; ol.y += (alvo[1] - ol.y) * kk;
    // cabeca acompanha 35% do olhar (como o 2D, HEAD_STABILIZATION_RATIO)
    // a cabeca carrega o olhar: os olhos so andam um pouco (tetos abaixo), entao a cabeca vira e ergue mais
    // a cabeca vai atras do olhar por dois filtros em cascata (CABECA_OLHAR_TAU): parte sem tranco e chega sem tranco
    const oc = this.olharCabeca || (this.olharCabeca = { x1: 0, y1: 0, x: 0, y: 0 }), kc = 1 - Math.exp(-dt / (CABECA_OLHAR_TAU / 2));
    oc.x1 += (ol.x - oc.x1) * kc; oc.y1 += (ol.y - oc.y1) * kc; oc.x += (oc.x1 - oc.x) * kc; oc.y += (oc.y1 - oc.y) * kc;
    this.no.head.rotateY(oc.x * 0.3); this.no.head.rotateX(-oc.y * 0.22);

    // ---- expressoes com bind (dono: three-vrm) ----
    const rosto = { ...prof.rosto };
    if (rostoAcao) {
      // A ACAO MANDA NA BOCA (rodada 8, p110/p116): a acao soma a chave de boca dela com Math.max, mas a boca da EMOCAO e OUTRA chave e as
      // duas apareciam: braveza (t58+t91) + acenar (t93) = 2 manchas de boca visiveis. A boca da emocao cede pelo peso da boca da acao.
      let ma = 0; for (const [k, v] of Object.entries(rostoAcao)) if (BOCAS_EMOCAO.includes(k)) ma = Math.max(ma, v);
      if (ma && !this.semCederBoca) for (const k of BOCAS_EMOCAO) if (rosto[k] && !(k in rostoAcao)) rosto[k] *= 1 - clamp(ma, 0, 1);
      for (const [k, v] of Object.entries(rostoAcao)) rosto[k] = Math.max(rosto[k] || 0, v);
    }
    const boca = Math.max(...Object.values(visemas));
    // UMA boca so: a boca da emocao some enquanto ela fala (somar com a da fala desenhava duas bocas, p51)
    const alvoFala = this.fala.ativa || this.energiaExterna > 0.05 ? 1 : 0;
    this.bocaFala = (this.bocaFala || 0) + (alvoFala - (this.bocaFala || 0)) * (1 - Math.exp(-dt / 0.08));
    for (const k of BOCAS_EMOCAO) if (rosto[k]) rosto[k] *= 1 - this.bocaFala;
    const expr = { blink: 0, aa: visemas.aa, ih: visemas.ih, ou: visemas.ou, ee: visemas.ee, oh: visemas.oh,
      lookLeft: Math.max(0, ol.x) * 0.12, lookRight: Math.max(0, -ol.x) * 0.12, lookUp: 0, lookDown: Math.max(0, -ol.y) * 0.3 };
    for (const [k, v] of Object.entries(rosto)) if (BOUND[k]) { expr[BOUND[k]] = Math.max(expr[BOUND[k]], v); delete rosto[k]; }
    // DUAS/TRES BOCAS (rodada 8, p110/p116/p117): a boca triste (target_62) desenha a PROPRIA boca; com target_105 (amor/provocante, ou a
    // acao "paz") ou com um visema (fala, inicio da fala) a partir de ~0,3-0,45 de peso dos dois aparecem 2 e ate 3 manchas de boca.
    // A triste cede: rampa 0,15 -> 0,40 do maior concorrente. Os outros pares medidos deram 1 mancha e ficam como estavam.
    if (rosto.target_62 && !this.semCederBoca) { const c = Math.max(rosto.target_105 || 0, expr.aa, expr.ih, expr.ou, expr.ee, expr.oh), u = clamp((c - 0.15) / 0.25, 0, 1); rosto.target_62 *= 1 - u * u * (3 - 2 * u); }
    // TETOS DO OLHAR, medidos em close 3x (web/p18_olhar_keys.py): lookUp abre faixa branca entre cilio e iris ja em
    // 0.03-0.06 (a key so mostra o branco) -> desligado; lookLeft/Right encostam a iris no canto a partir de 0.15 -> 0.1;
    // lookDown limpo ate 0.3 -> 0.25. Vale tambem para poses de rosto que usam essas keys (triste usava lookDown 0.35).
    expr.lookUp = 0; expr.lookLeft = Math.min(expr.lookLeft, OLHAR_LADO); expr.lookRight = Math.min(expr.lookRight, OLHAR_LADO);
    expr.lookDown = Math.min(expr.lookDown, OLHAR_BAIXO);
    const olhosFechadosEmocao = Math.max(rosto.target_48 || 0, rosto.target_74 ? rosto.target_74 * 0.4 : 0);
    expr.blink = Math.max(blink * (1 - olhosFechadosEmocao), 0);
    for (const [n, v] of Object.entries(expr)) this.em.setValue(n, clamp(v, 0, 1));
    this.rostoAtual = rosto;
    for (const o of OSSOS) if (!this.estat.donos[o] || !this.acao) this.estat.donos[o] = this.acao && (ACOES[this.acao.nome].corpoInteiro || ACOES[this.acao.nome].ossos.includes(o)) ? "acao:" + this.acao.nome : "procedural";
  }

  // depois do vrm.update: keys do rosto SEM bind (o three-vrm nao as toca). Zera o que saiu.
  aplicarRosto() {
    const novo = new Set(Object.keys(this.rostoAtual));
    for (const k of this.rostoEscrito) if (!novo.has(k)) for (const [m, i] of this.mapaKeys[k] || []) m.morphTargetInfluences[i] = 0;
    for (const [k, v] of Object.entries(this.rostoAtual)) for (const [m, i] of this.mapaKeys[k] || []) m.morphTargetInfluences[i] = clamp(v, 0, 1);
    this.rostoEscrito = novo;
    this._corretivaBracos();
  }

  // FORMA CORRETIVA DO BRACO LEVANTADO (rodada 15, pedido do Pai com as linhas desenhadas): o .vrm traz braco_cima_E/D, esculpidas com o umero
  // 70 graus acima da horizontal (ombro vira tubo ate o corpo: sem a "asa" embaixo, topo encostando na gola). Peso = elevacao do umero acima da
  // horizontal do PEITO, suavizada de 0 a 70 graus; com o braco abaixado fica 0 e entra a braco_baixo (0 a 65 graus abaixo). Sem as keys no .vrm nao faz nada. ?semcorretiva = A/B.
  _corretivaBracos() {
    if (!this._upPeito) return;
    const C = this._corr || (this._corr = { a: new THREE.Vector3(), b: new THREE.Vector3(), up: new THREE.Vector3(), q: new THREE.Quaternion() });
    this._peitoRaw.getWorldQuaternion(C.q); C.up.copy(this._upPeito).applyQuaternion(C.q);
    // braco_baixo_E/D: a frente da manga cobre a borda da frente da jaqueta na cava (costura reta), esculpida com o umero 65 graus abaixo da horizontal
    for (const [lado, cima, baixo] of [["left", "braco_cima_E", "braco_baixo_E"], ["right", "braco_cima_D", "braco_baixo_D"]]) {
      let wc = 0, wb = 0;
      if (!this.semCorretiva) {
        this.h.getRawBoneNode(lado + "UpperArm").getWorldPosition(C.a); this.h.getRawBoneNode(lado + "LowerArm").getWorldPosition(C.b);
        const elev = Math.asin(clamp(C.b.sub(C.a).normalize().dot(C.up), -1, 1));
        const tc = clamp(elev / (70 * DEG), 0, 1), tb = clamp(-elev / (65 * DEG), 0, 1);
        wc = tc * tc * (3 - 2 * tc); wb = tb * tb * (3 - 2 * tb);
      }
      for (const [m, i] of this.mapaKeys[cima] || []) m.morphTargetInfluences[i] = wc;
      for (const [m, i] of this.mapaKeys[baixo] || []) m.morphTargetInfluences[i] = wb;
      (this.estat.corretiva || (this.estat.corretiva = {}))[lado] = Math.round(wc * 100) / 100;
      (this.estat.corretivaBaixo || (this.estat.corretivaBaixo = {}))[lado] = Math.round(wb * 100) / 100;
    }
  }

  estado() {
    return { emocao: this.emocao, preset: this.preset, acao: this.acao && this.acao.nome, acaoT: this.acao && this.acao.t,
      falando: this.fala.ativa, energia: this.fala.energia, piscadas: this.estat.piscadas, acoes: this.estat.acoes,
      giroMortalGraus: Math.round(-this.estat.giroMortal / DEG), donos: { ...this.estat.donos } };
  }
}
