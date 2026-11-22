/*
 * celia-config-extra.js — arrumacao das Configuracoes que NAO depende de decisao do Pai (2026-10-07, R18).
 *
 *  1. Texto quebrado (mojibake) que ja vem DENTRO do bundle compilado: "Â©" (copyright da aba About) e a opcao de idioma "ä¸­æ–‡"
 *     (que deveria ser 中文). O arquivo do bundle foi gravado com UTF-8 lido como Latin-1; ninguem recompila o bundle, entao o texto e consertado no
 *     DOM, so dentro do dialogo de Configuracoes e so por troca EXATA de sequencia (nada de heuristica).
 *  2. Aba TTS (vazia na casca): "Voz atual", lida de /debug/observability/runtime (somente leitura; nao troca nada).
 *
 * Nao muda nenhuma opcao, nao salva nada, nao toca nas outras abas. Observador so de childList + rAF + escrita idempotente (regra da arvore:
 * observador de DOM que escreve DOM sem cache trava a pagina).
 */
(function () {
  "use strict";
  if (window.celiaConfigExtra) return;
  const estado = (window.celiaConfigExtra = { reparos: 0, ttsLido: 0 });
  const ENDPOINT = "/debug/observability/runtime";
  const REPAROS = [[/Â©/g, "©"], [/ä¸­?æ–‡/g, "中文"]];

  function reparar(dialogo) {
    const w = document.createTreeWalker(dialogo, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const antes = n.nodeValue;
      if (!antes || antes.length > 200) continue;
      let depois = antes;
      for (const [re, para] of REPAROS) depois = depois.replace(re, para);
      if (depois !== antes) { n.nodeValue = depois; estado.reparos += 1; }
    }
  }

  function linha(container, rotulo, valor) {
    const row = document.createElement("div");
    row.style.cssText = "display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr);gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.12);font-size:13px;";
    const nome = document.createElement("span");
    nome.textContent = rotulo;
    nome.style.cssText = "color:rgba(255,255,255,.72);font-weight:600;";
    const atual = document.createElement("code");
    atual.textContent = valor;
    atual.style.cssText = "color:#b8ebff;overflow-wrap:anywhere;font:12px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace;";
    row.append(nome, atual);
    container.append(row);
  }

  function desenhar(secao, snapshot) {
    const voz = (snapshot && snapshot.voice) || {};
    const ativo = (voz.active_preset && voz.active_preset.preset) || {};
    const ou = (v, f = "não informado") => (v === null || v === undefined || v === "" ? f : String(v));
    secao.replaceChildren();
    const titulo = document.createElement("h3");
    titulo.textContent = "Voz atual";
    titulo.style.cssText = "margin:0 0 6px;font-size:16px;";
    const legenda = document.createElement("p");
    legenda.textContent = "Leitura do processo local carregado. Somente leitura: esta aba não troca a voz.";
    legenda.style.cssText = "margin:0 0 12px;color:rgba(255,255,255,.72);font-size:12px;line-height:1.45;";
    const linhas = document.createElement("div");
    linha(linhas, "Voz carregada", voz.engine_loaded === true ? "sim" : voz.engine_loaded === false ? "não" : "não informado");
    linha(linhas, "Preset ativo", ou(ativo.label) + (ativo.name ? " (" + ativo.name + ")" : ""));
    linha(linhas, "Descrição", ou(ativo.description));
    linha(linhas, "Velocidade", ou(ativo.speed));
    linha(linhas, "Passos de difusão", ou(ativo.num_step));
    linha(linhas, "Guidance", ou(ativo.guidance_scale));
    linha(linhas, "Seed", ou(ativo.seed));
    linha(linhas, "Tag de texto", ou(ativo.text_tag, "nenhuma"));
    const saude = String(voz.transport_health || "");
    linha(linhas, "Saúde do transporte", /^NOT PROBED/i.test(saude) ? "não verificada por esta leitura" : ou(saude));
    secao.append(titulo, legenda, linhas);
  }

  function falha(secao) {
    secao.replaceChildren();
    const t = document.createElement("h3");
    t.textContent = "Voz indisponível";
    t.style.cssText = "margin:0 0 6px;font-size:16px;";
    const m = document.createElement("p");
    m.textContent = "Não foi possível ler o processo local agora.";
    m.style.cssText = "margin:0;color:rgba(255,255,255,.72);font-size:12px;line-height:1.45;";
    secao.append(t, m);
  }

  async function ler(secao) {
    if (secao.dataset.lendo === "true") return;
    secao.dataset.lendo = "true";
    try {
      const r = await fetch(ENDPOINT, { credentials: "same-origin", cache: "no-store" });
      if (!r.ok) throw new Error("runtime_" + r.status);
      desenhar(secao, await r.json());
      estado.ttsLido += 1;
    } catch (_) {
      falha(secao);
    } finally {
      secao.dataset.lendo = "false";
      secao.dataset.lidoEm = String(Date.now());
    }
  }

  function aba(dialogo) {
    const tab = dialogo.querySelector('[role="tab"][data-value="tts"]');
    const painel = dialogo.querySelector('[role="tabpanel"][id$="content-tts"]');
    if (!tab || !painel) return;
    let secao = painel.querySelector("#celia-tts-settings");
    if (!secao) {
      secao = document.createElement("section");
      secao.id = "celia-tts-settings";
      secao.setAttribute("aria-live", "polite");
      secao.style.cssText = "padding:4px 1px 10px;";
      painel.append(secao);
    }
    const aberta = tab.getAttribute("aria-selected") === "true";
    const velho = Date.now() - Number(secao.dataset.lidoEm || 0) > 5000;
    if (aberta && velho && secao.dataset.lendo !== "true") ler(secao);
    if (!tab.dataset.celiaTtsBound) {
      tab.dataset.celiaTtsBound = "true";
      tab.addEventListener("click", () => window.setTimeout(() => ler(secao), 0));
    }
  }

  let agendado = false;
  function agir() {
    agendado = false;
    for (const d of document.querySelectorAll('[role="dialog"]')) { reparar(d); aba(d); }
  }
  function agendar() {
    if (agendado) return;
    agendado = true;
    requestAnimationFrame(agir);
  }
  new MutationObserver(agendar).observe(document.documentElement, { childList: true, subtree: true });
  agendar();
})();
