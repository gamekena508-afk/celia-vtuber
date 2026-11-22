/* Controle de audio de cada fonte, em cima da preview.
 *
 * Ordem do Pai (2026-08-24, com imagem): o controle fica logo abaixo do rotulo
 * vermelho da fonte e acima da miniatura, e o mesmo padrao vale para Camera e
 * Browser, nao so para Screen.
 *
 * Estrutura medida na casca compilada, com a captura ligada:
 *   [id$="content-screen"] > div (conteudo)
 *      ├ div  -> bloco do rotulo "● Screen"   (top 566)
 *      └ div  -> embrulho do <video>          (top 604)
 * O controle entra entre os dois.
 *
 * Este controle nao inventa capacidade: ele reflete as faixas de audio REAIS do
 * stream. Quando a fonte nao entrega faixa nenhuma, ele diz isso na cara em vez
 * de fingir que ligou.
 */
(function installSourceAudioControls() {
  "use strict";

  const PREFIXO_ID = "celia-audio-fonte-";
  const CHAVE = "celia-fonte-audio:";
  const FONTES = [
    { id: "screen", rotulo: "screen" },
    { id: "camera", rotulo: "camera" },
    { id: "browser", rotulo: "browser" },
  ];

  function querAudio(fonte) {
    try {
      return localStorage.getItem(CHAVE + fonte) === "1";
    } catch (_) {
      return false;
    }
  }

  function guardarQuerAudio(fonte, ligado) {
    try {
      if (ligado) localStorage.setItem(CHAVE + fonte, "1");
      else localStorage.removeItem(CHAVE + fonte);
    } catch (_) {
      // Armazenamento bloqueado nao pode derrubar o controle.
    }
  }

  function painelDa(fonte) {
    return document.querySelector('[role="tabpanel"][id$="content-' + fonte + '"]');
  }

  function faixasDe(painel) {
    const video = painel && painel.querySelector("video");
    const stream = video && video.srcObject;
    return stream && typeof stream.getAudioTracks === "function" ? stream.getAudioTracks() : [];
  }

  function estado(fonte) {
    const painel = painelDa(fonte);
    const faixas = faixasDe(painel);
    const pedido = querAudio(fonte);
    if (!faixas.length) {
      return pedido
        ? { chave: "sem_faixa_pedido", texto: "ÁUDIO ON · religa a captura", cor: "#ffce5c", ligado: true }
        : { chave: "sem_faixa", texto: "ÁUDIO OFF", cor: "rgba(255,255,255,.42)", ligado: false };
    }
    const ativo = faixas.some((faixa) => faixa.enabled);
    return ativo
      ? { chave: "ouvindo", texto: "ÁUDIO ON · ouvindo", cor: "#5ee6a8", ligado: true }
      : { chave: "mudo", texto: "ÁUDIO MUDO", cor: "rgba(255,255,255,.42)", ligado: false };
  }

  function alternar(fonte) {
    const painel = painelDa(fonte);
    const faixas = faixasDe(painel);
    const agora = estado(fonte).ligado;
    const alvo = !agora;
    guardarQuerAudio(fonte, alvo);
    // Quando existe faixa de verdade, o desligar e imediato e real.
    for (const faixa of faixas) faixa.enabled = alvo;
    window.dispatchEvent(new CustomEvent("celia-audio-fonte", { detail: { fonte, ligado: alvo } }));
    const linha = document.getElementById(PREFIXO_ID + fonte);
    if (linha) delete linha.dataset.estado;  // forca o repintar deste clique
    atualizar();
    return alvo;
  }

  function pintar(linha, fonte) {
    const info = estado(fonte);
    // Escrever textContent e uma mutacao de childList, e o observador abaixo
    // escuta childList. Repintar sem necessidade realimentava o observador e
    // travava a pagina — medido em 2026-08-24: a bancada nem carregava em 20s.
    // A chave de estado descreve o desenho inteiro, entao ela e o cache.
    if (linha.dataset.estado === info.chave) return;
    const botao = linha.querySelector("button");
    const bolinha = linha.querySelector("[data-bolinha]");
    const texto = linha.querySelector("[data-texto]");
    const nota = linha.querySelector("[data-nota]");
    linha.dataset.estado = info.chave;
    botao.setAttribute("aria-pressed", info.ligado ? "true" : "false");
    bolinha.style.background = info.ligado ? info.cor : "transparent";
    bolinha.style.borderColor = info.cor;
    texto.textContent = info.texto;
    texto.style.color = info.cor;
    if (info.chave === "sem_faixa_pedido") {
      nota.textContent = "esta fonte ainda nao entregou faixa de audio";
      nota.hidden = false;
    } else {
      nota.hidden = true;
    }
  }

  function montarLinha(fonte) {
    const linha = document.createElement("div");
    linha.id = PREFIXO_ID + fonte;
    linha.dataset.fonte = fonte;
    Object.assign(linha.style, {
      display: "flex", alignItems: "center", gap: "9px", flexWrap: "wrap",
      margin: "4px 0 8px", padding: "6px 9px", borderRadius: "9px",
      border: "1px solid rgba(255,255,255,.12)", background: "rgba(255,255,255,.035)",
    });

    const botao = document.createElement("button");
    botao.type = "button";
    botao.setAttribute("aria-label", "Audio da fonte " + fonte);
    botao.setAttribute("aria-pressed", "false");
    botao.title = "Liga e desliga o audio desta fonte";
    Object.assign(botao.style, {
      display: "inline-flex", alignItems: "center", gap: "7px", padding: "3px 4px",
      border: "0", background: "transparent", cursor: "pointer", color: "inherit",
      font: "700 10.5px/1 system-ui, sans-serif", letterSpacing: ".1em",
    });

    const bolinha = document.createElement("span");
    bolinha.dataset.bolinha = "true";
    Object.assign(bolinha.style, {
      width: "9px", height: "9px", borderRadius: "50%",
      border: "1px solid rgba(255,255,255,.5)", background: "transparent", flex: "0 0 auto",
    });

    const texto = document.createElement("span");
    texto.dataset.texto = "true";
    texto.textContent = "ÁUDIO OFF";

    botao.append(bolinha, texto);
    botao.addEventListener("click", (evento) => {
      evento.preventDefault();
      evento.stopPropagation();
      alternar(fonte);
    });

    const nota = document.createElement("small");
    nota.dataset.nota = "true";
    nota.hidden = true;
    Object.assign(nota.style, {
      color: "rgba(255,255,255,.4)", font: "400 10.5px/1.3 system-ui, sans-serif",
    });

    linha.append(botao, nota);
    return linha;
  }

  function instalarEm(fonte) {
    const painel = painelDa(fonte);
    if (!painel) return false;
    if (painel.querySelector("#" + PREFIXO_ID + fonte)) return true;

    const conteudo = painel.firstElementChild;
    if (!conteudo || conteudo.children.length < 2) return false;

    // Ancora por ESTRUTURA, nao por texto. Medido em 2026-08-24 nos tres
    // paineis, com a captura ligada e desligada: o primeiro filho do conteudo e
    // sempre o bloco de rotulo, e a preview vem depois dele.
    //   camera  -> [bloco rotulo "Live"], [embrulho com <video>], [tooltip]
    //   screen  -> [bloco rotulo "Screen"], [embrulho com <video>]
    //   browser -> [bloco rotulo], [embrulho "No active browser session"]
    // A versao anterior procurava um <p> com o nome da fonte e so achava no
    // Screen: o rotulo da Camera diz "Live", nao "Camera". Nome de classe
    // tambem nao serve — o bundle embaralha a cada build.
    const bloco = conteudo.firstElementChild;
    if (!bloco || bloco.querySelector("video")) return false;

    bloco.insertAdjacentElement("afterend", montarLinha(fonte));
    return true;
  }

  let agendado = false;

  function agendar() {
    if (agendado) return;
    agendado = true;
    requestAnimationFrame(() => {
      agendado = false;
      atualizar();
    });
  }

  function atualizar() {
    for (const fonte of FONTES) {
      instalarEm(fonte.id);
      const linha = document.getElementById(PREFIXO_ID + fonte.id);
      if (linha) pintar(linha, fonte.id);
    }
  }

  window.celiaAudioFontes = {
    estado: (fonte) => estado(fonte),
    alternar,
    querAudio,
    fontes: () => FONTES.map((f) => f.id),
  };

  new MutationObserver(agendar).observe(document.documentElement, { childList: true, subtree: true });
  // As faixas mudam sem mexer no DOM (o usuario para a captura), entao um pulso
  // curto mantem o rotulo honesto sem depender de mutacao.
  window.setInterval(agendar, 1000);
  if (document.body) agendar();
  else document.addEventListener("DOMContentLoaded", agendar, { once: true });
}());
