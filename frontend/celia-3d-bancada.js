// celia-3d-bancada.js — bancada da Celia 3D (Kurt, 2026-10-06, rodada 2).
// Isolada da interface: nao ouve nem emite eventos celia-* e nao toca no #canvas do Live2D.
// O corpo animado mora em celia-3d-corpo.js (presets/emocoes herdados do 2D + gestos e acoes do 3D).
// Espiao para prova: window.celia3dBancada.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { Corpo3D, ACOES, PROFILES } from "./celia-3d-corpo.js?v=corpo20261029r15q";
import { OmbroSuave } from "./celia-3d-ombro.js?v=ombro20261006r4";
import { limparCabelo } from "./celia-3d-cabelo.js?v=cabelo20261006d";
import { repesarMaos } from "./celia-3d-maos.js?v=maos20261006c";
import { limparSaia } from "./celia-3d-saia.js?v=saia20261029r15";
import { criarCauda } from "./celia-3d-cauda.js?v=cauda20261006b";
import { transplantarMaos } from "./celia-3d-mao-doada.js?v=maodoada20261029r15d";
import { criarOculos } from "./celia-3d-oculos.js?v=oculos20261018c";
import { carregarM2M, carregarVRMA, carregarKayKit } from "./celia-3d-clipes.js?v=clipes20261006i";
import { Curadoria } from "./celia-3d-curadoria.js?v=curadoria20261006b";
import { KAYKIT, CLAWATAR } from "./celia-3d-catalogo.js?v=catalogo20261006d";
import { MEDIDAS } from "./celia-3d-medidas.js?v=medidas20261006a";

// ?vrm=<arquivo>.vrm troca o modelo (so arquivos de ./modelos-3d/, nome simples) para comparar versoes lado a lado
const VRM_PEDIDO = new URLSearchParams(location.search).get("vrm");
const URL_VRM = "./modelos-3d/" + (VRM_PEDIDO && /^[\w.-]+\.vrm$/.test(VRM_PEDIDO) ? VRM_PEDIDO : "celia_v3_web.vrm");
const ALVO = { blink: "target_6", aa: "target_2", ih: "target_3", ou: "target_67", ee: "target_12", oh: "target_35",
  lookLeft: "target_113", lookRight: "target_114", lookUp: "target_115", lookDown: "target_116" };
const Q = new URLSearchParams(location.search);
const PROVA = Q.has("prova");
// ?app=1 (integracao na interface, 2026-10-07): sem painel, sem demonstracao; so as acoes APROVADAS (celia-3d-aprovadas.json) tocam; fala por mensagem do pai.
const APP = Q.has("app");
let APROVADAS = new Set();
if (APP) { document.body.classList.add("app"); document.documentElement.style.background = "transparent"; try { const r = await fetch("./celia-3d-aprovadas.json", { cache: "no-store" }); APROVADAS = new Set((await r.json()).aprovadas || []); } catch (e) { console.warn("aprovadas:", e); } }
const NOMES_EMOCAO = { neutral: "neutra", happy: "feliz", in_love: "apaixonada", sad: "triste", angry: "brava", surprised: "surpresa",
  confused: "confusa", scared: "assustada", teasing: "provocando", funny: "rindo", holding_back_laughter: "segurando riso", passionate: "empolgada" };
const NOMES_ACAO = { acenar: "acenar", paz: "V de paz", andar: "andar", espreguicar: "espreguiçar", pensar: "pensativa", comemorar: "comemorar", timida: "tímida",
  pulinho: "pulinho", giro: "giro", dancinha: "dancinha", mortal: "mortal pra trás",
  correr: "correr", vitoria: "vitória", pulo_vitoria: "pulo de vitória", polichinelo: "polichinelo", charleston: "charleston",
  danca_quadril: "dança do quadril", backflip: "mortal (clipe)", confusa: "confusa", sim: "sim", cumprimentar: "cumprimentar",
  reverencia: "reverência", continencia: "continência", tonta: "tonta", recusar: "recusar" };
// clipes (rodada 6): Mesh2Motion CC0 por padrao. ?clawatar=1 soma 4 clipes Mixamo do Clawatar (NON-COMMERCIAL: so experimento).
const M2M = { "human-base-animations.glb": ["Idle_A", "Yes"], "human-addon-animations.glb": ["Walk_Female", "Run_Female", "Victory", "Victory Fist Pump",
  "Jumping Jacks", "Dance Charleston", "Dance Reach Hip", "Backflip", "Confused", "Greeting", "Bow", "Dizzy", "Reject"],
  "human-mocap-animations.glb": ["Cheering_Two_Hands", "Salute"] };
// KAYKIT e CLAWATAR (catalogo de clipes extras) moram em celia-3d-catalogo.js; a curadoria (o "x" em cada botao) em celia-3d-curadoria.js

const palco = document.getElementById("palco");
const $ = (id) => document.getElementById(id);
const api = { estado: "carregando", erro: null, vrm: null, corpo: null, fps: 0, msQuadro: 0, quadros: 0, tamanho: [0, 0, 0] };
window.celia3dBancada = api;
function mostrar(s, texto) { api.estado = s; $("estado").dataset.s = s; $("estado").textContent = texto; }

// palco: canvas transparente sobre vinheta carvao (como o cartao 3D da referencia do Pai)
if (!APP) palco.style.background = "radial-gradient(ellipse at 50% 42%, #3a3a3a 0%, #2b2b2b 45%, #161616 100%)";
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: PROVA || Q.has("mobileStudio") });
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.setClearColor(0x000000, 0);
renderer.domElement.style.opacity = "0"; renderer.domElement.style.transition = "opacity .9s ease";
palco.appendChild(renderer.domElement);
const cena = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(28, 1, 0.05, 50);
cena.add(new THREE.AmbientLight(0xffffff, 1.0));

// sombra de contato no chao (textura radial), acompanha o quadril
const sombraTex = (() => {
  const c = document.createElement("canvas"); c.width = c.height = 128; const g = c.getContext("2d");
  const r = g.createRadialGradient(64, 64, 4, 64, 64, 64); r.addColorStop(0, "rgba(0,0,0,.55)"); r.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = r; g.fillRect(0, 0, 128, 128); return new THREE.CanvasTexture(c);
})();
const sombra = new THREE.Mesh(new THREE.PlaneGeometry(0.9, 0.55), new THREE.MeshBasicMaterial({ map: sombraTex, transparent: true, depthWrite: false }));
sombra.rotation.x = -Math.PI / 2; sombra.position.y = 0.002; cena.add(sombra);

// camera: corpo inteiro ou busto; abre sozinha para corpo inteiro (com folga) durante acoes grandes
const CAMERAS = { corpo: { pos: [0, 0.9, 3.55], olha: [0, 0.84, 0] }, busto: { pos: [0, 1.3, 1.25], olha: [0, 1.27, 0] },
  acao: { pos: [0, 1.1, 4.9], olha: [0, 1.05, 0] }, mortal: { pos: [0, 1.2, 5.9], olha: [0, 1.12, 0] } };
let modoCamera = Q.get("camera") === "busto" ? "busto" : "corpo";
const camPos = new THREE.Vector3(...CAMERAS[modoCamera].pos), camOlha = new THREE.Vector3(...CAMERAS[modoCamera].olha);
camera.position.copy(camPos); camera.lookAt(camOlha);


// ?dividir=1 (pedido do Pai, rodada 7): esquerda = Celia normal; direita = a MESMA pose e camera como ossos + malha em
// aramado, para ver se o defeito esta no osso ou na malha. So a vista muda: nada da cena e alterado alem de override.
const DIVIDIR = Q.has("dividir");
// ?dist= (so no ?dividir=1; pedido do Pai, rodada 9): fracao que resta da distancia ENTRE o modelo (esquerda) e os ossos (direita).
// Cada boneco esta centrado na sua metade (distancia = meia largura da tela); 0.5 = metade da distancia (padrao), 1 = como era.
const DIST = Math.min(1, Math.max(0.2, Number(Q.get("dist")) || 0.5));
let ossosVista = null; const aramado = new THREE.MeshBasicMaterial({ color: 0x8aa0b8, wireframe: true, transparent: true, opacity: 0.35, depthWrite: false });
function renderizar() {
  if (!DIVIDIR) { renderer.render(cena, camera); return; }
  const pr = renderer.getPixelRatio(), W = renderer.domElement.width / pr, H = renderer.domElement.height / pr, meio = Math.floor(W / 2); // unidades CSS
  camera.aspect = meio / H; camera.updateProjectionMatrix();
  renderer.autoClear = false; renderer.setScissorTest(false); renderer.setViewport(0, 0, W, H); renderer.clear();
  renderer.setScissorTest(true);
  // aproxima os dois bonecos: cada vista desloca a imagem por `desloc` px rumo ao meio (setViewOffset com x negativo empurra para a direita)
  const desloc = (1 - DIST) * meio / 2;
  renderer.setViewport(0, 0, meio, H); renderer.setScissor(0, 0, meio, H);
  if (ossosVista) ossosVista.visible = false;
  camera.setViewOffset(meio, H, -desloc, 0, meio, H);
  renderer.render(cena, camera);
  renderer.setViewport(meio, 0, W - meio, H); renderer.setScissor(meio, 0, W - meio, H);
  camera.setViewOffset(meio, H, desloc, 0, meio, H);
  renderer.clearDepth(); cena.overrideMaterial = aramado; renderer.render(cena, camera); cena.overrideMaterial = null;
  if (ossosVista) { // ossos por cima, sem override (o override trocaria o material das linhas); so eles visiveis nesta passada
    const vis = []; cena.traverse((o) => { if (o !== cena && o.visible && (o.isMesh || o.isSkinnedMesh || o.isSprite)) { vis.push(o); o.visible = false; } });
    ossosVista.visible = true; ossosVista.material.depthTest = false; ossosVista.material.transparent = true; renderer.clearDepth(); renderer.render(cena, camera);
    ossosVista.visible = false; for (const o of vis) o.visible = true;
  }
  camera.clearViewOffset();
  renderer.setScissorTest(false);
}

// ?ss=2 (rodada 8, OPT-IN): supersampling. O teto de pixel ratio nao ajuda quem esta em dpr 1; para a escada do cabelo sumir
// ai e preciso desenhar a SS x SS pixels e o navegador reduzir (o CSS do canvas continua 100%). Custa ~SS^2 de pixels: nao e
// o padrao; o FPS medido com e sem esta em p112. Limite 1..3.
const SS = Math.min(3, Math.max(1, Number(new URLSearchParams(location.search).get("ss")) || 1));
// Armadilha 1 do contexto: atribuir tamanho apaga o buffer WebGL. So redimensiona quando MUDOU, com o dpr real.
function ajustarTamanho() {
  const w = palco.clientWidth, h = palco.clientHeight, dpr = window.devicePixelRatio || 1;
  if (w === api.tamanho[0] && h === api.tamanho[1] && dpr === api.tamanho[2]) return;
  api.tamanho = [w, h, dpr];
  renderer.setPixelRatio(dpr * SS); renderer.setSize(w, h, false);
  camera.aspect = w / Math.max(1, h); camera.updateProjectionMatrix();
}
window.addEventListener("resize", ajustarTamanho);
ajustarTamanho();

// ---- painel ----
function botao(pai, texto, fn) { const b = document.createElement("button"); b.type = "button"; b.textContent = texto; b.addEventListener("click", fn); $(pai).appendChild(b); return b; }
// curadoria (rodada 9): cada botao de gesto/acao vira um chip com "x"; o que sai fica salvo e some da demonstracao automatica
const curadoria = new Curadoria($("g-curadoria")); api.curadoria = curadoria;
const DICAS = MEDIDAS; // chave da acao -> numeros medidos (title do botao), de celia-3d-medidas.js
function botaoAcao(grupo, chave, texto, fn, dica = "") { return curadoria.chip($(grupo), chave, texto, fn, dica); }
const botoesEmocao = {};
for (const e of Object.keys(PROFILES)) botoesEmocao[e] = botao("g-emocao", NOMES_EMOCAO[e] || e, () => { pararDemo(); api.corpo && api.corpo.setEmotion(e); });
for (const [k, d] of Object.entries(ACOES)) if (d.tipo !== "micro") botaoAcao(d.tipo === "acao" ? "g-acao" : "g-gesto", k, NOMES_ACAO[k] || k, () => { pararDemo(); api.corpo && api.corpo.play(k, true); }); // botao interrompe na hora
$("falar").addEventListener("click", () => { pararDemo(); api.corpo && api.corpo.falar(3.4); });
$("camera").addEventListener("click", () => { modoCamera = modoCamera === "corpo" ? "busto" : "corpo"; $("camera").textContent = "câmera: " + (modoCamera === "corpo" ? "corpo inteiro" : "busto"); });
$("recolher").addEventListener("click", () => { const r = $("painel").classList.toggle("recolhido"); $("recolher").textContent = r ? "abrir" : "recolher"; $("recolher").setAttribute("aria-expanded", String(!r)); });
$("camera").textContent = "câmera: " + (modoCamera === "corpo" ? "corpo inteiro" : "busto");

// ---- demonstracao automatica: passa por emocoes, fala, gestos e acoes (o mortal inclusive) ----
const ROTEIRO = [
  [0, "emo", "happy"], [0.6, "play", "andar"], [8.6, "play", "cumprimentar"], [13.6, "falar", 3.0], [17.0, "play", "paz"],
  [20.0, "emo", "surprised"], [20.4, "play", "pulo_vitoria"], [22.4, "emo", "teasing"], [22.8, "falar", 2.6],
  [25.8, "play", "confusa"], [29.0, "emo", "funny"], [29.4, "play", "charleston"], [34.4, "emo", "sad"], [34.8, "play", "timida"],
  [38.4, "emo", "passionate"], [38.8, "falar", 3.0], [42.2, "emo", "happy"], [42.4, "play", "polichinelo"],
  [46.4, "play", "comemorar"], [50.0, "emo", "surprised"], [50.4, "play", "backflip"], [53.2, "emo", "happy"],
  [53.6, "play", "correr"], [59.0, "falar", 2.4], [61.8, "emo", "neutral"], [62.2, "play", "espreguicar"], [66.4, "fim"],
];
const demo = { ligada: !APP && !Q.has("parado") && Q.get("demo") !== "0", t: 0, i: 0 };
function pararDemo() { demo.ligada = false; $("demo").setAttribute("aria-pressed", "false"); $("demo").textContent = "automática: desligada"; }
$("demo").addEventListener("click", () => { demo.ligada = !demo.ligada; demo.t = 0; demo.i = 0; $("demo").setAttribute("aria-pressed", String(demo.ligada)); $("demo").textContent = "automática: " + (demo.ligada ? "ligada" : "desligada"); });
if (!demo.ligada) pararDemo();
function passoDemo(dt) {
  if (!demo.ligada || !api.corpo) return;
  demo.t += dt;
  while (demo.i < ROTEIRO.length && demo.t >= ROTEIRO[demo.i][0]) {
    const [, tipo, arg] = ROTEIRO[demo.i++];
    if (tipo === "emo") api.corpo.setEmotion(arg); else if (tipo === "play") { if (!curadoria.descartada(arg)) api.corpo.play(arg); }
    else if (tipo === "falar") api.corpo.falar(arg); else if (tipo === "fim") { demo.t = 0; demo.i = 0; }
  }
}

// ---- espiao (mesmo contrato da rodada 1) ----
function malhaDoAlvo(nomeKey) { const r = []; if (!api.vrm) return r; api.vrm.scene.traverse((o) => { if (o.isMesh && o.morphTargetDictionary && nomeKey in o.morphTargetDictionary) r.push(o); }); return r; }
api.influencia = (expr) => { const k = ALVO[expr]; return malhaDoAlvo(k).map((m) => ({ malha: m.name, valor: m.morphTargetInfluences[m.morphTargetDictionary[k]] })); };
api.expressao = (nome, peso) => { const em = api.vrm && api.vrm.expressionManager; if (!em) return false; em.setValue(nome, peso); api.vrm.update(0); return em.getValue(nome); };
api.expressoes = () => (api.vrm && api.vrm.expressionManager ? api.vrm.expressionManager.expressions.map((e) => e.expressionName) : []);
api.ossos = () => (api.vrm ? Object.keys(api.vrm.humanoid.humanBones).filter((k) => api.vrm.humanoid.getRawBoneNode(k)) : []);
api.mundo = (osso) => { const n = api.vrm.humanoid.getRawBoneNode(osso); const v = new THREE.Vector3(); n.getWorldPosition(v); return v.toArray(); };
api.acoes = ACOES; // so leitura: as reguas leem def.dur/rosto/tipo sem importar o modulo por versao (a versao muda a cada edicao)
api.pausado = Q.has("pausado"); // ?pausado: prova deterministica (relogio parado desde o carregamento)
// camera exposta so para LEITURA (rodada 8, Fase 0): a regua do "mortal sai do quadro" nunca capturou a camera (cam: None x6)
// e media o vazio. Getter sem setter: quem le projeta ossos com ela; ninguem a move por aqui (para isso existe verDe()).
Object.defineProperty(api, "camera", { get: () => camera, enumerable: true });
// so para prova: olhar de outro angulo sem mexer na camera do usuario
api.cameraLivre = false;
api.verDe = (pos, olha) => { api.cameraLivre = true; camera.position.set(...pos); camera.lookAt(...olha); renderizar(); };
// avanco deterministico para prova: pausa o relogio e simula `seg` segundos em passos fixos, depois desenha 1 quadro
api.avancar = (seg, passo = 1 / 60) => {
  api.pausado = true;
  for (let t = 0; t < seg - 1e-9; t += passo) { passoDemo(passo); api.corpo.update(passo); api.vrm.update(passo); api.corpo.distribuirTorcao(); api.corpo.aplicarRosto(); }
  if (api.ombro) api.ombro.atualizar();
  const st = api.corpo.estado(); const grande = st.acao && (ACOES[st.acao].tipo === "acao" || ACOES[st.acao].cameraAmpla); const alvo = CAMERAS[st.acao === "mortal" || st.acao === "backflip" ? "mortal" : (grande ? "acao" : modoCamera)];
  const q = api.vrm.humanoid.getRawBoneNode("hips").getWorldPosition(tmpA); // a camera acompanha o quadril (andar/correr)
  camPos.set(...alvo.pos).add(tmpB.set(q.x, 0, q.z)); camOlha.set(...alvo.olha).add(tmpB.set(q.x, 0, q.z)); camera.position.copy(camPos); camera.lookAt(camOlha);
  renderizar(); return st;
};

// ---- laco ----
const relogio = new THREE.Timer();
let janela = performance.now(), contados = 0;
const tmpA = new THREE.Vector3(), tmpB = new THREE.Vector3(), tmpC = new THREE.Vector3();
function quadro(ts) {
  requestAnimationFrame(quadro);
  ajustarTamanho();
  relogio.update(ts);
  const t0 = performance.now();
  const dt = api.pausado ? 0 : Math.min(relogio.getDelta(), 0.1);
  if (api.vrm && api.corpo && !api.pausado) { // pausado (prova): so o avancar() mexe no corpo; o laco so desenha
    passoDemo(dt);
    api.corpo.update(dt);
    api.vrm.update(dt);
    api.corpo.distribuirTorcao();
    api.corpo.aplicarRosto();
    if (api.ombro) api.ombro.atualizar();
    const quadril = api.vrm.humanoid.getRawBoneNode("hips").getWorldPosition(tmpA);
    sombra.position.x = quadril.x; sombra.position.z = quadril.z;
    const s = THREE.MathUtils.clamp(1 - (quadril.y - 0.87) * 1.2, 0.45, 1.15); sombra.scale.set(s, s, 1); sombra.material.opacity = s;
    const st = api.corpo.estado();
    const grande = st.acao && (ACOES[st.acao].tipo === "acao" || ACOES[st.acao].cameraAmpla);
    const alvo = CAMERAS[st.acao === "mortal" || st.acao === "backflip" ? "mortal" : (grande ? "acao" : modoCamera)];
    const k = 1 - Math.exp(-dt * (grande ? 3.5 : 2.2));
    // a camera acompanha o quadril na horizontal: na rota do andar/correr ela ia a 1,4 m e saia de quadro (p2, janela estreita).
    // Rodada 8 (p111): em janela estreita (480x800) o correr saia do quadro em 120 de 269 quadros porque a camera chegava atrasada
    // (lerp de 3,5/s a 2,5 m/s = ~0,7 m de atraso, e a meia-largura visivel e ~0,69 m). Na rota o x/z segue bem mais rapido.
    const kh = 1 - Math.exp(-dt * (st.acao && ACOES[st.acao]._rota ? 12 : (grande ? 3.5 : 2.2)));
    tmpB.set(...alvo.pos).add(tmpC.set(quadril.x, 0, quadril.z));
    camPos.y += (tmpB.y - camPos.y) * k; camPos.x += (tmpB.x - camPos.x) * kh; camPos.z += (tmpB.z - camPos.z) * kh;
    tmpB.set(...alvo.olha).add(tmpC);
    camOlha.y += (tmpB.y - camOlha.y) * k; camOlha.x += (tmpB.x - camOlha.x) * kh; camOlha.z += (tmpB.z - camOlha.z) * kh;
    if (!api.cameraLivre) { camera.position.copy(camPos); camera.lookAt(camOlha); }
    if (api.quadros % 15 === 0) $("agora").textContent = "emoção " + (NOMES_EMOCAO[st.emocao] || st.emocao) + " · " + st.preset
      + (st.acao ? " · " + (NOMES_ACAO[st.acao] || st.acao.replace("_", " ")) : "") + (st.falando ? " · falando" : "") + " · piscadas " + st.piscadas;
    for (const [e, b] of Object.entries(botoesEmocao)) b.setAttribute("aria-pressed", String(e === st.emocao));
  }
  renderizar();
  api.msQuadro = performance.now() - t0; api.quadros += 1; contados += 1;
  const agora = performance.now();
  if (agora - janela >= 1000) {
    api.fps = (contados * 1000) / (agora - janela); contados = 0; janela = agora;
    $("fps").textContent = "FPS " + api.fps.toFixed(1) + " · CPU do quadro " + api.msQuadro.toFixed(1) + " ms";
  }
}

const loader = new GLTFLoader();
loader.register((parser) => new VRMLoaderPlugin(parser));
const t0 = performance.now();
loader.load(URL_VRM, async (gltf) => {
  const vrm = gltf.userData.vrm;
  if (!vrm) { api.erro = "sem gltf.userData.vrm"; mostrar("erro", "arquivo sem VRM"); return; }
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);
  vrm.scene.traverse((o) => { o.frustumCulled = false; });
  cena.add(vrm.scene);
  // pontinhos brancos do bake no cabelo (p52): limpa por ilha de UV antes de compilar; ?cabelocru e o controle A/B
  api.cabeloLimpo = Q.has("cabelocru") ? null : limparCabelo(vrm);
  // manchas da saia (p82, rodada 6): mesma limpeza por ilha de UV, so na faixa da saia; ?saiacrua = controle A/B
  api.saiaLimpa = Q.has("saiacrua") ? null : limparSaia(vrm);
  // dedos que dobram sem rasgar (p56): pesos novos da mao, ANTES do corretor do ombro copiar os pesos; ?maocrua = controle
  // rodada 6: mao TRANSPLANTADA de uma VRM bem rigada (pedido do Pai; a do .vrm e sopa de triangulos com dedos
  // fundidos, p72). Doadora pixiv VRM1_Constraint_Twist_Sample (VRM Public License 1.0: modificar/redistribuir/comercial).
  // ?maoantiga = controle A/B (mao original com os pesos da rodada 5)
  api.maos = null; api.maoNova = null;
  if (!Q.has("maocrua")) {
    const alvo = []; vrm.scene.traverse((o) => { if (o.isSkinnedMesh && o.name.startsWith("V3_corpo_normal")) alvo.push(o); });
    for (const o of alvo) {
      api.maos = repesarMaos(o, vrm.humanoid); // poe no esqueleto os ossos do dedo medio que faltavam
      if (!Q.has("maoantiga")) {
        try { api.maoNova = await transplantarMaos(o, vrm, "./modelos-3d/anim/doadores/VRM1_Constraint_Twist_Sample.vrm", { escala: Number(Q.get("escalamao")) || 0.85, peleAntiga: Q.has("peleantiga"), corte: Q.has("cortemao") ? Number(Q.get("cortemao")) : 0.08 /* rodada 8 (p119/p121): com 0,3 o toco da doadora furava a manga e aparecia como pele laranja sobre o punho */, ...(Q.has("dentromanga") ? { dentroManga: Number(Q.get("dentromanga")) } : {}), centroManga: !Q.has("maoantiga"), bordaPunho: !Q.has("semborda") /* rodada 15: ?maoantiga = sem centralizar no punho */ }); } // 0.85: o Pai achou a mao grande (p99)
        catch (e) { api.maoNova = { ok: false, erro: String((e && e.message) || e) }; console.warn("mao doada:", api.maoNova.erro); }
      }
    }
  }
  // cauda: a malha "other" vinha 100% presa na coluna (bloco rigido que a perna atravessa, p81). Ossos + mola na carga,
  // ANTES do Corpo3D (ele poe os colisores do corpo em todas as juntas). ?caudarigida = controle A/B
  // ?caudaraio=0.11 / ?caudadir=x,y,z / ?caudarigidez=1.6: varredura da prova (p83), nao uso normal
  const numQ = (k) => (Q.has(k) ? Number(Q.get(k)) : undefined);
  api.cauda = Q.has("caudarigida") ? null : criarCauda(vrm, { raio: numQ("caudaraio"), rigidez: numQ("caudarigidez"),
    direcao: Q.has("caudadir") ? new THREE.Vector3(...Q.get("caudadir").split(",").map(Number)) : undefined });
  // cauda (rodada 8, Passo 3): textura 4096 com mipmap e anisotropia 1 (maximo da GPU: 16) -> borra de lado. Barato: 8.
  // ?caudaaniso=N troca o valor (1 = o comportamento antigo, controle A/B da p112).
  api.caudaAniso = (() => {
    const pedido = Q.has("caudaaniso") ? Number(Q.get("caudaaniso")) : 8, max = renderer.capabilities.getMaxAnisotropy(), usado = Math.max(1, Math.min(pedido || 1, max)), vistas = new Set();
    vrm.scene.traverse((o) => { if (!o.isMesh || o.name !== "other") return;
      for (const mat of (Array.isArray(o.material) ? o.material : [o.material])) for (const t of [mat.map, mat.uniforms && mat.uniforms.map && mat.uniforms.map.value]) if (t && !vistas.has(t)) { t.anisotropy = usado; t.needsUpdate = true; vistas.add(t); } });
    return { pedido, max, usado, texturas: vistas.size };
  })();
  mostrar("carregando", "compilando…");
  await renderer.compileAsync(cena, camera);
  api.vrm = vrm;
  api.oculos = criarOculos(vrm, { raio: numQ("oculosraio") || undefined, frente: numQ("oculosfrente") || undefined }); api.oculos.setVisivel(oculosPedido || Q.has("oculos")); // ordem do Pai 2026-10-18: opcao Corpo > Oculos
  if (DIVIDIR) { ossosVista = new THREE.SkeletonHelper(vrm.scene); ossosVista.material.color.set(0xffd166); ossosVista.visible = false; cena.add(ossosVista); }
  api.corpo = Q.has("parado") ? null : new Corpo3D(vrm, { colisores: !Q.has("semcolisor"), cabeloFirme: !Q.has("cabelosolto"), bracoSolto: Number(Q.get("bracosolto")) || 0,
    cabeloAtras: !Q.has("cabelofrente"), paredeZ: Number(Q.get("paredez")) || 0 }); // ?semcolisor / ?cabelosolto: controles A/B do cabelo (p38); ?cabelofrente: sem a parede (rodada 15)
  // ombro sem ponta (p20): Corrective Smooth por quadro na faixa do ombro; ?semombro e o controle A/B
  api.ombro = null;
  if (!Q.has("semombro")) {
    const lista = [], raizes = ["leftShoulder", "rightShoulder", ...(Q.has("ombropernas") ? ["leftUpperLeg", "rightUpperLeg"] : [])].map((n) => vrm.humanoid.getRawBoneNode(n) || vrm.humanoid.getRawBoneNode(n.replace("Shoulder", "UpperArm"))).filter(Boolean);
    vrm.scene.traverse((o) => { if (o.isSkinnedMesh && o.name.startsWith("V3_corpo_normal")) lista.push(new OmbroSuave(o, { raizes, passes: Number(Q.get("ombropasses")) || 10, ...(Q.has("ombroymin") ? { yMin: Number(Q.get("ombroymin")) } : {}) })); });
    api.ombro = { lista, ms: 0, atualizar() {
      vrm.scene.updateMatrixWorld(true); let ms = 0; for (const o of lista) { o.atualizar(); ms += o.ms; } this.ms = ms;
    } };
  }
  api.clipes = { ok: false, nomes: [], ms: 0, erro: null };
  if (api.corpo && Q.has("semtrava")) api.corpo.semTrava = true; // controle A/B da trava do pe (p70)
  if (api.corpo && Q.has("semcederboca")) api.corpo.semCederBoca = true; // controle A/B da boca triste que cede (p110/p116, rodada 8)
  // rodada 15: o .vrm com os bracos refeitos (tem a key braco_cima_E) traz o degrade de pesos arm -> arm.001 -> arm.002 que a torcao espalhada usa:
  // nele ela vem LIGADA (o braco nao afina no ombro ao comemorar); ?semtorcao desliga. No .vrm antigo continua opt-in (?torcao).
  // (a suspeita de que a torcao tirava a mao do lugar era erro de MEDIDA: desligada, ela deixava giro velho em arm001/002. Corrigido em distribuirTorcao;
  // com a cadeia zerada antes, a mao e o cotovelo ficam exatamente no lugar: 0,00 cm em paz/cumprimentar/acenar/comemorar/continencia.)
  if (api.corpo && api.corpo.mapaKeys && api.corpo.mapaKeys.braco_cima_E && !Q.has("semtorcao")) api.corpo.torcao = true;
  if (api.corpo && Q.has("torcao")) { api.corpo.torcao = true; if (Number(Q.get("torcao")) > 0 && Number(Q.get("torcao")) <= 1) api.corpo.torcaoFator = Number(Q.get("torcao")); if (Q.has("c1") && Q.has("c2")) api.corpo.torcaoCortes = [Number(Q.get("c1")), Number(Q.get("c2"))]; } // experimento do Pai (comemorar): ?torcao=1 espalha a torcao do umero
  if (api.corpo && Q.has("semcolisorbarriga")) api.corpo.ligarBarriga(false); // controle A/B do cabelo que atravessa a barriga (rodada 10)
  if (api.corpo && Q.has("semempurrar")) api.corpo.semEmpurrar = true; // controle A/B do braco dentro do tronco/cabeca (rodada 10)
  if (api.corpo && Q.has("semafastar")) api.corpo.semAfastar = true; // controle A/B do braco que sai da coxa (p104, rodada 8)
  if (api.corpo && Q.has("saia")) api.corpo.semSaia = false; // casca da saia contra as maos (rodada 15): desligada por padrao, dava trancos no braco ao andar
  if (api.corpo && Q.has("empurraantigo")) { api.corpo.empurraMax = 20; api.corpo.empurraTol = 0.015; } // controle A/B do empurrao do braco (rodada 15: 40 graus / 0,5 cm)
  if (api.corpo && Q.has("semcorretiva")) api.corpo.semCorretiva = true; // controle A/B da forma corretiva do braco levantado (rodada 15)
  if (api.corpo && Q.has("semchao")) api.corpo.semChao = true; // controle A/B do apoio no chao (p109, rodada 8)
  if (api.corpo && Q.has("semderiva")) api.corpo.semDeriva = true; // controle A/B da deriva do quadril nos gestos (p109, rodada 8)
  if (api.corpo && !Q.has("semclipes")) {
    const tc = performance.now();
    try {
      const dic = {};
      for (const [arq, nomes] of Object.entries(M2M)) Object.assign(dic, await carregarM2M("./modelos-3d/anim/m2m/" + arq.split("#")[0], vrm, nomes));
      // Clawatar (.vrma Mixamo, NON-COMMERCIAL): so com ?clawatar=1. Um arquivo que falta nao derruba os outros (o texto que pediu estes clipes
      // citava 3 que nao existem no clone: 35_Female Peek And Aim, 16_Cheering, 40_Happy Idle).
      api.clawatar = { ok: [], erro: {} };
      if (Q.has("clawatar")) for (const [id, [rotulo, rosto, tipo = "gesto"]] of Object.entries(CLAWATAR)) {
        try {
          dic["vrma:" + id] = await carregarVRMA("./modelos-3d/anim/clawatar/" + encodeURIComponent(id) + ".vrma", vrm, id);
          const k = "mx_" + id.replace(/\W/g, "_"); ACOES[k] = { dur: 3, tipo, soClipe: true, clipe: "vrma:" + id, ossos: [], rosto, pose() {} };
          NOMES_ACAO[k] = rotulo; botaoAcao(tipo === "acao" ? "g-acao" : "g-gesto", k, rotulo, () => { pararDemo(); api.corpo.play(k, true); }); api.clawatar.ok.push(k);
        } catch (e) { api.clawatar.erro[id] = String((e && e.message) || e); console.warn("clawatar:", id, api.clawatar.erro[id]); }
      }
      // KayKit: so as acoes cujo clipe carregou entram (botao morto e defeito); falha de um arquivo nao derruba os outros
      api.kaykit = { ok: [], erro: {}, mapa: {} };
      if (!Q.has("semkaykit")) for (const [arq, clipes] of Object.entries(KAYKIT)) {
        try {
          Object.assign(dic, await carregarKayKit("./modelos-3d/anim/kaykit/" + arq, vrm, Object.keys(clipes)));
          for (const [nome, [k, rotulo, tipo, rosto, op = {}]] of Object.entries(clipes)) if (dic["kk:" + nome]) {
            ACOES[k] = { dur: 3, tipo, soClipe: true, clipe: "kk:" + nome, ossos: [], rosto, cameraAmpla: !!op.ampla, pose() {} }; NOMES_ACAO[k] = rotulo;
            botaoAcao(tipo === "acao" ? "g-acao" : "g-gesto", k, rotulo, () => { pararDemo(); api.corpo.play(k, true); }, DICAS[k] || ""); api.kaykit.ok.push(k); api.kaykit.mapa[k] = { arquivo: arq, clipe: nome };
          }
        } catch (e) { api.kaykit.erro[arq] = String((e && e.message) || e); console.warn("kaykit:", arq, api.kaykit.erro[arq]); }
      }
      api.corpo.registrarClipes(dic);
      api.clipes = { ok: true, nomes: Object.keys(api.corpo.clipes), ms: Math.round(performance.now() - tc), erro: null };
    } catch (e) { api.clipes.erro = String((e && e.message) || e); console.warn("clipes:", api.clipes.erro); }
  }
  api.msCarga = performance.now() - t0;
  let tri = 0;
  vrm.scene.traverse((o) => { if (o.isMesh) tri += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3; });
  api.triangulos = tri;
  mostrar("ok", "ok");
  if (APP && api.corpo) { // lista do menu: so aprovadas que existem e cujo clipe carregou
    api.corpo.acoesPermitidas = APROVADAS;
    api.appLista = [...APROVADAS].filter((k) => ACOES[k] && ACOES[k].tipo !== "micro" && (!ACOES[k].soClipe || api.corpo.clipes[ACOES[k].clipe])).map((k) => ({ k, rotulo: NOMES_ACAO[k] || k, tipo: ACOES[k].tipo }));
    if (window.parent !== window) window.parent.postMessage({ celia3d: 1, tipo: "pronto", acoes: api.appLista }, location.origin);
  }
  setTimeout(() => { renderer.domElement.style.opacity = "1"; }, 350); // o 1o desenho real chega ~300 ms depois (medido)
  $("info").textContent = "VRM " + (vrm.meta && vrm.meta.metaVersion) + " · ossos " + api.ossos().length + " · " + Math.round(tri / 1000)
    + " mil tri · carga " + (api.msCarga / 1000).toFixed(1) + " s";
}, (ev) => { if (ev.total) $("estado").textContent = "carregando… " + Math.round((100 * ev.loaded) / ev.total) + "%"; },
(err) => { api.erro = String((err && err.message) || err); mostrar("erro", "erro: " + api.erro); });
requestAnimationFrame(quadro);
let oculosPedido = false; // pedido do pai que chegou antes do VRM carregar
if (APP) window.addEventListener("message", (e) => { // so o pai, mesma origem
  if (e.origin !== location.origin || e.source !== window.parent || !e.data || e.data.celia3d !== 1) return;
  const m = e.data;
  if (m.tipo === "pedir-lista") { if (api.appLista) window.parent.postMessage({ celia3d: 1, tipo: "pronto", acoes: api.appLista }, location.origin); return; }
  if (m.tipo === "oculos") { oculosPedido = m.v === true; if (api.oculos) api.oculos.setVisivel(oculosPedido); return; }
  if (!api.corpo) return;
  if (m.tipo === "play" && typeof m.k === "string" && APROVADAS.has(m.k)) api.corpo.play(m.k, true);
  else if (m.tipo === "emotion" && typeof m.v === "string") api.corpo.setEmotion(m.v);
  else if (m.tipo === "energia") api.corpo.setSpeechEnergy(m.v);
});
