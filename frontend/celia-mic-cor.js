/*
 * celia-mic-cor.js — cor do botao do microfone (ordem do Pai, 2026-10-07): DESLIGADO = azul escuro, LIGADO = azul claro.
 * A casca compilada pinta o botao com `bg: ligado ? "green.500" : "red.500"` e troca o icone (BsMicFill / BsMicMuteFill). Aqui so se le o icone
 * (o desenho do mic cortado por uma barra termina em "-12-12z") e se marca o botao com data-celia-mic="on|off"; a cor vem do CSS (!important
 * vence a classe do emotion, hover incluido). Nao liga, nao desliga e nao le o microfone: so reflete o que o bundle ja desenhou.
 * Ancora estrutural (o bundle embaralha classes): o botao da linha de acoes (abaixo do selo) que NAO e o "Raise hand".
 */
(function () {
  "use strict";
  if (window.celiaMicCor) return;
  if (new URLSearchParams(location.search).has("pet")) return;
  const estado = (window.celiaMicCor = { botao: null, estado: null });

  const css = document.createElement("style");
  css.textContent = `
button[data-celia-mic="off"]{background-color:#12306e!important;box-shadow:inset 0 0 0 1px rgba(255,255,255,.12)}
button[data-celia-mic="on"]{background-color:#58b7ff!important;box-shadow:0 0 10px rgba(88,183,255,.55)}
button[data-celia-mic]{transition:background-color .2s ease,box-shadow .2s ease}
button[data-celia-mic]:hover{filter:brightness(1.12)}`;
  document.head.appendChild(css);

  function acharMic() {
    const campo = document.querySelector('textarea, input[placeholder*="message" i]');
    if (!campo) return null;
    for (let linha = campo.parentElement; linha && linha !== document.body; linha = linha.parentElement) {
      if (linha.children.length !== 2) continue;
      const [esq, dir] = linha.children;
      if (esq.contains(campo) || !dir.contains(campo)) continue;
      const botoes = [...esq.querySelectorAll("button")].filter((b) => b.id !== "celia-corpo-interruptor" && b.getAttribute("aria-label") !== "Raise hand" && b.querySelector("svg"));
      if (botoes.length) return botoes[0];
    }
    return null;
  }

  function atualizar() {
    const b = acharMic();
    if (!b) return;
    const mudo = /-12-12z/.test(b.innerHTML);
    const v = mudo ? "off" : "on";
    if (b.dataset.celiaMic !== v) b.dataset.celiaMic = v;
    estado.botao = b; estado.estado = v;
  }

  setInterval(atualizar, 400);
  window.addEventListener("resize", atualizar);
})();
