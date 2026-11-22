/*
 * celia-modo-menu.js — o V (check) do menu de modo reflete o modo REAL (ordem do Pai, 2026-10-07).
 *
 * O menu "Mode Menu" da casca compilada tem dois itens de radio: "Live Mode" (value=window) e "Pet Mode" (value=pet). O V vem do estado interno
 * da casca, que no navegador nunca sai de "window" (celia-browser-pet.js responde "window" de proposito: emitir "mode-changed: pet" faria o bundle
 * montar o layout legado de Pet e esconder o chat). Resultado: com o Pet na tela, o V continuava em "Live Mode".
 *
 * Aqui so se acerta o DESENHO dos dois itens quando o menu esta aberto: o V vai para o modo que celiaBrowserPet.state().mode informa
 * ("pet" quando o Pet esta vivo — adotado ou lancado —, "window" caso contrario). Nao escuta clique, nao chama setMode, nao mexe no estado.
 * Ancora: role="menuitemradio" + o texto do item (o bundle embaralha classes, o texto "Live Mode"/"Pet Mode" e fixo).
 */
(function () {
  "use strict";
  if (window.celiaModoMenu) return;
  if (new URLSearchParams(location.search).has("pet")) return;
  const estado = (window.celiaModoMenu = { modo: null, ajustes: 0 });

  function modoAtual() {
    try {
      const st = window.celiaBrowserPet && window.celiaBrowserPet.state();
      if (st && (st.mode === "pet" || st.desktopPetLaunch === "requested")) return "pet";
    } catch (_) { /* sem a camada: janela */ }
    return "window";
  }

  function marcar(item, ligado) {
    const v = ligado ? "true" : "false", s = ligado ? "checked" : "unchecked";
    if (item.getAttribute("aria-checked") !== v) { item.setAttribute("aria-checked", v); estado.ajustes += 1; }
    if (item.dataset.state !== s) item.dataset.state = s;
    const indicador = item.querySelector('[data-part="item-indicator"]');
    if (indicador) {
      if (indicador.dataset.state !== s) indicador.dataset.state = s;
      if (indicador.hidden === ligado) indicador.hidden = !ligado;
    }
  }

  function ajustar() {
    const itens = document.querySelectorAll('[role="menuitemradio"]');
    if (!itens.length) return;
    const modo = (estado.modo = modoAtual());
    for (const item of itens) {
      const texto = (item.textContent || "").trim();
      if (texto === "Live Mode" || texto === "Interface Mode") {
        const walker=document.createTreeWalker(item,NodeFilter.SHOW_TEXT);
        while(walker.nextNode()) if(walker.currentNode.nodeValue.includes('Live Mode')) walker.currentNode.nodeValue=walker.currentNode.nodeValue.replace('Live Mode','Interface Mode');
        marcar(item, modo === "window");
      }
      else if (texto === "Pet Mode") marcar(item, modo === "pet");
    }
  }

  // Modo Móvel é um terceiro destino de apresentação, sem mexer no bundle compilado
  // nem chamar setMode("pet")/fechar a janela Electron. Reinsere se o menu React remontar.
  function adicionarMovel() {
    const itens = Array.from(document.querySelectorAll('[role="menuitemradio"]'));
    const pet = itens.find((item) => (item.textContent || "").trim() === "Pet Mode");
    if (!pet || !pet.parentElement || pet.parentElement.querySelector('[data-celia-mobile-mode]')) return;
    const item = document.createElement("div");
    item.setAttribute("role", "menuitem");
    item.setAttribute("tabindex", "0");
    item.dataset.celiaMobileMode = "1";
    item.className = pet.className;
    item.textContent = "Mobile Mode · Móvel";
    item.style.cursor = "pointer";
    item.setAttribute("aria-label", "Abrir Modo Móvel no PC");
    const abrir = (event) => {
      event.preventDefault();
      event.stopPropagation();
      location.assign("/mobile/studio");
    };
    item.addEventListener("click", abrir);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") abrir(event);
    });
    pet.insertAdjacentElement("afterend", item);
  }
  function adicionarCenaLive() {
    const itens = Array.from(document.querySelectorAll('[role="menuitemradio"]'));
    const pet = itens.find((item) => (item.textContent || "").trim() === "Pet Mode");
    if (!pet || !pet.parentElement || pet.parentElement.querySelector('[data-celia-cena-live]')) return;
    const item = document.createElement("div");
    item.setAttribute("role", "menuitem");
    item.setAttribute("tabindex", "0");
    item.setAttribute("aria-label", "Abrir cena do modo Live da Célia");
    item.dataset.celiaCenaLive = "1";
    item.className = pet.className;
    item.textContent = "Modo Live da Célia";
    item.style.cursor = "pointer";
    const abrir = (event) => {
      event.preventDefault();
      event.stopPropagation();
      location.assign("http://127.0.0.1:12593/?ativar=1");
    };
    item.addEventListener("click", abrir);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") abrir(event);
    });
    pet.insertAdjacentElement("afterend", item);
  }
  setInterval(() => { ajustar(); adicionarMovel(); adicionarCenaLive(); }, 120);
})();
