// celia-3d-interface.js — Celia 3D DENTRO da interface (so quando a pessoa escolheu "3D" em celia-escolha.js). Script classico.
// Monta o corpo VRM como um <iframe> da bancada em modo app (celia-3d-bancada.html?app=1): canvas proprio, nunca o #canvas do Live2D.
// Ponte (so ouve, contrato 4.5 do plano): celia-emotion -> emocao; celia-speech-energy -> energia da boca. Menu: SO as acoes aprovadas (V) em
// celia-3d-aprovadas.json; a bancada em modo app tambem recusa qualquer outra por mensagem e dentro do Corpo3D (acoesPermitidas).
// Pet nativo (?pet=1): so o corpo, sem menu. Interface com o Pet LIGADO: nada renderiza aqui (iframe oculto => o rAF para) e o menu some;
// volta sozinho quando o Pet e desligado (ordem do Pai, 2026-10-07: Pet ligado => so no Pet; Pet desligado => so na interface).
(() => {
  "use strict";
  if (window.__celia3dInterface) return; window.__celia3dInterface = true;
  const ORIGEM = location.origin;
  const PET_NATIVO = new URLSearchParams(location.search).has("pet");
  const css = document.createElement("style");
  css.textContent = `
#celia-3d-quadro{position:fixed;inset:0;width:100vw;height:100vh;border:0;z-index:0;background:transparent;pointer-events:none;color-scheme:normal}
#celia-3d-quadro.no-palco{position:absolute;inset:0;width:100%;height:100%;z-index:1}
#celia-3d-menu{position:fixed;top:44px;right:12px;z-index:60;font:13px/1.35 system-ui,sans-serif;color:#ececec}
#celia-3d-menu>button{background:rgba(24,24,24,.86);color:inherit;border:1px solid #3a3a3a;border-radius:8px;padding:6px 12px;cursor:pointer;font:inherit}
#celia-3d-menu .lista{display:none;margin-top:6px;max-width:min(320px,80vw);max-height:60vh;overflow:auto;background:rgba(24,24,24,.9);border:1px solid #3a3a3a;border-radius:10px;padding:8px;backdrop-filter:blur(6px)}
#celia-3d-menu.aberto .lista{display:block}
#celia-3d-menu .lista button{margin:3px 4px 0 0;background:#262626;color:#ececec;border:1px solid #3a3a3a;border-radius:6px;padding:4px 9px;cursor:pointer;font:inherit}
#celia-3d-menu .lista button:focus-visible,#celia-3d-menu>button:focus-visible{outline:2px solid #e8c34a;outline-offset:1px}
#celia-3d-menu .vazio{color:#a0a0a0}`;
  document.head.appendChild(css);
  const quadro = document.createElement("iframe");
  quadro.id = "celia-3d-quadro"; quadro.title = "Célia 3D"; quadro.setAttribute("aria-hidden", "true"); quadro.tabIndex = -1;
  const params3d = new URLSearchParams(location.search);
  quadro.src = "./celia-3d-bancada.html?app=1&v=app20261023-mobile" + (params3d.has("prova3d") ? "&prova=1" : "") + (params3d.has("mobileStudio") ? "&mobileStudio=1" : "") + (params3d.has("vrm") ? "&vrm=" + encodeURIComponent(params3d.get("vrm")) : ""); // mobileStudio: captura local somente no palco do PC.
  document.body.insertBefore(quadro, document.body.firstChild);
  // o fundo (a sala) e o Live2D vivem num palco da interface: o corpo 3D entra NESSE palco, no lugar do canvas do Live2D (que fica vazio: sem model_info).
  // Fallback: se o #canvas nao aparecer em 30 s, o quadro continua fixo atras da interface.
  let tentativas = 0;
  // Pet nativo: o #canvas do Live2D nao tem tamanho (0x0), entao o quadro fica FIXO na janela inteira (e celia-palco.js o deixa passar).
  const achar = PET_NATIVO ? null : setInterval(() => {
    const c = document.getElementById("canvas"); tentativas += 1;
    if (c && c.parentElement) { clearInterval(achar); const pai = c.parentElement; if (getComputedStyle(pai).position === "static") pai.style.position = "relative"; quadro.classList.add("no-palco"); pai.appendChild(quadro); window.celia3dInterface.montadoNoPalco = true; }
    else if (tentativas > 150) clearInterval(achar);
  }, 200);
  const menu = document.createElement("div"); menu.id = "celia-3d-menu";
  const abrir = document.createElement("button"); abrir.type = "button"; abrir.textContent = "Ações 3D"; abrir.setAttribute("aria-expanded", "false");
  const lista = document.createElement("div"); lista.className = "lista"; lista.textContent = "carregando…";
  abrir.addEventListener("click", () => { const a = menu.classList.toggle("aberto"); abrir.setAttribute("aria-expanded", String(a)); });
  menu.append(abrir, lista); if (!PET_NATIVO) document.body.appendChild(menu);
  const estado = window.celia3dInterface = { pronta: false, acoes: [], mensagens: 0, erro: null, montadoNoPalco: false, petAtivo: false, petNativo: PET_NATIVO };
  // Pet ligado (so a interface; o Pet em si nunca se esconde). celia-browser-pet.js guarda o pedido: state().desktopPetLaunch / petRequested.
  function petLigado() {
    try { const st = window.celiaBrowserPet && window.celiaBrowserPet.state(); return Boolean(st && (st.liveFocus || st.desktopPetLaunch === "requested" || st.desktopPetLaunch === "pending" || st.petRequested)); } catch (e) { return false; }
  }
  function sincronizarPet() {
    const ativo = !PET_NATIVO && petLigado();
    if (ativo === estado.petAtivo) return; estado.petAtivo = ativo;
    quadro.style.display = ativo ? "none" : ""; menu.style.display = ativo ? "none" : "";
  }
  if (!PET_NATIVO) { sincronizarPet(); setInterval(sincronizarPet, 400); }
  const enviar = (m) => { try { if (quadro.contentWindow) quadro.contentWindow.postMessage({ celia3d: 1, ...m }, ORIGEM); } catch (e) { estado.erro = String((e && e.message) || e); } };
  window.addEventListener("message", (e) => {
    if (e.origin !== ORIGEM || e.source !== quadro.contentWindow || !e.data || e.data.celia3d !== 1) return;
    if (e.data.tipo === "pronto" && Array.isArray(e.data.acoes)) {
      estado.pronta = true; estado.acoes = e.data.acoes.map((a) => ({ k: String(a.k), rotulo: String(a.rotulo), tipo: String(a.tipo) }));
      lista.textContent = ""; abrir.textContent = "Ações 3D (" + estado.acoes.length + ")";
      if (!estado.acoes.length) { const v = document.createElement("span"); v.className = "vazio"; v.textContent = "nenhuma ação aprovada carregou"; lista.appendChild(v); }
      for (const a of estado.acoes) { const b = document.createElement("button"); b.type = "button"; b.dataset.k = a.k; b.textContent = a.rotulo; b.addEventListener("click", () => enviar({ tipo: "play", k: a.k })); lista.appendChild(b); }
    }
  });
  quadro.addEventListener("load", () => enviar({ tipo: "pedir-lista" }));
  setInterval(() => { if (!estado.pronta) enviar({ tipo: "pedir-lista" }); }, 1500);
  window.addEventListener("celia-emotion", (e) => { const v = e && e.detail && e.detail.emotion; if (typeof v === "string") { estado.mensagens += 1; enviar({ tipo: "emotion", v }); } });
  window.addEventListener("celia-speech-energy", (e) => { const v = Number(e && e.detail && e.detail.energy); if (Number.isFinite(v)) enviar({ tipo: "energia", v }); });
})();
