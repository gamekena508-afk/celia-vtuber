// celia-escolha.js — portao de inicio 2D | 3D (ordem do Pai, 2026-10-07). Script CLASSICO, o PRIMEIRO do <head>.
// Regra: fora do Pet, NADA renderiza (nem Live2D nem 3D) ate a pessoa escolher. O bundle compilado (que carrega o Cubism e monta o Live2D) fica
// adiado: index.html traz <script id="celia-bundle" type="application/x-celia-adiado" data-src="...">; este arquivo o importa DEPOIS da escolha, em
// DOMContentLoaded (mesma hora em que o <script type=module> original rodava: depois de todos os scripts classicos do <head>).
//   - Pet (?pet=1): sem tela. Le o corpo escolhido em GET /bridge/body (o Electron nao divide o armazenamento do navegador); 2D se nao houver
//     resposta. Consulta de novo a cada 1,5 s e recarrega se a escolha mudou (interruptor 2D/3D da interface).
//   - ?corpo=2d|3d: atalho EXPLICITO (testes/favoritos); sem isso a escolha e obrigatoria a cada abertura (nada e lembrado).
//   - 2D: so importa o bundle. Nenhuma outra mudanca.
//   - 3D: remove model_info das mensagens "set-model-and-conf" (o Live2D so monta com model_info.url), importa o bundle (chat/configuracoes
//     continuam) e carrega celia-3d-interface.js (iframe da bancada em modo app + menu so com as aprovadas).
(() => {
  "use strict";
  const Q = new URLSearchParams(location.search);
  const PET = Q.has("pet");
  const FORCADO = ["2d", "3d"].includes(Q.get("corpo")) ? Q.get("corpo") : null;
  window.__celiaCorpo = null;
  // Na tela de escolha NADA de debug aparece (ordem do Pai, 2026-10-07): sem FPS (#celia-body-hud) nem as caixas THINKING/PROCESSOS. Voltam quando
  // iniciar() grava data-celia-corpo. Pet e ?corpo= (sem tela de escolha) nao sao afetados.
  if (!PET && !FORCADO) {
    const oculta = document.createElement("style"); oculta.id = "celia-escolha-oculta";
    oculta.textContent = "html:not([data-celia-corpo]) #celia-debug-boxes,html:not([data-celia-corpo]) #celia-body-hud{display:none!important}";
    document.head.appendChild(oculta);
  }
  window.celiaEscolha = { versao: 2, pet: PET, forcado: FORCADO, escolhido: null, bundleImportado: false, trocar: null };

  // Escolha publicada para o Pet (que roda no Electron, com storage proprio). So por gesto humano: automacao (navigator.webdriver) nao
  // publica, para um teste meu nunca mudar o corpo do Pet real; os testes que provam a publicacao pedem ?publicarcorpo=1 e interceptam a rota.
  const PUBLICAR = !navigator.webdriver || Q.has("publicarcorpo");
  async function publicarCorpo(corpo) {
    if (!PUBLICAR) return false;
    try { const r = await fetch("/bridge/body", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ corpo }) }); return r.ok; } catch (e) { return false; }
  }
  async function corpoPublicado() {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 2000);
    try { const r = await fetch("/bridge/body", { cache: "no-store", signal: ctl.signal }); if (!r.ok) return null; const j = await r.json(); return j && ["2d", "3d"].includes(j.corpo) ? j.corpo : null; } catch (e) { return null; } finally { clearTimeout(t); }
  }
  async function modoPublicado() {
    const ctl = new AbortController(), tm = setTimeout(() => ctl.abort(), 2000);
    try { const r = await fetch("/bridge/mode", { cache: "no-store", signal: ctl.signal }); return r.ok ? await r.json() : null; } catch (e) { return null; } finally { clearTimeout(tm); }
  }
  // interruptor da interface: grava a escolha e recarrega com ?corpo= (nada e lembrado alem disso; o Pet segue pelo servidor).
  window.celiaEscolha.trocar = async (corpo) => {
    if (!["2d", "3d"].includes(corpo) || corpo === window.__celiaCorpo) return false;
    await publicarCorpo(corpo);
    const u = new URL(location.href); u.searchParams.set("corpo", corpo); location.replace(u.toString()); return true;
  };

  function instalarFiltroModelo() {
    const Nativo = window.WebSocket; if (!Nativo || Nativo.__celia3d) return;
    const filtrar = (ev) => {
      if (!ev || typeof ev.data !== "string" || ev.data.indexOf("set-model-and-conf") < 0) return ev;
      try { const o = JSON.parse(ev.data); if (o && o.type === "set-model-and-conf" && o.model_info) { delete o.model_info; return new MessageEvent("message", { data: JSON.stringify(o), origin: ev.origin, lastEventId: ev.lastEventId }); } } catch (e) { /* mensagem que nao e JSON passa intacta */ }
      return ev;
    };
    // NAO e subclasse: os shims de rms_vad/celia-face podem devolver a instancia nativa (a subclasse perderia os metodos). Patch por instancia.
    const achar = (obj, nome) => { for (let o = obj; o; o = Object.getPrototypeOf(o)) { const d = Object.getOwnPropertyDescriptor(o, nome); if (d) return d; } return null; };
    const instrumentar = (ws) => {
      const add = ws.addEventListener.bind(ws), rem = ws.removeEventListener.bind(ws), emb = new Map();
      ws.addEventListener = (tipo, fn, opc) => {
        if (tipo === "message" && typeof fn === "function") { let w = emb.get(fn); if (!w) { w = (e) => fn.call(ws, filtrar(e)); emb.set(fn, w); } return add(tipo, w, opc); }
        return add(tipo, fn, opc);
      };
      ws.removeEventListener = (tipo, fn, opc) => rem(tipo, (tipo === "message" && emb.get(fn)) || fn, opc);
      const d = achar(ws, "onmessage"); if (d && d.set) {
        let atual = null;
        Object.defineProperty(ws, "onmessage", { configurable: true, enumerable: true, get() { return atual; },
          set(fn) { atual = fn; d.set.call(ws, typeof fn === "function" ? (e) => fn.call(ws, filtrar(e)) : fn); } });
      }
      return ws;
    };
    const WebSocket3D = function (...args) { return instrumentar(new Nativo(...args)); };
    WebSocket3D.prototype = Nativo.prototype; for (const k of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) WebSocket3D[k] = Nativo[k];
    WebSocket3D.__celia3d = true; window.WebSocket = WebSocket3D;
  }

  function carregarBundle() {
    const el = document.getElementById("celia-bundle");
    if (!el || window.celiaEscolha.bundleImportado) return;
    window.celiaEscolha.bundleImportado = true;
    import(el.dataset.src).catch((e) => { console.error("celia-escolha: falha ao importar o bundle", e); window.celiaEscolha.erro = String((e && e.message) || e); });
  }

  function iniciar(corpo) {
    window.__celiaCorpo = corpo; window.celiaEscolha.escolhido = corpo;
    document.documentElement.dataset.celiaCorpo = corpo;
    if (corpo === "3d") {
      instalarFiltroModelo();
      const s = document.createElement("script"); s.src = "./celia-3d-interface.js?v=livefoco20261028"; s.dataset.celia = "3d"; document.head.appendChild(s);
    }
    carregarBundle();
  }

  // Pet: segue o corpo publicado e recarrega quando ele muda.
  async function iniciarPet() {
    const corpo = (await corpoPublicado()) || "2d";
    iniciar(corpo);
    const carga = Date.now();
    setInterval(async () => {
      const atual = await corpoPublicado(); if (atual && atual !== corpo) { location.reload(); return; }
      // Exclusivos (R17): se a interface escolheu "Live Mode" DEPOIS de este Pet carregar, ele se fecha pela ponte nativa (celiaPetNative.close).
      // Um "janela" mais antigo que a carga nao conta (Pet aberto a mao depois de a interface ter sido usada).
      const m = await modoPublicado();
      if (m && m.modo === "janela" && typeof m.em === "number" && m.em > carga && window.celiaPetNative && typeof window.celiaPetNative.close === "function") window.celiaPetNative.close();
    }, 1500);
  }

  function telaDeEscolha() {
    const css = document.createElement("style");
    css.textContent = `
#celia-escolha{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;background:rgba(10,10,12,.94);font:16px/1.4 "Press Start 2P",ui-monospace,monospace;color:#fff}
#celia-escolha .moldura{display:grid;grid-template-columns:1fr 1fr;gap:28px;padding:28px;background:#cfcfd2;box-shadow:0 0 0 6px #8e8e93,0 0 0 12px #2b2b2b,12px 12px 0 12px rgba(0,0,0,.45);clip-path:polygon(0 12px,12px 12px,12px 0,calc(100% - 12px) 0,calc(100% - 12px) 12px,100% 12px,100% calc(100% - 12px),calc(100% - 12px) calc(100% - 12px),calc(100% - 12px) 100%,12px 100%,12px calc(100% - 12px),0 calc(100% - 12px))}
#celia-escolha .titulo{grid-column:1/-1;margin:0;text-align:center;color:#2b2b2b;font-size:14px;letter-spacing:.04em}
#celia-escolha .admin-link{grid-column:1/-1;justify-self:center;color:#262626;font:600 13px/1.4 system-ui,sans-serif;text-decoration:underline;text-underline-offset:3px;padding:5px 10px}
#celia-escolha .admin-link:focus-visible{outline:3px solid #e8c34a;outline-offset:2px}
#celia-escolha button{all:unset;box-sizing:border-box;cursor:pointer;display:flex;flex-direction:column;align-items:stretch;gap:0;width:min(36vw,420px);aspect-ratio:668/783;max-height:78vh;padding:0;background:#222;border:6px solid #2b2b2b;overflow:hidden}
#celia-escolha .previa{flex:1;min-height:0;width:100%;object-fit:cover;object-position:50% 20%;display:block}
#celia-escolha button:hover,#celia-escolha button:focus-visible{border-color:#e8c34a;outline:none;box-shadow:0 0 0 4px #e8c34a}
#celia-escolha .placa{background:#1c1c1c;padding:12px 18px;font-size:22px;border-top:4px solid #000;text-align:center;color:#fff}
#celia-escolha .sub{font:12px/1.3 system-ui,sans-serif;color:#cfcfd2;text-align:center}
@media (max-width:640px){#celia-escolha .moldura{grid-template-columns:1fr}#celia-escolha button{width:60vw;max-height:36vh}}`;
    document.head.appendChild(css);
    const tela = document.createElement("div"); tela.id = "celia-escolha"; tela.setAttribute("role", "dialog"); tela.setAttribute("aria-modal", "true"); tela.setAttribute("aria-label", "Escolha o corpo da Célia");
    const moldura = document.createElement("div"); moldura.className = "moldura";
    const titulo = document.createElement("p"); titulo.className = "titulo"; titulo.textContent = "ESCOLHA O CORPO DA CÉLIA"; moldura.appendChild(titulo);
    const admin = document.createElement("a"); admin.className = "admin-link"; admin.href = "./admin-bots.html"; admin.textContent = "Admin local de bots"; moldura.appendChild(admin);
    const mk = (id, rotulo, sub, previa) => {
      const b = document.createElement("button"); b.type = "button"; b.dataset.corpo = id; b.title = sub; b.setAttribute("aria-label", "Iniciar com " + rotulo + ": " + sub);
      const img = document.createElement("img"); img.src = previa; img.alt = ""; img.draggable = false; img.className = "previa";
      const p = document.createElement("span"); p.className = "placa"; p.textContent = rotulo;
      b.append(img, p); b.addEventListener("click", () => escolher(id)); return b;
    };
    const b2 = mk("2d", "2D", "Live2D (como sempre)", "./media/celia-escolha/previa-2d.png?v=1"), b3 = mk("3d", "3D", "VRM, só as animações aprovadas", "./media/celia-escolha/previa-3d.png?v=1");
    moldura.append(b2, b3); tela.appendChild(moldura); document.body.appendChild(tela);
    tela.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") { b2.focus(); e.preventDefault(); } else if (e.key === "ArrowRight") { b3.focus(); e.preventDefault(); }
      else if (e.key === "Tab") { e.preventDefault(); (document.activeElement === b2 ? b3 : b2).focus(); }
    });
    b2.focus();
    function escolher(id) { tela.remove(); css.remove(); publicarCorpo(id); iniciar(id); }
  }

  const quandoPronto = () => {
    if (PET) return iniciarPet();
    if (FORCADO) return iniciar(FORCADO);
    telaDeEscolha();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", quandoPronto, { once: true }); else quandoPronto();
})();
