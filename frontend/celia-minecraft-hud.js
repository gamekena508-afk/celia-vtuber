/*
 * celia-minecraft-hud.js — HUD Minecraft da Celia (Fase 5, plano CELIA-V5-HUD-MINECRAFT-REFERENCIA-SPRITES-20261019).
 *
 * UMA interface, UM estado de visibilidade. Status, inventario, objetivos, Celia/Criador, bussola, hora/dia e mapa sao conteudo
 * permanente desta HUD: nao existem interruptores por painel. Quem liga/desliga e o booleano `hud_minecraft` da aba Corpo
 * (celia-corpo-config.js, persistido em /bridge/corpo-opcoes) — a voz ("ligar mapa", "desligar inventario"...) grava o mesmo campo.
 *
 * So exibe fatos. Fonte unica de dados: GET /bridge/minecraft/hud (corpo Mineflayer + etapas do Jev + waypoints do SQLite), 8 vezes
 * por segundo (tempo real, com suavizacao por quadro) e SOMENTE enquanto a HUD esta ligada. Dado ausente ou velho vira "indisponivel"; nada e preenchido por fantasia.
 * Sprites: arquivos locais reais em /minecraft-items/1.21.10/ (manifest + NOTICE ao lado); item sem sprite = casa neutra, nome
 * registrado em estado().faltando; nunca emoji, nunca icone inventado.
 *
 * Camadas: jogo -> HUD (z-index 4, pointer-events:none) -> Live2D/Slime (wrapper do Live2D = z-index 5). Layout ancorado na
 * referencia 1672x941: esquerda (status, inventario, objetivos), centro (Celia/Criador, bussola), direita (hora/dia, mapa).
 */
(function () {
  "use strict";
  if (window.celiaMinecraftHud) return;
  const ROTA = "/bridge/minecraft/hud";
  const SPRITES = "/minecraft-items/1.21.10/";
  const REF_W = 1672, REF_H = 941;
  const FRESCO_S = 5;       // fato mais velho que isto (relogio do daemon) e tratado como indisponivel
  const PERIODO_MS = 125;   // 8 consultas por segundo (tempo real), so com a HUD ligada; o daemon responde em ~2 ms
  const TAU_MS = 90;        // suavizacao entre amostras: o numero acompanha o corpo sem saltos
  const FATOR_ESCALA = 0.78; // a referencia (1672x941) era grande demais: tudo a 78%
  const estado = { montada: false, ligada: false, disponivel: false, motivo: "nunca_consultada", consultas: 0, falhas: 0, ultimaEm: 0, escala: 1, faltando: [], sprites: 0, quadrosMapa: 0, dimensao: null };
  window.celiaMinecraftHud = { estado: () => ({ ...estado, faltando: [...estado.faltando] }) };

  // ---------- estilo (uma folha, escopada em #celia-mc-hud) ----------
  const U = (n) => `calc(${n}px * var(--s))`;
  const css = document.createElement("style");
  css.id = "celia-mc-hud-css";
  css.textContent = `
#celia-mc-hud{position:fixed;inset:0;z-index:4;pointer-events:none;display:none;--s:1;--ciano:#12dcff;--ciano2:#6af0ff;--ouro:#ffd25a;--fundo:linear-gradient(180deg,rgba(9,34,74,.9),rgba(3,12,32,.93));font-family:"Rajdhani","Chakra Petch","Segoe UI",system-ui,sans-serif;color:#f2fbff;user-select:none;-webkit-user-select:none}
#celia-mc-hud[data-on="1"]{display:block;transition:opacity .2s}
#celia-mc-hud[data-dialogo="1"]{opacity:.18}
#celia-mc-hud *{box-sizing:border-box;pointer-events:none}
#celia-mc-hud svg{display:inline-block;vertical-align:middle}
#celia-mc-hud .p{position:absolute}
#celia-mc-hud .p,#mc-cen .jg{background:var(--fundo);border:${U(2)} solid var(--ciano);border-radius:${U(10)};box-shadow:0 0 ${U(14)} rgba(18,220,255,.6),0 0 ${U(3)} rgba(106,240,255,.9),inset 0 0 ${U(18)} rgba(24,120,200,.55),inset 0 ${U(1)} 0 rgba(160,245,255,.35);backdrop-filter:blur(${U(1.5)})}
#celia-mc-hud .p::before,#mc-cen .jg::before{content:"";position:absolute;inset:${U(-3)};border-radius:${U(11)};background:linear-gradient(var(--ouro),var(--ouro)) 0 0/${U(12)} ${U(2)} no-repeat,linear-gradient(var(--ouro),var(--ouro)) 0 0/${U(2)} ${U(12)} no-repeat,linear-gradient(var(--ouro),var(--ouro)) 100% 0/${U(12)} ${U(2)} no-repeat,linear-gradient(var(--ouro),var(--ouro)) 100% 0/${U(2)} ${U(12)} no-repeat,linear-gradient(var(--ouro),var(--ouro)) 0 100%/${U(12)} ${U(2)} no-repeat,linear-gradient(var(--ouro),var(--ouro)) 0 100%/${U(2)} ${U(12)} no-repeat,linear-gradient(var(--ouro),var(--ouro)) 100% 100%/${U(12)} ${U(2)} no-repeat,linear-gradient(var(--ouro),var(--ouro)) 100% 100%/${U(2)} ${U(12)} no-repeat;filter:drop-shadow(0 0 ${U(3)} rgba(255,210,90,.8))}
#celia-mc-hud .p::after,#mc-cen .jg::after{content:"";position:absolute;left:${U(14)};right:${U(14)};top:${U(1)};height:${U(1)};background:linear-gradient(90deg,transparent,#aef6ff,transparent);opacity:.8}


#celia-mc-hud .tt{display:flex;align-items:center;justify-content:space-between;font-weight:700;letter-spacing:${U(.6)};color:var(--ciano2);text-shadow:0 0 ${U(8)} rgba(18,220,255,.75);font-size:${U(19)};line-height:${U(22)}}
#celia-mc-hud .tt b{color:#fff;font-weight:700}
#celia-mc-hud .ic{display:inline-block;width:${U(21)};height:${U(21)};margin-right:${U(9)};vertical-align:middle}
#celia-mc-hud .nv{display:flex;align-items:center}
/* esquerda */
#mc-status{left:${U(15)};top:${U(22)};width:${U(448)};height:${U(166)};padding:${U(10)} ${U(16)}}
#mc-status .lin{display:flex;align-items:center;height:${U(33)};margin-top:${U(5)}}
#mc-status .rot{width:${U(62)};font-size:${U(16)};font-weight:700;letter-spacing:${U(.5)};color:var(--ciano2)}
#mc-status .it{display:flex;gap:${U(3)};align-items:center}
#mc-status .hrt{width:${U(25)};height:${U(23)}}
#mc-status .fm{position:relative;width:${U(25)};height:${U(25)}}
#mc-status .fm img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
#mc-status .fm img.v{opacity:.22;filter:grayscale(1) brightness(.6)}
#mc-status .fm .m{position:absolute;inset:0;overflow:hidden;width:50%}
#mc-status .fm .m img{width:${U(25)};height:${U(25)}}
#mc-status .xp{flex:1;height:${U(16)};border:${U(2)} solid var(--ciano);border-radius:${U(9)};background:rgba(0,10,20,.7);overflow:hidden}
#mc-status .xp i{display:block;height:100%;width:0;background:linear-gradient(#7dff9c,#14d655 55%,#0aa63c);box-shadow:0 0 ${U(10)} rgba(60,255,120,.9)}
#mc-inv{left:${U(15)};top:${U(208)};width:${U(448)};height:${U(130)};padding:${U(9)} ${U(14)}}
#mc-inv .slots{display:flex;gap:${U(4)};margin-top:${U(10)}}
#mc-inv .sl{flex:1;min-width:0;text-align:center;font-size:${U(15)};font-weight:700;color:#cfe9f5}
#mc-inv .cx{position:relative;aspect-ratio:1;border:${U(2)} solid rgba(110,170,215,.7);border-radius:${U(5)};background:linear-gradient(180deg,rgba(24,50,92,.95),rgba(8,20,42,.95));margin-bottom:${U(3)};display:flex;align-items:center;justify-content:center}
#mc-inv .sl[data-sel="1"] .cx{border-color:#9ff8ff;box-shadow:0 0 ${U(12)} var(--ciano),0 0 ${U(3)} #fff,inset 0 0 ${U(8)} rgba(18,220,255,.6)}
#mc-inv .cx img{width:82%;height:82%;object-fit:contain}
#mc-inv .cx .q{position:absolute;right:${U(3)};bottom:${U(1)};font-size:${U(15)};font-weight:700;color:#fff;text-shadow:0 0 ${U(2)} #000,${U(1)} ${U(1)} 0 #000}
#mc-inv .cx .nf{width:70%;height:70%;border:${U(2)} dashed rgba(160,190,210,.5);border-radius:${U(4)}}
#mc-obj{left:${U(15)};bottom:${U(35)};width:${U(305)};height:${U(178)};padding:${U(10)} ${U(16)}}
#mc-obj .ln{display:flex;align-items:center;height:${U(40)};font-size:${U(18)};font-weight:600;color:#f1f9ff;border-top:${U(1)} solid rgba(53,214,242,.18)}
#mc-obj .ln:first-of-type{border-top:${U(2)} solid rgba(53,214,242,.5);margin-top:${U(6)}}
#mc-obj .ln img{width:${U(28)};height:${U(28)};margin-right:${U(10)};flex:none}
#mc-obj .ln .tx{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#mc-obj .ln .st{flex:none;margin-left:${U(8)};font-size:${U(12)};font-weight:700;letter-spacing:${U(.2)};color:#7fe9fb;border:${U(1)} solid rgba(127,233,251,.55);border-radius:${U(5)};padding:0 ${U(5)};line-height:${U(17)}}
#mc-obj .ln[data-cur="0"]{opacity:.78;font-weight:500}
#mc-obj{height:${U(318)};width:${U(392)}}
#mc-obj .feed{position:absolute;left:${U(12)};right:${U(12)};top:${U(40)};bottom:${U(10)};display:flex;flex-direction:column;justify-content:flex-end;gap:${U(7)};overflow:hidden}
#mc-obj .fb{--c:#ff4560;--g:rgba(255,59,82,.85);flex:none;display:flex;align-items:center;min-height:${U(34)};padding:${U(5)} ${U(12)};border:${U(2.5)} solid var(--c);border-radius:${U(11)};background:linear-gradient(180deg,rgba(14,10,22,.9),rgba(6,8,18,.9));box-shadow:0 0 ${U(18)} var(--g),0 0 ${U(6)} var(--c),inset 0 0 ${U(12)} var(--g);font-size:${U(16.5)};font-weight:600;line-height:${U(19)};color:#fff;text-shadow:0 0 ${U(6)} var(--g)}
#mc-obj .fb[data-k="thought"]{--c:#3dffa0;--g:rgba(45,255,143,.8)}
#mc-obj .fb[data-k="goal"]{--c:#4aa8ff;--g:rgba(61,155,255,.85)}
#mc-obj .fb .tx{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
#mc-obj .fb.entra{animation:mcFeedEntra .32s cubic-bezier(.2,.8,.2,1) both}
#mc-obj .fb.sai{transition:opacity .4s,transform .4s;opacity:0;transform:translateY(-${U(14)})}
@keyframes mcFeedEntra{from{opacity:0;transform:translateY(${U(18)})}to{opacity:1;transform:none}}
/* centro */
#mc-cen{position:absolute;left:50%;top:${U(22)};transform:translateX(-50%);display:flex;gap:${U(22)}}
#mc-cen .jg{position:relative;height:${U(64)};width:${U(300)};display:flex;align-items:center;padding:0 ${U(12)} 0 ${U(10)}}
#mc-cen .jg .avq{flex:none;width:${U(48)};height:${U(48)};margin-right:${U(12)};border-radius:${U(8)};background:radial-gradient(circle at 50% 38%,#16407a,#08162e);border:${U(1.5)} solid rgba(106,240,255,.85);box-shadow:0 0 ${U(7)} rgba(18,220,255,.55),inset 0 0 ${U(6)} rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;overflow:hidden}
#mc-cen .jg .avq img{width:100%;height:100%;object-fit:contain;object-position:center;display:block}
#mc-cen .jg .nm{font-weight:700;font-size:${U(21)};letter-spacing:${U(.4)};color:var(--ciano2);line-height:${U(23)}}
#mc-cen .jg .co{font-size:${U(17)};font-weight:600;color:#fff;white-space:nowrap}
#mc-cen .jg .co em{font-style:normal;color:var(--ciano2);margin:0 ${U(3)} 0 ${U(9)}}
#mc-cen .jg .co em:first-child{margin-left:0}
#mc-cen .jg .nd{font-size:${U(15)};font-weight:600;color:#a9c4d6}
#mc-bus{position:absolute;left:50%;top:${U(100)};transform:translateX(-50%);width:${U(560)};height:${U(42)};overflow:hidden;
 -webkit-mask-image:linear-gradient(90deg,transparent,#000 14%,#000 86%,transparent);mask-image:linear-gradient(90deg,transparent,#000 14%,#000 86%,transparent)}
#mc-bus canvas{position:absolute;inset:0;width:100%;height:100%}
/* direita */
#mc-clk{right:${U(345)};top:${U(22)};width:${U(147)};height:${U(72)};display:flex;align-items:center;padding:0 ${U(12)}}
#mc-clk svg{width:${U(44)};height:${U(44)};margin-right:${U(10)};flex:none}
#mc-clk .h{font-size:${U(25)};font-weight:700;color:#fff;line-height:${U(28)}}
#mc-clk .d{font-size:${U(17)};font-weight:600;color:var(--ciano2);line-height:${U(21)}}
#mc-map{right:${U(12)};top:${U(18)};width:${U(304)};height:${U(305)};padding:${U(8)} ${U(8)} ${U(8)}}
#mc-map .tt{height:${U(26)};padding:0 ${U(4)}}
#mc-map .cv{position:absolute;left:${U(8)};right:${U(8)};top:${U(40)};bottom:${U(8)};border:${U(2)} solid rgba(106,240,255,.8);transition:border-color .6s,box-shadow .6s;border-radius:${U(7)};overflow:hidden;background:#071221}
#mc-map canvas{position:absolute;inset:0;width:100%;height:100%}
#mc-map .semsinal{position:absolute;inset:0;display:none;align-items:center;justify-content:center;text-align:center;font-weight:700;font-size:${U(16)};color:#a9c4d6;background:rgba(5,14,28,.72);padding:${U(12)}}
#celia-mc-hud[data-disp="0"] #mc-map .semsinal{display:flex}
#mc-map[data-fase="noite"] .cv{border-color:#6f8cff;box-shadow:0 0 ${U(12)} rgba(90,120,255,.75)}
#mc-map[data-fase="entardecer"] .cv,#mc-map[data-fase="amanhecer"] .cv{border-color:#ffa24a;box-shadow:0 0 ${U(12)} rgba(255,150,60,.75)}
#mc-map[data-fase="dia"] .cv{box-shadow:0 0 ${U(10)} rgba(18,220,255,.5)}
`;
  document.head.appendChild(css);

  // ---------- DOM (construido uma vez; atualizacoes escrevem so o que mudou) ----------
  const el = (tag, attrs, ...filhos) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) { if (k === "text") e.textContent = v; else e.setAttribute(k, v); }
    for (const f of filhos) if (f) e.append(f);
    return e;
  };
  const svg = (html) => { const s = document.createElement("span"); s.innerHTML = html; return s.firstElementChild; };
  const ICONE_STATUS = '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="#35d6f2" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5" fill="#35d6f2"/><path d="M12 1v5M12 18v5M1 12h5M18 12h5"/></svg>';
  const ICONE_CUBO = '<svg class="ic" viewBox="0 0 24 24" fill="#35d6f2" stroke="#9ff3ff" stroke-width="1"><path d="M12 2l9 5v10l-9 5-9-5V7z" fill-opacity=".55"/><path d="M12 12l9-5M12 12L3 7M12 12v10" fill="none"/></svg>';
  const ICONE_ALVO = '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="#9ff3ff" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1.2" fill="#9ff3ff"/></svg>';
  const ICONE_COROA = '<svg viewBox="0 0 24 24" width="17" height="17" style="margin-right:6px;vertical-align:-2px" fill="#e8c46c"><path d="M2 8l5 4 5-8 5 8 5-4-2 11H4z"/></svg>';
  const ICONE_USER = '<svg viewBox="0 0 24 24" width="16" height="16" style="margin-right:6px;vertical-align:-2px" fill="#35d6f2"><circle cx="12" cy="8" r="4.5"/><path d="M3 22c0-5 4-8 9-8s9 3 9 8z"/></svg>';
  const SOL = '<svg viewBox="0 0 48 48"><g stroke="#ffd34a" stroke-width="3" stroke-linecap="round"><path d="M24 3v7M24 38v7M3 24h7M38 24h7M9 9l5 5M34 34l5 5M39 9l-5 5M14 34l-5 5"/></g><circle cx="24" cy="24" r="10.5" fill="#ffc21f" stroke="#fff3b0" stroke-width="1.5"/></svg>';
  const LUA = '<svg viewBox="0 0 48 48"><path d="M30 5a19 19 0 1 0 13 30A16 16 0 0 1 30 5z" fill="#dfe8f7" stroke="#ffffff" stroke-width="1.5"/></svg>';
  const HORIZONTE = (cor) => `<svg viewBox="0 0 48 48"><g stroke="${cor}" stroke-width="3" stroke-linecap="round"><path d="M24 6v6M8 14l4 4M40 14l-4 4M3 28h6M39 28h6"/></g><path d="M10 34a14 14 0 0 1 28 0z" fill="${cor}" stroke="#fff3b0" stroke-width="1.5"/><path d="M4 38h40" stroke="#ffe0b0" stroke-width="3" stroke-linecap="round"/></svg>`;
  // coracao em pixels (9x8), desenho proprio; cheio / metade esquerda / vazio
  const CORACAO = ["01100110", "11111111", "11111111", "11111111", "01111110", "00111100", "00011000"];
  function coracao(modo) {
    const px = CORACAO.map((linha, y) => [...linha].map((c, x) => (c === "1" ? `<rect x="${x}" y="${y}" width="1.02" height="1.02" fill="${modo === "cheio" || (modo === "meio" && x < 4) ? "#ff2b3d" : "#3a1a26"}"/>` : "")).join("")).join("");
    const brilho = modo === "vazio" ? "" : '<rect x="1" y="1" width="1.02" height="1.02" fill="#ffc2bb"/>';
    return `<svg class="hrt" viewBox="0 0 8 7" shape-rendering="crispEdges">${px}${brilho}</svg>`;
  }

  const raiz = el("div", { id: "celia-mc-hud", "data-on": "0", "data-disp": "0", role: "presentation" });
  const pStatus = el("div", { id: "mc-status", class: "p" });
  const tNivel = el("b", { text: "NÍVEL —" });
  const hearts = el("div", { class: "it" });
  const fome = el("div", { class: "it" });
  const xpFill = el("i");
  pStatus.append(
    el("div", { class: "tt" }, el("span", { class: "nv" }, svg(ICONE_STATUS), document.createTextNode("STATUS - CÉLIA (IA)")), tNivel),
    el("div", { class: "lin" }, el("span", { class: "rot", text: "VIDA" }), hearts),
    el("div", { class: "lin" }, el("span", { class: "rot", text: "FOME" }), fome),
    el("div", { class: "lin" }, el("span", { class: "rot", text: "EXP" }), el("div", { class: "xp" }, xpFill)),
  );
  const pInv = el("div", { id: "mc-inv", class: "p" });
  const slots = el("div", { class: "slots" });
  const casas = [];
  for (let i = 0; i < 9; i += 1) {
    const cx = el("div", { class: "cx" });
    const sl = el("div", { class: "sl", "data-sel": "0" }, cx, el("span", { text: String(i + 1) }));
    slots.append(sl); casas.push({ sl, cx, chave: "" });
  }
  pInv.append(el("div", { class: "tt" }, el("span", { class: "nv" }, svg(ICONE_CUBO), document.createTextNode("INVENTÁRIO"))), slots);
  const pObj = el("div", { id: "mc-obj", class: "p" });
  const objLinhas = el("div");
  pObj.append(el("div", { class: "tt" }, el("span", { class: "nv" }, svg(ICONE_ALVO), document.createTextNode("AÇÕES DO BOT · DEBUG"))), objLinhas);

  const centro = el("div", { id: "mc-cen" });
  function modulo(chave, nome, avatar, icone) {
    const coords = el("div", { class: "co" });
    const nm = el("div", { class: "nm" }, svg(icone), document.createTextNode(nome));
    const img = el("img", { src: avatar, alt: "" });
    img.addEventListener("error", () => { img.style.visibility = "hidden"; });
    const caixa = el("div", { class: "jg", "data-k": chave }, el("span", { class: "avq" }, img), el("div", {}, nm, coords));
    return { caixa, coords };
  }
  const mCelia = modulo("celia", "CÉLIA", "/avatars/celia.png", ICONE_COROA);
  const mCriador = modulo("criador", "CRIADOR", "/avatars/criador-slime-retrato.png", ICONE_USER);
  centro.append(mCelia.caixa, mCriador.caixa);
  const bussola = el("div", { id: "mc-bus" });
  const cvBus = el("canvas"); bussola.append(cvBus);

  const pClk = el("div", { id: "mc-clk", class: "p" });
  const clkIcone = el("span"); clkIcone.style.display = "contents";
  const clkHora = el("div", { class: "h", text: "--:--" });
  const clkDia = el("div", { class: "d", text: "Dia —" });
  pClk.append(clkIcone, el("div", {}, clkHora, clkDia));
  const pMap = el("div", { id: "mc-map", class: "p" });
  const cvMapa = el("canvas");
  const semSinal = el("div", { class: "semsinal", text: "Sem sinal do corpo no jogo" });
  pMap.append(el("div", { class: "tt" }, el("span", { text: "" }), el("span", { text: "↑", style: "color:#e8c46c" })), el("div", { class: "cv" }, cvMapa, semSinal));
  raiz.append(pStatus, pInv, pObj, centro, bussola, pClk, pMap);

  // ---------- sprites ----------
  let manifesto = null, manifestoPedido = false;
  function carregarManifesto() {
    if (manifestoPedido) return; manifestoPedido = true;
    fetch(SPRITES + "manifest.json", { cache: "force-cache" }).then((r) => (r.ok ? r.json() : Promise.reject(new Error("http_" + r.status))))
      .then((m) => { manifesto = m.items || {}; estado.sprites = Object.keys(manifesto).length; ultimoDado && pintar(ultimoDado); })
      .catch(() => { manifesto = {}; estado.sprites = 0; });
  }
  function urlSprite(nome) { return manifesto && Object.prototype.hasOwnProperty.call(manifesto, nome) ? SPRITES + nome + ".png" : null; }
  function registrarFalta(nome) { if (nome && !estado.faltando.includes(nome) && estado.faltando.length < 64) estado.faltando.push(nome); }
  function imgSprite(nome, extra) {
    const url = urlSprite(nome);
    if (!url) return null;
    const i = el("img", { src: url, alt: "", draggable: "false", ...(extra || {}) });
    return i;
  }

  // ---------- atualizacao de cada area (idempotente) ----------
  const numero = (v, casasDec) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(casasDec) : "—");
  function pintarCoords(mod, pos, rotulo) {
    if (pos) {
      const t = `X ${numero(pos.x, 1)} Y ${numero(pos.y, 0)} Z ${numero(pos.z, 1)}`;
      if (mod.coords.dataset.t !== t) { mod.coords.dataset.t = t; mod.coords.replaceChildren(...["X", "Y", "Z"].flatMap((eixo) => [el("em", { text: eixo }), document.createTextNode(numero(pos[eixo.toLowerCase()], eixo === "Y" ? 0 : 1))])); }
    } else if (mod.coords.dataset.t !== "nd:" + rotulo) { mod.coords.dataset.t = "nd:" + rotulo; mod.coords.replaceChildren(el("span", { class: "nd", text: rotulo })); }
  }
  function pintarVida(vida, fomeV) {
    const alvo = (cont, valor, fabrica, chave) => {
      const k = chave + ":" + (typeof valor === "number" ? Math.round(valor) : "nd") + ":" + (manifesto ? 1 : 0);
      if (cont.dataset.k === k) return; cont.dataset.k = k;
      cont.replaceChildren(...Array.from({ length: 10 }, (_, i) => fabrica(typeof valor === "number" ? Math.max(0, Math.min(2, Math.round(valor) - i * 2)) : 0)));
    };
    alvo(hearts, vida, (n) => svg(coracao(n === 2 ? "cheio" : n === 1 ? "meio" : "vazio")), "v");
    alvo(fome, fomeV, (n) => {
      const caixa = el("div", { class: "fm" });
      const v = imgSprite("cooked_chicken", { class: "v" });
      if (!v) return caixa;
      caixa.append(v);
      if (n >= 1) { const m = el("div", { class: "m", style: n === 2 ? "width:100%" : "width:50%" }); const c = imgSprite("cooked_chicken"); c && m.append(c); caixa.append(m); }
      return caixa;
    }, "f");
  }
  function pintarHotbar(h) {
    casas.forEach((casa, i) => {
      const s = h && h.slots ? h.slots[i] : null;
      const chave = s ? s.name + "x" + s.count : "";
      const sel = h && h.selected === i ? "1" : "0";
      if (casa.sl.dataset.sel !== sel) casa.sl.dataset.sel = sel;
      const k = chave + ":" + (manifesto ? 1 : 0);
      if (casa.chave === k) return; casa.chave = k;
      casa.cx.replaceChildren();
      if (!s) return;
      const im = imgSprite(s.name);
      if (im) { casa.cx.append(im); } else if (manifesto) { registrarFalta(s.name); casa.cx.append(el("div", { class: "nf", title: s.name })); }
      if (s.count > 1) casa.cx.append(el("span", { class: "q", text: String(s.count) }));
    });
  }
  // ---------- feed do debug (23/10): vermelho = automatico, verde = pensamento, azul = objetivo ----------
  // Caixas arredondadas com brilho ESTATICO (nada pisca). A nova entra embaixo e empurra as outras pra cima (FLIP: cada caixa
  // desliza da posicao antiga pra nova); a mais antiga sai pelo topo esmaecendo. Texto vem pronto do daemon, so fatos concretos.
  const FEED_MAX = 6;
  const feedCaixas = new Map(); // id -> elemento
  function pintarFeed(feed) {
    if (!Array.isArray(feed)) return false;
    if (objLinhas.dataset.modo !== "feed") { objLinhas.replaceChildren(); objLinhas.dataset.modo = "feed"; objLinhas.dataset.k = ""; objLinhas.className = "feed"; }
    const visiveis = feed.filter((e) => e && Number.isInteger(e.id) && ["auto", "thought", "goal"].includes(e.kind)).slice(-FEED_MAX);
    const ids = new Set(visiveis.map((e) => e.id));
    const antes = new Map();
    for (const [id, n] of feedCaixas) antes.set(id, n.getBoundingClientRect().top);
    for (const [id, n] of feedCaixas) {
      if (ids.has(id)) continue;
      feedCaixas.delete(id); antes.delete(id);
      n.classList.add("sai"); setTimeout(() => n.remove(), 420);
    }
    for (const e of visiveis) {
      let n = feedCaixas.get(e.id);
      if (!n) { n = el("div", { class: "fb entra", "data-k": e.kind }, el("span", { class: "tx" })); objLinhas.append(n); feedCaixas.set(e.id, n); }
      const txt = String(e.text || "");
      if (n.firstChild.textContent !== txt) n.firstChild.textContent = txt;
    }
    for (const [id, top] of antes) { // as que ja estavam deslizam do lugar antigo para o novo
      const n = feedCaixas.get(id); if (!n) continue;
      const dy = top - n.getBoundingClientRect().top;
      if (Math.abs(dy) > 0.5) n.animate([{ transform: `translateY(${dy}px)` }, { transform: "translateY(0)" }], { duration: 320, easing: "cubic-bezier(.2,.8,.2,1)" });
    }
    return true;
  }
  function pintarObjetivos(lista, acoes) {
    if (objLinhas.dataset.modo === "feed") { objLinhas.replaceChildren(); feedCaixas.clear(); objLinhas.dataset.modo = ""; objLinhas.className = ""; }
    const k = JSON.stringify(lista || []) + JSON.stringify(acoes || []) + (manifesto ? 1 : 0);
    if (objLinhas.dataset.k === k) return; objLinhas.dataset.k = k;
    if (acoes && acoes.length) { // debug: o que o corpo esta fazendo / acabou de fazer (mais nova em cima), de qualquer origem
      const ROT = { running: "…", succeeded: "ok", failed: "falhou", cancelled: "cancelada", stopped: "parada", blocked: "bloqueada", timeout: "tempo" };
      objLinhas.replaceChildren(...acoes.slice(0, 3).map((a) => {
        const ln = el("div", { class: "ln", "data-cur": a.status === "running" ? "1" : "0" });
        ln.append(el("span", { class: "tx", text: String(a.type || "?") + (a.brief ? " " + a.brief : "") }));
        const st = (ROT[a.status] || String(a.status || "?")) + (a.status !== "running" && Number.isInteger(a.ms) ? " " + (a.ms / 1000).toFixed(1) + "s" : "") + (a.error ? " · " + a.error : "");
        ln.append(el("span", { class: "st", text: st }));
        return ln;
      }));
      return;
    }
    const linhas = (lista || []).slice(0, 3).map((o) => {
      const ln = el("div", { class: "ln", "data-cur": o.current ? "1" : "0" });
      const im = o.icon ? imgSprite(o.icon) : null;
      if (im) ln.append(im); else if (o.icon && manifesto) registrarFalta(o.icon);
      ln.append(el("span", { class: "tx", text: o.text }));
      if (o.status) ln.append(el("span", { class: "st", text: o.status }));
      return ln;
    });
    objLinhas.replaceChildren(...(linhas.length ? linhas : [el("div", { class: "ln", "data-cur": "0", text: "Sem ação do corpo no momento" })]));
  }
  function pintarRelogio(c) {
    const fd = faseDoDia(c);
    const fase = fd ? fd.fase : null;
    estado.faseDia = fase;
    if (pMap.dataset.fase !== String(fase)) pMap.dataset.fase = String(fase);
    const k = String(fase);
    if (clkIcone.dataset.k !== k) { // sem hora conhecida o icone fica apagado: nao afirma nem dia nem noite
      clkIcone.dataset.k = k; const ic = svg(fase === "noite" ? LUA : fase === "entardecer" ? HORIZONTE("#ff8a3d") : fase === "amanhecer" ? HORIZONTE("#ffb347") : SOL); if (!fase) ic.style.opacity = ".22"; clkIcone.replaceChildren(ic);
    }
    const hora = c && Number.isInteger(c.hour) ? String(c.hour).padStart(2, "0") + ":" + String(c.minute).padStart(2, "0") : "--:--";
    const dia = c && Number.isInteger(c.day) ? "Dia " + c.day : "Dia —";
    if (clkHora.textContent !== hora) clkHora.textContent = hora;
    if (clkDia.textContent !== dia) clkDia.textContent = dia;
  }

  // ---------- bussola (canvas): rumo real da Celia, 0 = norte, sentido horario ----------
  const MARCAS = [["N", 0], ["NE", 45], ["E", 90], ["SE", 135], ["S", 180], ["SW", 225], ["W", 270], ["NW", 315]];
  const ROTULO_BUSSOLA = { N: "N", NE: "NE", E: "E", SE: "SE", S: "S", SW: "SW", W: "W", NW: "NW" };
  function ajustarCanvas(cv) {
    const r = cv.getBoundingClientRect(); const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    return { w, h, dpr };
  }
  function desenharBussola(rumo) {
    const { w, h, dpr } = ajustarCanvas(cvBus); const g = cvBus.getContext("2d"); g.clearRect(0, 0, w, h);
    const mid = w / 2, vao = 90, pxPorGrau = (w * 0.5) / vao; // 90 graus para cada lado, como na referencia (W NW N NE E)
    g.strokeStyle = "rgba(127,233,251,.75)"; g.lineWidth = 1.6 * dpr; g.beginPath(); g.moveTo(0, h * 0.34); g.lineTo(w, h * 0.34); g.stroke();
    if (typeof rumo !== "number") {
      g.fillStyle = "#a9c4d6"; g.font = `700 ${15 * dpr}px Rajdhani, system-ui, sans-serif`; g.textAlign = "center"; g.fillText("rumo indisponível", mid, h * 0.72); return;
    }
    g.textAlign = "center";
    for (let graus = Math.floor((rumo - vao) / 5) * 5; graus <= rumo + vao; graus += 5) {
      const x = mid + (graus - rumo) * pxPorGrau; const norm = ((graus % 360) + 360) % 360; const maior = norm % 45 === 0, medio = norm % 15 === 0;
      g.strokeStyle = maior ? "#7fe9fb" : "rgba(127,233,251,.6)"; g.lineWidth = (maior ? 2.2 : 1.3) * dpr;
      g.beginPath(); g.moveTo(x, h * 0.34); g.lineTo(x, h * (maior ? 0.52 : medio ? 0.46 : 0.42)); g.stroke();
      const marca = MARCAS.find((m) => m[1] === norm);
      if (marca) { g.fillStyle = marca[0] === "N" ? "#ffffff" : "#cfeaf5"; g.font = `700 ${(marca[0].length === 1 ? 17 : 15) * dpr}px Rajdhani, system-ui, sans-serif`; g.fillText(ROTULO_BUSSOLA[marca[0]], x, h * 0.86); }
    }
    g.fillStyle = "#ffd23f"; g.strokeStyle = "#7a5a00"; g.lineWidth = 1 * dpr; // marca amarela = direcao real da Celia
    g.beginPath(); g.moveTo(mid - 7 * dpr, 1 * dpr); g.lineTo(mid + 7 * dpr, 1 * dpr); g.lineTo(mid, 15 * dpr); g.closePath(); g.fill(); g.stroke();
  }

  // ---------- mapa topografico: so amostras reais; desconhecido fica escuro ----------
  // Hora do dia -> tinta do mapa. tick 0 = 06:00 (mesma convencao do corpo). Pontos-chave (tick, cor multiplicadora):
  // dia pleno ate 11000, entardecer laranja em 12000, crepusculo roxo em 13000, noite azul a partir de 13800 ate 22300,
  // amanhecer rosado em 23300, dia de novo em 25000 (= tick 1000 do dia seguinte). Sem hora conhecida: sem tinta.
  const TINTA = [[1000, [255, 255, 255]], [11000, [255, 255, 255]], [12000, [255, 158, 100]], [13000, [160, 106, 156]], [13800, [88, 108, 172]], [22300, [88, 108, 172]], [23300, [255, 172, 138]], [25000, [255, 255, 255]]];
  function faseDoDia(c) {
    if (!c || !Number.isInteger(c.hour) || !Number.isInteger(c.minute)) return null;
    let t = ((c.hour - 6 + 24) % 24) * 1000 + c.minute * (1000 / 60);
    if (t < 1000) t += 24000;
    let rgb = TINTA[TINTA.length - 1][1];
    for (let i = 1; i < TINTA.length; i += 1) {
      if (t <= TINTA[i][0]) { const [t0, c0] = TINTA[i - 1], [t1, c1] = TINTA[i]; const f = (t - t0) / (t1 - t0); rgb = c0.map((v, k) => Math.round(v + (c1[k] - v) * f)); break; }
    }
    const fase = t >= 12000 && t < 13800 ? "entardecer" : t >= 13800 && t < 22300 ? "noite" : t >= 22300 ? "amanhecer" : "dia";
    return { tick: Math.round(t % 24000), rgb, fase };
  }
  const COR = { 1: [36, 118, 214], 2: [86, 184, 50], 3: [30, 122, 48], 4: [236, 212, 140], 5: [136, 142, 150], 6: [240, 248, 255], 7: [150, 96, 48], 8: [156, 152, 146], 9: [255, 96, 24], a: [188, 142, 82] };
  const avatarCriador = new Image(); avatarCriador.src = "/avatars/criador-slime-retrato.png";
  function desenharMapa(d) {
    const { w, h, dpr } = ajustarCanvas(cvMapa); const g = cvMapa.getContext("2d");
    g.fillStyle = "#071221"; g.fillRect(0, 0, w, h);
    estado.quadrosMapa += 1;
    const celia = d && d.available && d.celia && d.celia.position;
    const terr = d && d.available && d.map && d.map.terrain;
    if (!celia) return;
    const lado = Math.max(w, h), raio = 64; // a janela do corpo cobre +-64 blocos em volta da Celia; o lado maior mostra os 128, o menor corta (nunca inventa)
    const pb = lado / (raio * 2);
    const cx = w / 2, cz = h / 2;
    const aX = (x) => cx + (x - celia.x) * pb, aZ = (z) => cz + (z - celia.z) * pb;
    if (terr) {
      const n = terr.size, passo = terr.step, ys = terr.y;
      const alt = (i, j) => (i >= 0 && j >= 0 && i < n && j < n ? ys[j * n + i] : null);
      for (let j = 0; j < n; j += 1) {
        for (let i = 0; i < n; i += 1) {
          const c = terr.cls[j * n + i]; if (c === "0") continue;
          const y = ys[j * n + i]; const base = COR[c] || COR.a;
          const nw = alt(i - 1, j - 1), se = alt(i + 1, j + 1);
          let luz = 1 + (nw != null && se != null ? (nw - se) * 0.045 : 0) + (typeof y === "number" ? (y - celia.y) * 0.004 : 0);
          luz = Math.max(0.6, Math.min(1.35, luz));
          g.fillStyle = `rgb(${Math.min(255, base[0] * luz) | 0},${Math.min(255, base[1] * luz) | 0},${Math.min(255, base[2] * luz) | 0})`;
          const x0 = aX(terr.x0 + i * passo), z0 = aZ(terr.z0 + j * passo);
          g.fillRect(Math.floor(x0), Math.floor(z0), Math.ceil(passo * pb) + 1, Math.ceil(passo * pb) + 1);
        }
      }
    }
    const fd = faseDoDia(d.clock);
    if (fd && fd.fase !== "dia") { g.save(); g.globalCompositeOperation = "multiply"; g.fillStyle = `rgb(${fd.rgb[0]},${fd.rgb[1]},${fd.rgb[2]})`; g.fillRect(0, 0, w, h); g.restore(); }
    // waypoints reais do mapa do Jev
    const marcas = (d.map && d.map.waypoints) || [];
    for (const m of marcas) {
      const x = aX(m.x), z = aZ(m.z); if (x < 0 || z < 0 || x > w || z > h) continue;
      const s = 9 * dpr;
      if (m.kind === "base") { // casa branca
        g.fillStyle = "#fff"; g.strokeStyle = "#0a1a2e"; g.lineWidth = dpr; g.beginPath(); g.moveTo(x - s, z); g.lineTo(x, z - s); g.lineTo(x + s, z); g.lineTo(x + s * 0.7, z); g.lineTo(x + s * 0.7, z + s * 0.9); g.lineTo(x - s * 0.7, z + s * 0.9); g.lineTo(x - s * 0.7, z); g.closePath(); g.fill(); g.stroke();
      } else if (m.kind === "portal") { // roxo
        g.fillStyle = "#b55cff"; g.strokeStyle = "#220a40"; g.lineWidth = dpr; g.fillRect(x - s * 0.6, z - s, s * 1.2, s * 2); g.strokeRect(x - s * 0.6, z - s, s * 1.2, s * 2);
      } else if (m.kind === "death") { // marca de morte
        g.strokeStyle = "#ff5a5a"; g.lineWidth = 2.4 * dpr; g.beginPath(); g.moveTo(x - s * 0.7, z - s * 0.7); g.lineTo(x + s * 0.7, z + s * 0.7); g.moveTo(x + s * 0.7, z - s * 0.7); g.lineTo(x - s * 0.7, z + s * 0.7); g.stroke();
      } else { // bancada/fornalha: ponto laranja do objetivo de trabalho
        g.fillStyle = "#ff9a1f"; g.strokeStyle = "#3a1c00"; g.lineWidth = dpr; g.beginPath(); g.arc(x, z, s * 0.55, 0, Math.PI * 2); g.fill(); g.stroke();
      }
    }
    // Criador: so se o corpo enxerga a posicao real dele
    const jp = d.criador && d.criador.position;
    if (jp) {
      const x = aX(jp.x), z = aZ(jp.z);
      if (x >= 0 && z >= 0 && x <= w && z <= h) {
        const r = 11 * dpr; g.save(); g.beginPath(); g.arc(x, z, r, 0, Math.PI * 2); g.clip();
        if (avatarCriador.complete && avatarCriador.naturalWidth) g.drawImage(avatarCriador, x - r, z - r, r * 2, r * 2);
        else { g.fillStyle = "#35a7ff"; g.fillRect(x - r, z - r, r * 2, r * 2); }
        g.restore(); g.strokeStyle = "#9ff3ff"; g.lineWidth = 2 * dpr; g.beginPath(); g.arc(x, z, r, 0, Math.PI * 2); g.stroke();
      }
    }
    // Celia: seta amarela apontando para o rumo real (norte para cima)
    const rumo = d.celia && typeof d.celia.heading === "number" ? d.celia.heading : null;
    g.save(); g.translate(cx, cz); if (rumo !== null) g.rotate((rumo * Math.PI) / 180);
    g.fillStyle = "#ffd23f"; g.strokeStyle = "#5a4200"; g.lineWidth = 1.6 * dpr; const a = 11 * dpr;
    g.beginPath(); if (rumo !== null) { g.moveTo(0, -a * 1.1); g.lineTo(a * 0.85, a * 0.85); g.lineTo(0, a * 0.35); g.lineTo(-a * 0.85, a * 0.85); } else g.arc(0, 0, a * 0.7, 0, Math.PI * 2);
    g.closePath(); g.fill(); g.stroke(); g.restore();
    // pontos cardeais (norte fixo para cima)
    g.fillStyle = "#e8f6ff"; g.strokeStyle = "rgba(0,0,0,.8)"; g.lineWidth = 3 * dpr; g.font = `700 ${17 * dpr}px Rajdhani, system-ui, sans-serif`; g.textAlign = "center"; g.textBaseline = "middle";
    for (const [t, px, py] of [["N", w / 2, 13 * dpr], ["S", w / 2, h - 13 * dpr], ["W", 13 * dpr, h / 2], ["E", w - 13 * dpr, h / 2]]) { g.strokeText(t, px, py); g.fillText(t, px, py); }
  }

  // ---------- tempo real: o numero acompanha o corpo (amostras a 4 Hz + suavizacao por quadro) ----------
  // `vivo` e o que esta na tela; `alvo` e o ultimo fato real. Sem valor real (null) a tela mostra null na hora: nada e inventado.
  const vivo = { c: null, j: null, h: null }, alvo = { c: null, j: null, h: null };
  const SALTO_BLOCOS = 6; // acima disto (teleporte, troca de mundo) a tela encaixa direto em vez de deslizar
  const dist3 = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
  const pontoDe = (p) => (p && Number.isFinite(p.x) && Number.isFinite(p.y) && Number.isFinite(p.z) ? { x: p.x, y: p.y, z: p.z } : null);
  function definirAlvo(d) {
    const ok = !!(d && d.available);
    alvo.c = ok && d.celia ? pontoDe(d.celia.position) : null;
    alvo.j = ok && d.criador ? pontoDe(d.criador.position) : null;
    alvo.h = ok && d.celia && typeof d.celia.heading === "number" && Number.isFinite(d.celia.heading) ? d.celia.heading : null;
    const encaixa = document.hidden; // aba oculta: o navegador pausa os quadros, entao nao ha o que suavizar
    for (const k of ["c", "j"]) if (!alvo[k] || !vivo[k] || encaixa || dist3(vivo[k], alvo[k]) > SALTO_BLOCOS) vivo[k] = alvo[k] ? { ...alvo[k] } : null;
    if (alvo.h === null || vivo.h === null || encaixa) vivo.h = alvo.h;
  }
  function suavizado(d) {
    if (!d || !d.available) return d;
    const celia = d.celia ? { ...d.celia, position: vivo.c ? { ...vivo.c } : d.celia.position, heading: vivo.h !== null ? vivo.h : d.celia.heading } : d.celia;
    const criador = d.criador ? { ...d.criador, position: vivo.j ? { ...vivo.j } : d.criador.position } : d.criador;
    return { ...d, celia, criador };
  }
  let quadroId = 0, tQuadro = 0, tDesenho = 0;
  function pintarMovel(d) { // so o que se move: coordenadas, bussola e mapa
    const v = suavizado(d); const ok = !!(v && v.available);
    const c = ok ? v.celia : null, j = ok ? v.criador : null;
    pintarCoords(mCelia, c ? c.position : null, "sem sinal do corpo");
    pintarCoords(mCriador, j ? j.position : null, !ok ? "indisponível" : j && j.online ? "online · posição indisponível" : "fora do jogo");
    desenharBussola(c ? c.heading : null);
    desenharMapa(v);
  }
  function quadro(ts) {
    quadroId = requestAnimationFrame(quadro);
    const dt = Math.min(100, tQuadro ? ts - tQuadro : 16); tQuadro = ts;
    if (!estado.ligada || !ultimoDado || !ultimoDado.available) return;
    const k = 1 - Math.exp(-dt / TAU_MS);
    let mexeu = false;
    for (const nome of ["c", "j"]) {
      const a = vivo[nome], b = alvo[nome]; if (!a || !b) continue;
      for (const eixo of ["x", "y", "z"]) { const dlt = b[eixo] - a[eixo]; if (Math.abs(dlt) > 0.004) { a[eixo] += dlt * k; mexeu = true; } else a[eixo] = b[eixo]; }
    }
    if (vivo.h !== null && alvo.h !== null) {
      const dlt = ((alvo.h - vivo.h + 540) % 360) - 180;
      if (Math.abs(dlt) > 0.05) { vivo.h = (((vivo.h + dlt * k) % 360) + 360) % 360; mexeu = true; } else vivo.h = alvo.h;
    }
    if (mexeu && ts - tDesenho >= 28) { tDesenho = ts; pintarMovel(ultimoDado); }
  }

  // ---------- ciclo ----------
  let ultimoDado = null, timer = null, emVoo = false;
  function pintar(d) {
    d = suavizado(d);
    const ok = !!(d && d.available);
    raiz.dataset.disp = ok ? "1" : "0";
    const c = ok ? d.celia : null;
    const xp = c && c.xp;
    const nivel = xp && Number.isInteger(xp.level) ? "NÍVEL " + xp.level : "NÍVEL —";
    if (tNivel.textContent !== nivel) tNivel.textContent = nivel;
    pintarVida(c ? c.health : null, c ? c.hunger : null);
    const pct = xp && typeof xp.progress === "number" ? Math.round(xp.progress * 100) + "%" : "0%";
    if (xpFill.style.width !== pct) xpFill.style.width = pct;
    pintarHotbar(ok ? d.hotbar : null);
    if (!pintarFeed(d && d.feed)) pintarObjetivos(d ? d.objectives : [], d ? d.debug_actions : []);
    pintarCoords(mCelia, c ? c.position : null, "sem sinal do corpo");
    const j = ok ? d.criador : null;
    pintarCoords(mCriador, j ? j.position : null, !ok ? "indisponível" : j && j.online ? "online · posição indisponível" : "fora do jogo");
    pintarRelogio(ok ? d.clock : null);
    desenharBussola(c ? c.heading : null);
    desenharMapa(d);
    estado.dimensao = ok ? d.dimension : null;
  }
  function limpar(motivo) { ultimoDado = { available: false, reason: motivo, objectives: [] }; estado.disponivel = false; estado.motivo = motivo; definirAlvo(ultimoDado); pintar(ultimoDado); }
  async function consultar() {
    if (emVoo || !estado.ligada) return; emVoo = true;
    const ctl = new AbortController(); const to = setTimeout(() => ctl.abort(), 2500);
    try {
      const r = await fetch(ROTA, { cache: "no-store", signal: ctl.signal });
      if (!r.ok) throw new Error("http_" + r.status);
      const d = await r.json(); estado.consultas += 1; estado.falhas = 0;
      const velho = typeof d.at === "number" && Date.now() / 1000 - d.at > FRESCO_S;
      if (velho) { limpar("dado_velho"); return; }
      ultimoDado = d; estado.disponivel = !!d.available; estado.motivo = d.available ? "ok" : d.reason || "indisponivel"; estado.ultimaEm = Date.now();
      definirAlvo(d); pintar(d);
    } catch (e) {
      estado.falhas += 1; if (estado.falhas >= 2) limpar("central_sem_resposta");
    } finally { clearTimeout(to); emVoo = false; }
  }
  function ligar(sim) {
    if (sim === estado.ligada) return; // idempotente: ligar duas vezes nao duplica nada
    estado.ligada = sim; raiz.dataset.on = sim ? "1" : "0";
    if (sim) { carregarManifesto(); limpar("aguardando"); consultar(); timer = setInterval(consultar, PERIODO_MS); tQuadro = 0; quadroId = requestAnimationFrame(quadro); }
    else { clearInterval(timer); timer = null; cancelAnimationFrame(quadroId); quadroId = 0; }
  }
  function escala() {
    const s = Math.min(window.innerWidth / REF_W, window.innerHeight / REF_H) * FATOR_ESCALA;
    if (Math.abs(s - estado.escala) > 0.001) { estado.escala = s; raiz.style.setProperty("--s", String(s)); if (estado.ligada && ultimoDado) pintar(ultimoDado); }
  }
  function montar() {
    if (estado.montada || !document.body) return; estado.montada = true;
    document.body.appendChild(raiz); escala(); window.addEventListener("resize", escala);
  }
  // o unico interruptor: o booleano da aba Corpo (a voz grava o mesmo campo no servidor; o Corpo segue o servidor)
  // na janela comum (nao no palco/Pet) as Configuracoes abrem em gaveta com contexto proprio, que a HUD nao consegue cobrir:
  // enquanto houver dialogo aberto a HUD fica quase transparente, para o interruptor da aba Corpo continuar legivel.
  const PARAMS = new URLSearchParams(location.search);
  const PALCO = PARAMS.get("palco") === "1";
  // a HUD vive SO no modo Pet (?pet=1). Na interface comum ela nao monta nem liga: o interruptor e a voz continuam gravando o mesmo
  // booleano no servidor, mas quem o desenha e o Pet; senao a HUD aparecia duplicada (interface + Pet).
  const SO_PET = PARAMS.has("pet");
  function seguirInterruptor() {
    const c = window.celiaCorpoConfig; if (!c) return;
    if (!SO_PET) { if (estado.montada) ligar(false); return; }
    montar(); ligar(c.estado().hud_minecraft === true);
    const dialogo = !PALCO && !!document.querySelector('[role="dialog"]') ? "1" : "0";
    if (raiz.dataset.dialogo !== dialogo) raiz.dataset.dialogo = dialogo;
  }
  setInterval(seguirInterruptor, 250);
  seguirInterruptor();
})();
