/*
 * 2026-10-07 (ordem do Pai, caixa 3 do print numerado): a LEGENDA do palco ("Connection established" / "Thinking..." / fala) sai de vez. Ela fica
 * sempre invisivel (visibility:hidden — o layout nao muda, as caixas de debug continuam no lugar dela) e as caixas nunca sobem para "acima da
 * legenda". A legenda do Pet (janela separada, ws :8788) e o overlay do OBS nao sao tocados. Reverter: celiaCaixotesPalco.legendaVisivel = true.
 *
 * celia-caixotes-palco.js — as duas caixas de debug (THINKING e PROCESSOS, de celia-debug-boxes.js) saem de cima da camera, no canto do chat,
 * e passam a ocupar o lugar da caixinha "Connection established" no meio do palco (pedido do Pai, 2026-10-07; ele escolheu a caixa 5 e as caixas 1 e 2
 * no print numerado).
 *
 * O QUE E A CAIXINHA: e a LEGENDA do bundle compilado (Subtitle, texto inicial "Connection established"). Por isso ela nao some de vez:
 *   - enquanto mostra so o texto inicial, fica INVISIVEL (visibility:hidden, o layout nao muda) e as caixas de debug ocupam o lugar dela;
 *   - quando a Celia fala e a legenda traz texto de verdade, a legenda volta e as caixas sobem para logo ACIMA dela (nada fica escondido).
 * Nao cria legenda nem janela, nao toca no #celia-debug-boxes alem de posicao/largura, e nao mexe no Pet (la nao ha legenda do bundle: nada acontece).
 * Desligar:  celiaCaixotesPalco.desligar()  (volta a posicao original)  /  .ligar()
 */
(function () {
  "use strict";
  if (window.celiaCaixotesPalco) return;
  const INICIAL = "Connection established";
  const TEXTOS = [INICIAL, "Thinking..."];
  const estado = { ligado: true, legenda: null, ultimoTexto: null, voltas: 0, legendaVisivel: false };
  window.celiaCaixotesPalco = estado;

  function acharLegenda() {
    if (estado.legenda && estado.legenda.isConnected) return estado.legenda;
    estado.legenda = null;
    const raiz = document.getElementById("root"); if (!raiz) return null;
    // so vale enquanto o texto inicial existir; depois a referencia fica guardada (o React reaproveita o mesmo no ao trocar o texto)
    const w = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (TEXTOS.includes(n.textContent.trim()) && n.parentElement) {
        let el = n.parentElement;
        for (let i = 0; i < 4 && el.parentElement && el.parentElement !== raiz; i++) { const cs = getComputedStyle(el); if (cs.backgroundColor !== "rgba(0, 0, 0, 0)" && el.getBoundingClientRect().width > 80) break; el = el.parentElement; }
        estado.legenda = el; return el;
      }
    }
    return null;
  }

  function restaurar() {
    const caixa = document.getElementById("celia-debug-boxes");
    if (caixa) { caixa.style.left = "10px"; caixa.style.bottom = "10px"; caixa.style.width = "320px"; }
    if (estado.legenda) estado.legenda.style.visibility = "";
  }

  function ajustar() {
    if (!estado.ligado) return;
    const caixa = document.getElementById("celia-debug-boxes"); const leg = acharLegenda();
    if (!caixa || !leg) return;
    const r = leg.getBoundingClientRect(); if (r.width < 40) return;
    const texto = leg.textContent.trim(); estado.ultimoTexto = texto;
    const placeholder = !estado.legendaVisivel || texto === INICIAL || texto === "";
    leg.style.visibility = placeholder ? "hidden" : "";
    caixa.style.left = Math.round(r.left) + "px"; caixa.style.width = Math.round(r.width) + "px";
    // placeholder: a caixa ocupa o lugar da legenda (mesma base); com fala: sobe para cima da legenda
    const base = window.innerHeight - r.bottom;
    caixa.style.bottom = Math.round(placeholder ? base : base + r.height + 8) + "px";
  }

  estado.desligar = () => { estado.ligado = false; restaurar(); };
  estado.ligar = () => { estado.ligado = true; ajustar(); };
  setInterval(ajustar, 300);
  window.addEventListener("resize", ajustar);
})();
