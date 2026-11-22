/*
 * celia-corpo-interruptor.js — interruptor 2D | 3D dentro da interface (ordem do Pai, 2026-10-07, caixa 7 do print numerado).
 *
 * Ocupa o lugar do selo de estado ("idle", "thinking/speaking"...) acima dos botoes de microfone/mao, na barra de entrada do chat.
 * Lado esquerdo 2D, lado direito 3D; o botao desliza e a trilha troca de cor: 2D = vermelho, 3D = verde. O botao (escuro, ordem do Pai) traz o modo ATUAL escrito;
 * o rotulo do outro lado (o modo para onde se vai) fica na trilha. O estado antigo do selo continua
 * legivel (dica do interruptor), so deixou de ocupar o lugar.
 *
 * Trocar = celiaEscolha.trocar(corpo): grava a escolha em POST /bridge/body (o Pet le de la) e recarrega com ?corpo=. Nada e lembrado alem disso.
 * Nao aparece no Pet (?pet=1) nem antes da escolha. Ancora ESTRUTURAL (o bundle embaralha classes): a linha de duas colunas cuja segunda coluna
 * contem o campo de texto do chat; a primeira coluna e a do selo. Medido: coluna 110x86, selo 110x30.
 */
(function () {
  "use strict";
  if (window.celiaCorpoInterruptor) return;
  const Q = new URLSearchParams(location.search);
  if (Q.has("pet")) return;
  const estado = { montado: false, corpo: null, trocando: false };
  window.celiaCorpoInterruptor = estado;

  const css = document.createElement("style");
  css.textContent = `
#celia-corpo-interruptor{position:absolute;z-index:3;box-sizing:border-box;display:block;margin:0;padding:0;border:0;border-radius:15px;cursor:pointer;background:#d6403f;
 box-shadow:inset 0 0 0 1px rgba(0,0,0,.35),0 1px 6px rgba(0,0,0,.35);transition:background .28s ease;user-select:none;-webkit-tap-highlight-color:transparent;font:700 12px/30px system-ui,sans-serif;letter-spacing:.04em}
#celia-corpo-interruptor[aria-checked="true"]{background:#2faa54}
#celia-corpo-interruptor .rotulo{position:absolute;top:0;width:50%;text-align:center;color:rgba(255,255,255,.62);transition:color .28s ease;pointer-events:none}
#celia-corpo-interruptor .rotulo.e{left:0}#celia-corpo-interruptor .rotulo.d{right:0}
#celia-corpo-interruptor[aria-checked="false"] .rotulo.e,#celia-corpo-interruptor[aria-checked="true"] .rotulo.d{color:transparent}
#celia-corpo-interruptor[aria-checked="false"] .rotulo.d,#celia-corpo-interruptor[aria-checked="true"] .rotulo.e{color:#fff}
#celia-corpo-interruptor .botao::after{content:"2D"}#celia-corpo-interruptor[aria-checked="true"] .botao::after{content:"3D"}
#celia-corpo-interruptor .botao{position:absolute;top:3px;left:3px;width:49px;height:24px;border-radius:12px;background:#17181d;box-shadow:0 1px 4px rgba(0,0,0,.55),inset 0 0 0 1px rgba(255,255,255,.14);
 transition:transform .28s cubic-bezier(.3,.7,.3,1);pointer-events:none;display:flex;align-items:center;justify-content:center;color:#fff;font:800 12px/1 system-ui,sans-serif}
#celia-corpo-interruptor[aria-checked="true"] .botao{transform:translateX(55px)}
#celia-corpo-interruptor:focus-visible{outline:2px solid #e8c34a;outline-offset:2px}
@media (prefers-reduced-motion:reduce){#celia-corpo-interruptor,#celia-corpo-interruptor .botao,#celia-corpo-interruptor .rotulo{transition:none}}`;
  document.head.appendChild(css);

  function acharSelo() {
    const campo = document.querySelector('textarea, input[placeholder*="message" i]');
    if (!campo) return null;
    for (let linha = campo.parentElement; linha && linha !== document.body; linha = linha.parentElement) {
      if (linha.children.length !== 2) continue;
      const [esq, dir] = linha.children;
      if (esq.contains(campo) || !dir.contains(campo)) continue;
      const r = linha.getBoundingClientRect();
      if (r.height > 160 || !esq.firstElementChild) continue;
      const selo = esq.firstElementChild, rs = selo.getBoundingClientRect();
      if (rs.width < 60 || rs.height < 18 || rs.height > 60) continue;
      return { coluna: esq, selo };
    }
    return null;
  }

  function montar() {
    if (!window.__celiaCorpo) return;
    const alvo = acharSelo();
    const atual = document.getElementById("celia-corpo-interruptor");
    if (!alvo) return;
    if (atual && atual.parentElement === alvo.coluna) { posicionar(atual, alvo); return; }
    if (atual) atual.remove();
    const b = document.createElement("button");
    b.id = "celia-corpo-interruptor"; b.type = "button"; b.setAttribute("role", "switch");
    b.setAttribute("aria-label", "Corpo da Célia: 2D ou 3D");
    b.innerHTML = '<span class="rotulo e">2D</span><span class="rotulo d">3D</span><span class="botao"></span>';
    b.addEventListener("click", () => alternar(b));
    if (getComputedStyle(alvo.coluna).position === "static") alvo.coluna.style.position = "relative";
    alvo.coluna.appendChild(b);
    estado.montado = true;
    posicionar(b, alvo);
  }

  function posicionar(b, alvo) {
    const c = alvo.coluna.getBoundingClientRect(), s = alvo.selo.getBoundingClientRect();
    b.style.left = Math.round(s.left - c.left) + "px"; b.style.top = Math.round(s.top - c.top) + "px";
    b.style.width = Math.round(s.width) + "px"; b.style.height = Math.round(s.height) + "px";
    // o selo continua ocupando o espaco, so invisivel; o texto dele vira a dica do interruptor
    if (alvo.selo.style.visibility !== "hidden") alvo.selo.style.visibility = "hidden";
    const texto = (alvo.selo.textContent || "").trim();
    const corpo = window.__celiaCorpo === "3d" ? "3D" : "2D";
    const dica = "Corpo: " + corpo + (texto ? " · estado: " + texto : "") + " — clique para trocar";
    if (b.title !== dica) b.title = dica;
    const ligado = window.__celiaCorpo === "3d" ? "true" : "false";
    if (b.getAttribute("aria-checked") !== ligado) b.setAttribute("aria-checked", ligado);
    estado.corpo = window.__celiaCorpo;
  }

  async function alternar(b) {
    if (estado.trocando || !window.celiaEscolha || typeof window.celiaEscolha.trocar !== "function") return;
    const proximo = window.__celiaCorpo === "3d" ? "2d" : "3d";
    estado.trocando = true;
    b.setAttribute("aria-checked", proximo === "3d" ? "true" : "false"); // animacao antes de recarregar
    await new Promise((r) => setTimeout(r, 320));
    const ok = await window.celiaEscolha.trocar(proximo);
    if (!ok) { estado.trocando = false; b.setAttribute("aria-checked", window.__celiaCorpo === "3d" ? "true" : "false"); }
  }

  estado.alternar = () => { const b = document.getElementById("celia-corpo-interruptor"); return b ? alternar(b) : false; };
  setInterval(montar, 400);
  window.addEventListener("resize", montar);
})();
