/* Adds the recording panel below Pet Mode without replacing the React UI. */
(() => {
  const ITEM_ID = "celia-roteiro-live-menu-item";

  /*
   * ABRIR O PAINEL NAO PODE ESCOLHER O ROTEIRO — medido em 2026-09-30.
   *
   * Este botao chamava POST /bridge/roteiro/start. Um clique causava DOIS
   * estragos de uma vez, reproduzidos com o roteiro 3D carregado:
   *
   *   1. a rota forca o perfil `roteiro`, e o handler de `profile.changed`
   *      chama prepare_operational_script(behavior, None), que recarrega
   *      roteiros/ia_vs_criador.json do disco POR CIMA do roteiro escolhido —
   *      inclusive sobrescrevendo var/continuous_behavior_script.json;
   *   2. a rota marca a gravacao como ATIVA, e o seletor de roteiro do painel
   *      so aparece quando `!snapshot.active` (frontend/roteiro-live/app.js).
   *      Resultado: o dono perdia o roteiro E perdia a tela de escolha.
   *
   * GET /bridge/roteiro/start-info devolve as mesmas lan_urls sem tocar em
   * perfil, roteiro ou estado. Medido: depois de chama-la, o disco continuava
   * em celia-3d-passo-a-passo e o perfil em `gravacao`.
   *
   * Quem escolhe o roteiro e o dono, no proprio painel (POST
   * /roteiro/scripts/select, que usa o perfil `gravacao` e preserva). Abrir a
   * janela nao e escolher nada. Nao volte a usar /bridge/roteiro/start aqui.
   *
   * O campo "profile":"roteiro" no retorno de start-info e rotulo informativo
   * da rota; ela nao aplica perfil nenhum.
   */
  async function abrirRoteiro() {
    let url = `${location.origin}/roteiro/`;
    let data = null;
    try {
      const response = await fetch("/bridge/roteiro/start-info");
      if (response.ok) {
        data = await response.json();
        url = (data.lan_urls || [])[0] || url;
      }
    } catch (error) {
      // Sem a URL da LAN o painel ainda abre local. Perder o endereco do
      // celular e menos grave que nao abrir.
      console.warn("Roteiro Live: nao consegui ler o endereco da LAN", error);
    }
    window.open("/roteiro/", "celia-roteiro-live", "noopener");
    showUrl(url);
    window.dispatchEvent(new CustomEvent("celia-roteiro-live-started", { detail: { url, data } }));
  }

  function showUrl(url) {
    let notice = document.getElementById("celia-roteiro-live-url");
    if (!notice) {
      notice = document.createElement("div");
      notice.id = "celia-roteiro-live-url";
      Object.assign(notice.style, {
        background: "#000", border: "1px solid #fff", bottom: "20px", color: "#fff",
        font: "14px system-ui", left: "20px", maxWidth: "min(90vw, 620px)", padding: "12px 16px",
        position: "fixed", zIndex: "2147483647"
      });
      document.body.appendChild(notice);
    }
    notice.textContent = `ROTEIRO LIVE no celular: ${url}`;
    clearTimeout(showUrl.timer);
    showUrl.timer = setTimeout(() => notice.remove(), 12000);
  }

  function install() {
    if (document.getElementById(ITEM_ID)) return;
    const items = [...document.querySelectorAll('[role="menuitemradio"], [role="menuitem"], button')];
    const pet = items.find((item) => item.textContent.trim() === "Pet Mode");
    if (!pet || !pet.parentElement) return;
    const item = pet.cloneNode(true);
    item.id = ITEM_ID;
    item.removeAttribute("disabled");
    item.setAttribute("aria-label", "Roteiro Live");
    item.title = "Abrir teleprompter de gravação no celular";
    item.textContent = "Roteiro Live";
    item.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      item.setAttribute("aria-busy", "true");
      try { await abrirRoteiro(); } catch (error) { console.warn("Roteiro Live indisponível", error); }
      item.removeAttribute("aria-busy");
    });
    pet.insertAdjacentElement("afterend", item);
  }

  new MutationObserver(install).observe(document.documentElement, { childList: true, subtree: true });
  install();
})();
