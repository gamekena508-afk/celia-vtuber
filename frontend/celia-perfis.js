/* Operational profiles share one runtime; profile changes never grant tool authority. */
(function installProfileSelector() {
  "use strict";

  const BOTAO_ID = "celia-perfis-botao";
  const MENU_ID = "celia-perfis-menu";
  const ENDPOINT = "/bridge/behavior/profile";

  const TRAVADOS = new Set();

  const SERIE_ID = "conversation_autonomous";
  // Perfis de fala contínua: a cota é por MINUTO (substitui os tetos de 10 min e por hora do servidor). Valor = padrão do perfil.
  const CONTINUOS = { conversation_autonomous: 4, autonomous: 6 };
  const PERFIS = [
    { id: SERIE_ID, nome: "Conversa Autônoma", detalhe: "papo normal + iniciativa curta no silêncio; joga Minecraft ao seu lado" },
    { id: "chat", nome: "Conversa", detalhe: "papo normal, ela responde quando chamada" },
    { id: "roteiro", nome: "Roteiro", detalhe: "segue o teleprompter da gravacao" },
    { id: "gravacao", nome: "Gravação", detalhe: "roteiro flexível + visão + co-host/co-professora" },
    { id: "cohost", nome: "Co-host", detalhe: "participacao seletiva e timing social" },
    { id: "autonomous", nome: "Autônoma", detalhe: "sustenta a live e participa mesmo sem o Criador" },
  ];

  let perfilAtual = null;
  let carregando = false;

  function icone() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "20");
    svg.setAttribute("height", "20");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.8");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    const cracha = document.createElementNS("http://www.w3.org/2000/svg", "path");
    cracha.setAttribute("d", "M4 7h16v13H4z M9 7V4h6v3");
    const linha = document.createElementNS("http://www.w3.org/2000/svg", "path");
    linha.setAttribute("d", "M8 13h8");
    svg.append(cracha, linha);
    return svg;
  }

  async function lerPerfil() {
    try {
      const resposta = await fetch(ENDPOINT, { credentials: "same-origin" });
      if (!resposta.ok) throw new Error("perfil_" + resposta.status);
      const dados = await resposta.json();
      perfilAtual = dados && dados.profile ? String(dados.profile) : null;
    } catch (_) {
      // Servidor fora do ar nao pode inventar um perfil ativo.
      perfilAtual = null;
    }
    return perfilAtual;
  }

  async function trocarPerfil(id) {
    if (TRAVADOS.has(id) || carregando) return false;
    carregando = true;
    try {
      const resposta = await fetch(ENDPOINT, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: id }),
      });
      if (!resposta.ok) throw new Error("perfil_post_" + resposta.status);
      const dados = await resposta.json();
      perfilAtual = dados && dados.profile ? String(dados.profile) : id;
      window.dispatchEvent(new CustomEvent("celia-perfil-mudou", { detail: { profile: perfilAtual } }));
      return true;
    } catch (_) {
      return false;
    } finally {
      carregando = false;
      desenharMenu();
    }
  }

  function fecharMenu() {
    const menu = document.getElementById(MENU_ID);
    if (menu) menu.remove();
    const botao = document.getElementById(BOTAO_ID);
    if (botao) botao.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", cliqueFora, true);
    document.removeEventListener("keydown", teclaEscape, true);
  }

  function cliqueFora(evento) {
    const menu = document.getElementById(MENU_ID);
    const botao = document.getElementById(BOTAO_ID);
    if (!menu) return;
    if (menu.contains(evento.target) || (botao && botao.contains(evento.target))) return;
    fecharMenu();
  }

  function teclaEscape(evento) {
    if (evento.key === "Escape") fecharMenu();
  }

  // V6 editor extends this selector instead of adding a parallel profile UI.
  const TRIGGERS_V6 = {
    direct_question: "Pergunta direta", achievement: "Conquista", danger: "Perigo",
    combat: "Combate", damage: "Dano", death: "Morte", respawn: "Respawn",
    discovery: "Descoberta", goal_complete: "Objetivo concluído",
    goal_failed: "Falha de objetivo", world_change: "Mudança do mundo",
    audience: "Chat da audiência", script_beat: "Beat do roteiro", silence: "Silêncio"
  };
  const SOCIAL_V6 = [
    ["participation", "Participação espontânea", 0, 100, "range"],
    ["interruption", "Permissão para interromper", 0, 100, "range"],
    ["silence_seconds", "Reação ao silêncio (s)", 2, 120, "number"],
    ["max_spontaneous_per_10m", "Falas espontâneas / 10 min", 0, 60, "number"],
    ["response_words", "Palavras por resposta", 5, 300, "number"],
    ["inference_budget_per_hour", "Limite proativo / hora", 0, 240, "number"],
    ["screen_awareness", "Observar tela", 0, 1, "checkbox"],
    ["script_tracking", "Acompanhar roteiro", 0, 1, "checkbox"],
    ["audience_chat", "Participar do chat", 0, 1, "checkbox"],
    ["respect_concentration", "Respeitar concentração", 0, 1, "checkbox"],
  ];
  const GAME_V6 = [
    ["ideal_distance", "Distância ideal (blocos)", 2, 128, "nullable"],
    ["max_distance", "Distância máxima (blocos)", 4, 256, "nullable"],
    ["collection_radius", "Raio para coleta (blocos)", 0, 128, "number"],
    ["return_delay_seconds", "Retornar após (s)", 0, 120, "number"],
    ["spontaneous_collection", "Coleta espontânea", 0, 1, "checkbox"],
    ["defensive_combat", "Combate defensivo", 0, 1, "checkbox"],
    ["allow_exploration", "Exploração livre", 0, 1, "checkbox"],
    ["return_after_goal", "Retornar depois da tarefa", 0, 1, "checkbox"],
    ["pause_on_owner_call", "Pausar quando chamado", 0, 1, "checkbox"],
    ["resume_authorized_goal", "Retomar missão autorizada", 0, 1, "checkbox"],
    ["spectator_camera", "Câmera espectadora", 0, 1, "checkbox"],
  ];

  // Zonas da Conversa Autônoma (a ordem é validada no servidor: faixa <= ideal <= faixa alta <= trabalho <= margem; perseguir <= margem)
  const ZONAS_V6 = [
    ["ideal_distance", "Distância ideal (blocos)", 2, 16, "number", "Onde ela fica quando está só acompanhando você."],
    ["band_low", "Faixa de enquadramento: de (blocos)", 1, 16, "number"],
    ["band_high", "Faixa de enquadramento: até (blocos)", 2, 24, "number", "Dentro desta faixa ela NÃO se mexe: não é coleira."],
    ["work_radius", "Zona de trabalho (blocos)", 4, 24, "number", "Até onde, medido de VOCÊ, ela aceita minerar/combater uma tarefa curta."],
    ["exceptional_radius", "Margem excepcional (blocos)", 4, 24, "number", "Teto absoluto, só para terminar a ação que já começou."],
    ["lease_seconds", "Tempo máximo na margem (s)", 2, 30, "number"],
    ["chase_limit", "Não perseguir além de (blocos)", 4, 24, "number"],
    ["auto_follow_idle", "Acompanhar você quando está parada", 0, 1, "checkbox"],
    ["return_after_goal", "Voltar para perto depois da tarefa", 0, 1, "checkbox"],
    ["stop_on_intro", "Ficar parada na introdução", 0, 1, "checkbox"],
    ["pause_on_owner_call", "Parar o que faz quando você chamar", 0, 1, "checkbox"],
    ["defensive_combat", "Combate defensivo", 0, 1, "checkbox"],
  ];
  const ZONAS_PADRAO = { ideal_distance: 5, band_low: 4, band_high: 7, work_radius: 9, exceptional_radius: 12, lease_seconds: 8, chase_limit: 9 };
  const ESTADO_SERIE = {
    NO_WORLD: "sem mundo da série aberto", WORLD_LOADING: "carregando o mundo", INTRO_READY: "introdução: Célia parada, mundo congelado",
    GAMEPLAY: "jogando", TALK_PAUSE: "pausa para conversar", EPISODE_END: "episódio encerrado", BLOCKED: "BLOQUEADO (precisa de recuperação)", RECOVERY: "recuperando",
  };
  const ESTADO_COMPANHEIRA = {
    AT_OWNER_SIDE: "ao seu lado", ACTION_LEASE: "fazendo uma tarefa curta", FINISHING_ACTION: "terminando a tarefa na margem", RETURN_PENDING: "vai voltar",
    RETURNING: "voltando para perto", HOLD_SAFE: "parada com segurança", DISABLED: "parada (introdução/pausa)",
  };
  const MOTIVOS = {
    needs_confirmation: "precisa da sua confirmação", not_attested: "o pedido não veio do dono", bad_state: "o momento da gravação não permite isso",
    no_world: "ainda não há mundo da série escolhido", switch_failed: "a troca de mundo não terminou; o servidor ficou no mundo anterior",
    no_player_online: "não há ninguém conectado no jogo", tick_freeze_unsupported: "este servidor não sabe congelar o mundo",
    profile_is_not_conversation_autonomous: "ative o perfil Conversa Autônoma primeiro", jev_daemon_offline: "o executivo do jogo (Jev) está desligado",
    unlisted_intent: "ação não permitida", no_physical_ack: "o corpo não confirmou que parou", world_exists: "esse mundo já existe",
  };
  // estados em que cada botão faz sentido (o servidor valida de novo; isto só evita clique inútil)
  const BOTOES_SERIE = [
    ["series.prepare_intro", "Preparar introdução", ["NO_WORLD", "EPISODE_END", "INTRO_READY"]],
    ["series.start_gameplay", "Começar gameplay", ["INTRO_READY"]],
    ["series.talk_pause", "Pausar Célia (conversar)", ["GAMEPLAY"]],
    ["series.resume", "Voltar ao jogo", ["TALK_PAUSE"]],
    ["world.checkpoint", "Salvar mundo", ["NO_WORLD", "INTRO_READY", "GAMEPLAY", "TALK_PAUSE", "EPISODE_END", "BLOCKED", "RECOVERY"]],
    ["world.backup", "Backup do mundo", ["NO_WORLD", "INTRO_READY", "GAMEPLAY", "TALK_PAUSE", "EPISODE_END", "BLOCKED", "RECOVERY"]],
    ["series.end_episode", "Encerrar episódio", ["INTRO_READY", "GAMEPLAY", "TALK_PAUSE"]],
    ["companion.return", "Retornar ao lado", null],
    ["world.list", "Listar mundos", null],
  ];

  function elemento(tag, texto, css) {
    const el = document.createElement(tag);
    if (texto !== undefined && texto !== null) el.textContent = String(texto);
    if (css) Object.assign(el.style, css);
    return el;
  }

  // Chance de falar: NIVEL do perfil (campo `participation`), nunca uma probabilidade sorteada. As faixas sao exatamente as da politica
  // do servidor (continuous_behavior._apply_profile_settings: 0 off, <35 light, <60 medium, resto strong).
  function faixaFala(valor) {
    const n = Number(valor) || 0;
    return n <= 0 ? "Só quando chamada" : n < 35 ? "Discreta" : n < 60 ? "Moderada" : "Falante";
  }
  function faixaInterrupcao(valor) {
    const n = Number(valor) || 0;
    return n <= 0 ? "nunca" : n < 30 ? "pouco" : n < 65 ? "às vezes" : "bastante";
  }
  const resumos = {}; // id -> {participation, customized}: so o que o servidor devolveu; ausente = nao desenha

  async function carregarResumos() {
    await Promise.all(PERFIS.map(async (perfil) => {
      try {
        const r = await fetch("/bridge/behavior/profiles/" + encodeURIComponent(perfil.id), { credentials: "same-origin" });
        if (!r.ok) return;
        const d = await r.json();
        const n = Number(d && d.settings && d.settings.social && d.settings.social.participation);
        if (Number.isFinite(n)) resumos[perfil.id] = { participation: n, customized: Boolean(d.customized) };
      } catch (_) { /* sem dado: o menu nao inventa chance */ }
    }));
  }

  async function lerSessao() {
    try {
      const r = await fetch(ENDPOINT, { credentials: "same-origin" });
      if (!r.ok) return null;
      const d = await r.json();
      const m = d && d.metrics ? d.metrics : null;
      if (!m) return null;
      return { perfil: String(d.profile || ""), oportunidades: Number(m.participation_opportunities), faladas: Number(m.spontaneous_accepted) };
    } catch (_) { return null; }
  }

  async function editarPerfil(id) {
    const antigo = document.getElementById("celia-perfil-v6-editor");
    if (antigo) antigo.remove();
    const back = elemento("div", "", {
      position: "fixed", inset: "0", zIndex: "2147483646", // acima das caixas THINKING/PROCESSOS (2147483645), que cobriam Salvar
      background: "rgba(1,5,12,.75)", display: "grid", placeItems: "center",
      padding: "16px", boxSizing: "border-box",
    });
    back.id = "celia-perfil-v6-editor";
    back.setAttribute("role", "presentation");
    const painel = elemento("section", "", {
      width: "min(520px, 100%)", maxHeight: "min(740px, 90vh)",
      overflowY: "auto", border: "1px solid rgba(91,198,255,.3)",
      borderRadius: "18px", padding: "18px", boxSizing: "border-box",
      color: "#e9f5ff", background: "linear-gradient(155deg,#14263c,#080e1c)",
      boxShadow: "0 24px 70px #000c",
      font: "13px/1.5 system-ui,sans-serif",
    });
    painel.setAttribute("role", "dialog");
    painel.setAttribute("aria-modal", "true");
    painel.setAttribute("aria-label", "Configurações do perfil");
    back.append(painel);
    document.body.append(back);
    const limpezas = [];
    const fechar = () => { for (const f of limpezas) { try { f(); } catch (_) { /* nada a limpar */ } } back.remove(); };
    back.addEventListener("click", (e) => { if (e.target === back) fechar(); });
    const heading = elemento("div", "", { display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" });
    heading.append(elemento("h2", "⚙ Perfil " + (PERFIS.find(x => x.id === id)?.nome || id), { fontSize: "19px", margin: "0 0 8px" }));
    const x = elemento("button", "✕", { cursor: "pointer", background: "transparent", color: "#e9f5ff", border: "0", fontSize: "22px" });
    x.type = "button"; x.setAttribute("aria-label", "Fechar configurações"); x.addEventListener("click", fechar);
    heading.append(x); painel.append(heading);
    const sub = elemento("p", "Configura o comportamento da Célia e as políticas do Jev. Não altera permissões.", {
      color: "#a0bed6", fontSize: "12px", margin: "0 0 14px"
    });
    painel.append(sub);
    const aviso = elemento("p", "Carregando...", { color: "#85d7ff", minHeight: "18px" });
    painel.append(aviso);
    const tabs = elemento("div", "", { display: "flex", gap: "6px", marginBottom: "12px", flexWrap: "wrap" });
    const form = elemento("div");
    const foot = elemento("div", "", { display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "16px", position: "sticky", bottom: "-18px", padding: "10px 0 2px", background: "linear-gradient(180deg,#0000,#080e1c 30%)" });
    painel.append(tabs, form, foot);
    const btn = (text, fn, main = false) => {
      const b = elemento("button", text, {
        padding: "9px 12px", border: main ? "1px solid #50ceff" : "1px solid #57728d",
        borderRadius: "9px", background: main ? "#086499" : "#223751", color: "#fff",
        fontWeight: "650", cursor: "pointer",
      });
      b.type = "button"; b.addEventListener("click", fn); return b;
    };
    let draft, revision = 0, tab = "social";
    const notify = (msg, bad = false) => { aviso.textContent = msg; aviso.style.color = bad ? "#ff9da2" : "#85d7ff"; };
    let custom = false;
    const refresca = () => { if (typeof atualizarCartao === "function") atualizarCartao(); };
    const field = (holder, object, [key, name, min, max, type, dica]) => {
      const label = elemento("label", "", {
        display: "flex", gap: "10px", justifyContent: "space-between", alignItems: "center",
        padding: "8px", borderBottom: "1px solid #ffffff15",
      });
      const nameEl = elemento("span", "", { flex: "1", minWidth: "0" });
      const titulo = elemento("span", name); nameEl.append(titulo);
      if (dica) nameEl.append(elemento("small", dica, { display: "block", color: "#8fb0c9", fontSize: "11px", fontWeight: "400" }));
      const input = document.createElement("input");
      input.style.cssText = "width:110px;max-width:45%;padding:5px;border-radius:6px;background:#0c182c;color:#fff;border:1px solid #47607b;";
      if (type === "checkbox") {
        input.type = "checkbox"; input.checked = Boolean(object[key]); input.style.width = "19px";
        input.addEventListener("change", () => { object[key] = input.checked; notify("Alterações não salvas"); refresca(); });
      } else {
        input.type = type === "range" ? "range" : "number";
        input.min = String(min); input.max = String(max); input.step = "1";
        input.value = object[key] == null ? "" : String(object[key]);
        if (type === "nullable") {
          input.placeholder = "Sem limite";
        }
        // Fora do limite o servidor recusa o PUT inteiro (HTTP 422). Marca em vermelho ao digitar e corrige para o limite ao sair do campo.
        const foraDoLimite = () => input.type === "number" && input.value !== "" && (Number(input.value) < Number(min) || Number(input.value) > Number(max));
        input.addEventListener("change", () => {
          if (input.type !== "number" || input.value === "") return;
          const preso = Math.min(Number(max), Math.max(Number(min), Math.round(Number(input.value))));
          if (preso !== Number(input.value)) { input.value = String(preso); object[key] = preso; notify("Valor ajustado ao limite " + min + "–" + max + ": " + name); refresca(); }
          input.style.borderColor = "#47607b";
        });
        input.addEventListener("input", () => {
          input.style.borderColor = foraDoLimite() ? "#ff5c6c" : "#47607b";
          object[key] = type === "nullable" && input.value === "" ? null : Number(input.value);
          notify(foraDoLimite() ? "Fora do limite " + min + "–" + max + ": " + name : "Alterações não salvas");
          if (type === "range") titulo.textContent = name + " — " + object[key] + "%";
          refresca();
        });
        if (type === "range") titulo.textContent += " — " + object[key] + "%";
      }
      label.append(nameEl,input); holder.append(label);
    };

    // Cartao "Chance de falar": montado uma vez; so o texto/enchimento mudam, para o slider nao ser recriado no meio do arrasto.
    const cartao = elemento("div", "", { margin: "0 0 10px", padding: "9px 12px", borderRadius: "12px", background: "#0b1a2e", border: "1px solid #3d6a91" });
    cartao.id = "celia-perfil-chance";
    painel.insertBefore(cartao, tabs);
    const ref = {};
    let sessao = null;
    function montarCartao() {
      cartao.replaceChildren();
      const topo = elemento("div", "", { display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px" });
      ref.valor = elemento("span", "", { fontSize: "20px", fontWeight: "750", color: "#fff" });
      topo.append(elemento("strong", "Chance de falar", { fontSize: "14px", color: "#b5dbff" }), ref.valor);
      const trilha = elemento("div", "", { position: "relative", height: "12px", borderRadius: "8px", background: "#0c182c", border: "1px solid #47607b", margin: "6px 0 2px", overflow: "hidden" });
      trilha.setAttribute("role", "meter"); trilha.setAttribute("aria-label", "Chance de falar do perfil");
      trilha.setAttribute("aria-valuemin", "0"); trilha.setAttribute("aria-valuemax", "100");
      ref.trilha = trilha;
      ref.barra = elemento("div", "", { height: "100%", background: "linear-gradient(90deg,#2b7fb8,#50ceff)", transition: "width .12s" });
      trilha.append(ref.barra);
      for (const marca of [35, 60]) trilha.append(elemento("i", "", { position: "absolute", top: "0", bottom: "0", left: marca + "%", width: "1px", background: "#ffffff55" }));
      const faixas = elemento("div", "", { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", fontSize: "11px", color: "#8fb0c9", margin: "0 0 2px" });
      ref.faixas = [["Discreta"], ["Moderada"], ["Falante"]].map(([t]) => faixas.appendChild(elemento("span", t, { textAlign: "center" })));
      ref.slider = document.createElement("input");
      ref.slider.type = "range"; ref.slider.min = "0"; ref.slider.max = "100"; ref.slider.step = "1";
      ref.slider.setAttribute("aria-label", "Quanto ela fala por conta própria");
      ref.slider.style.cssText = "width:100%;";
      ref.slider.addEventListener("input", () => { draft.social.participation = Number(ref.slider.value); notify("Alterações não salvas"); atualizarCartao(); });
      ref.slider.title = "Arraste para ela falar menos ou mais por conta própria. 0 = só responde quando chamada.";
      ref.modo = elemento("p", "", { margin: "4px 0 2px", fontSize: "11px", color: "#8fb0c9" });
      ref.lista = elemento("ul", "", { margin: "0", padding: "0 0 0 16px", listStyle: "disc", fontSize: "12px", color: "#cfe7fb", lineHeight: "1.5" });
      ref.sessao = elemento("p", "", { margin: "4px 0 0", fontSize: "11px", color: "#8fb0c9" });
      cartao.append(topo, trilha, faixas, ref.slider, ref.modo, ref.lista, ref.sessao);
    }
    function atualizarCartao() {
      if (!draft || !ref.valor) return;
      const so = draft.social, p = Number(so.participation) || 0;
      ref.valor.textContent = p + "% · " + faixaFala(p);
      ref.barra.style.width = Math.max(0, Math.min(100, p)) + "%";
      ref.trilha.setAttribute("aria-valuenow", String(p));
      const ativa = p <= 0 ? -1 : p < 35 ? 0 : p < 60 ? 1 : 2;
      ref.faixas.forEach((el, k) => { el.style.color = k === ativa ? "#fff" : "#8fb0c9"; el.style.fontWeight = k === ativa ? "700" : "400"; });
      if (Number(ref.slider.value) !== p) ref.slider.value = String(p);
      ref.modo.textContent = custom
        ? "Valendo agora: valores personalizados deste perfil."
        : "Padrão do perfil: os limites abaixo só valem depois de Salvar.";
      const m10 = Number(so.max_spontaneous_per_10m), mh = Number(so.inference_budget_per_hour);
      const continuo = Object.prototype.hasOwnProperty.call(CONTINUOS, id);
      const mmin = continuo ? Number(so.max_spontaneous_per_minute == null ? CONTINUOS[id] : so.max_spontaneous_per_minute) : null;
      const zero = continuo ? mmin === 0 : m10 === 0;
      const linhas = [
        zero ? "Nunca fala por conta própria." : "Puxa assunto depois de " + so.silence_seconds + " s de silêncio.",
        zero ? "Só responde quando falam com ela."
          : continuo ? "Fala continuamente: no máximo " + mmin + " falas por minuto, com pelo menos " + so.spontaneous_cooldown_seconds + " s entre uma e outra."
          : "No máximo " + m10 + " falas por conta própria a cada 10 min (e " + mh + " por hora).",
        "Fala por cima de você: " + faixaInterrupcao(so.interruption) + ".",
      ];
      ref.lista.replaceChildren(...linhas.map((t) => elemento("li", t)));
      if (!sessao) ref.sessao.textContent = "Medido nesta sessão: sem leitura do servidor.";
      else if (sessao.perfil !== id) ref.sessao.textContent = "Medido nesta sessão: ative este perfil para medir (o contador é do perfil ativo).";
      else ref.sessao.textContent = "Medido nesta sessão: " + (Number.isFinite(sessao.oportunidades) ? sessao.oportunidades : "?") + " oportunidades registradas, " + (Number.isFinite(sessao.faladas) ? sessao.faladas : "?") + " falas por conta própria.";
    }
    lerSessao().then((v) => { sessao = v; atualizarCartao(); });

    // ---- aba Série: status REAL (nada inventado) e botoes de fallback que usam o mesmo portao e o mesmo envio tipado da voz ----
    const serie = { parar: null };
    function montarSerie() {
      const caixa = elemento("div", "", { display: "grid", gap: "10px" });
      const faixa = elemento("div", "", { padding: "8px 10px", borderRadius: "10px", background: "#0b1a2e", border: "1px solid #3d6a91", fontSize: "12px", lineHeight: "1.55" });
      faixa.id = "celia-serie-status";
      const aviso2 = elemento("p", "", { margin: "0", fontSize: "12px", color: "#ffd48a", minHeight: "16px" });
      aviso2.id = "celia-serie-aviso";
      const botoes = elemento("div", "", { display: "flex", flexWrap: "wrap", gap: "6px" });
      const lista = elemento("div", "", { fontSize: "12px", color: "#cfe7fb" });
      lista.id = "celia-serie-mundos";
      const confirmacao = elemento("div", "", { display: "none", padding: "8px 10px", borderRadius: "10px", border: "1px solid #ffb04a", background: "#2a1d08", fontSize: "12px" });
      confirmacao.id = "celia-serie-confirmacao";
      const criar = elemento("details", "", { fontSize: "12px" });
      criar.append(elemento("summary", "Criar um mundo novo para a série", { cursor: "pointer", color: "#85d7ff", padding: "4px 0" }));
      const nomeIn = document.createElement("input"); nomeIn.placeholder = "Nome (ex.: Célia e Criador — Série 02)"; nomeIn.setAttribute("aria-label", "Nome do mundo");
      const serieIn = document.createElement("input"); serieIn.placeholder = "s02"; serieIn.setAttribute("aria-label", "Número da série"); serieIn.style.width = "70px";
      for (const el of [nomeIn, serieIn]) el.style.cssText += ";padding:5px;border-radius:6px;background:#0c182c;color:#fff;border:1px solid #47607b;margin-right:6px;";
      criar.append(nomeIn, serieIn);
      const painel = { estado: "NO_WORLD", perfil: null, ocupado: false, ok: false };
      const botaoPorIntent = {};
      const rodar = async (intent, params, confirmado) => {
        painel.ocupado = true; pintarBotoes();
        aviso2.style.color = "#85d7ff"; aviso2.textContent = "Executando… (abrir ou trocar de mundo reinicia o servidor do Minecraft e leva cerca de um minuto)";
        try {
          const r = await fetch("/bridge/minecraft/series/command", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ intent, params: params || {}, confirmed: Boolean(confirmado) }) });
          if (!r.ok) throw new Error("HTTP " + r.status);
          const d = await r.json();
          if (d.needs_confirmation) {
            const trocar = d.plan && d.plan.needs === "confirm_switch";
            confirmacao.style.display = "block"; confirmacao.replaceChildren();
            confirmacao.append(elemento("p", (trocar ? "Isto reinicia o servidor do Minecraft e desconecta quem estiver jogando. " : "Isto cria um mundo novo. ") + "Confirmar?", { margin: "0 0 6px" }));
            const sim = elemento("button", "Confirmar", { padding: "6px 10px", borderRadius: "8px", border: "1px solid #ffb04a", background: "#6b4410", color: "#fff", cursor: "pointer", marginRight: "6px" });
            sim.type = "button"; sim.addEventListener("click", () => { confirmacao.style.display = "none"; rodar(intent, params, true); });
            const nao = elemento("button", "Cancelar", { padding: "6px 10px", borderRadius: "8px", border: "1px solid #57728d", background: "#223751", color: "#fff", cursor: "pointer" });
            nao.type = "button"; nao.addEventListener("click", () => { confirmacao.style.display = "none"; aviso2.textContent = "Cancelado. Nada foi feito."; });
            confirmacao.append(sim, nao);
            aviso2.textContent = "";
          } else if (d.ok) {
            aviso2.style.color = "#8be9a8"; aviso2.textContent = "Feito.";
            if (intent === "world.list") atualizarMundos(d.worlds);
          } else {
            aviso2.style.color = "#ff9da2"; aviso2.textContent = "Não foi possível: " + (MOTIVOS[d.code] || (d.code ? String(d.code).replace(/_/g, " ") : "motivo não informado")) + ".";
          }
        } catch (e) {
          aviso2.style.color = "#ff9da2"; aviso2.textContent = "Sem resposta da central (reinicie-a para carregar as rotas novas): " + e.message;
        } finally { painel.ocupado = false; await ler(); }
      };
      const pintarBotoes = () => {
        for (const [intent, , estados] of BOTOES_SERIE) {
          const b = botaoPorIntent[intent]; if (!b) continue;
          const naoSerie = painel.perfil !== SERIE_ID && !["companion.return", "world.list"].includes(intent);
          const foraDeEstado = estados && !estados.includes(painel.estado);
          b.disabled = !painel.ok || painel.ocupado || naoSerie || Boolean(foraDeEstado);
          b.style.opacity = b.disabled ? ".45" : "1"; b.style.cursor = b.disabled ? "not-allowed" : "pointer";
          b.title = !painel.ok ? "O executivo do jogo (Jev) está desligado" : naoSerie ? "Ative o perfil Conversa Autônoma" : foraDeEstado ? "Não faz sentido neste momento da gravação" : "";
        }
        parada.disabled = !painel.ok; parada.style.opacity = painel.ok ? "1" : ".45";
        criarBtn.disabled = !painel.ok || painel.ocupado || painel.perfil !== SERIE_ID || !["NO_WORLD", "EPISODE_END"].includes(painel.estado);
        criarBtn.style.opacity = criarBtn.disabled ? ".45" : "1";
      };
      for (const [intent, rotulo] of BOTOES_SERIE) {
        const b = elemento("button", rotulo, { padding: "7px 10px", borderRadius: "9px", border: "1px solid #57728d", background: "#223751", color: "#fff", fontWeight: "650" });
        b.type = "button"; b.dataset.intent = intent; b.addEventListener("click", () => rodar(intent, {}, false));
        botaoPorIntent[intent] = b; botoes.append(b);
      }
      const parada = elemento("button", "Parada de emergência", { padding: "7px 10px", borderRadius: "9px", border: "1px solid #ff6b6b", background: "#5a1620", color: "#fff", fontWeight: "700", cursor: "pointer" });
      parada.type = "button"; parada.dataset.intent = "control.emergency_stop"; parada.addEventListener("click", () => rodar("control.emergency_stop", {}, false));
      parada.style.width = "100%"; parada.style.padding = "9px 10px";  // always the first thing in the tab: never hidden below the fold or under the sticky footer
      const criarBtn = elemento("button", "Criar mundo", { padding: "6px 10px", borderRadius: "8px", border: "1px solid #57728d", background: "#223751", color: "#fff", cursor: "pointer" });
      criarBtn.type = "button"; criarBtn.dataset.intent = "world.create";
      criarBtn.addEventListener("click", () => rodar("world.create", { name: nomeIn.value.trim() || "Célia e Criador", series: serieIn.value.trim().toLowerCase() }, false));
      criar.append(criarBtn);
      const atualizarMundos = (mundos) => {
        lista.replaceChildren();
        const gerenciados = (mundos || []).filter((m) => m.managed), externos = (mundos || []).filter((m) => !m.managed);
        lista.append(elemento("strong", "Mundos da série", { color: "#b5dbff" }));
        if (!gerenciados.length) lista.append(elemento("div", "Nenhum criado ainda."));
        for (const m of gerenciados) lista.append(elemento("div", (m.active ? "▶ " : "• ") + (m.display_name || m.world_id) + " (" + m.world_id + ")" + (m.active ? " — rodando no servidor" : "") + (m.exists === false ? " — pasta ausente!" : "")));
        if (externos.length) {
          const det = elemento("details", "", { marginTop: "4px" });
          det.append(elemento("summary", externos.length + " mundo(s) antigo(s) (não gerenciados, nunca migrados)", { cursor: "pointer", color: "#8fb0c9" }));
          for (const m of externos) det.append(elemento("div", (m.active ? "▶ " : "• ") + m.world_id, { color: "#8fb0c9" }));
          lista.append(det);
        }
      };
      const ler = async () => {
        try {
          const r = await fetch("/bridge/minecraft/series", { credentials: "same-origin", cache: "no-store" });
          if (!r.ok) throw new Error("HTTP " + r.status);
          const d = await r.json();
          painel.ok = Boolean(d.ok); painel.perfil = d.profile || null;
          faixa.replaceChildren();
          if (!d.ok) {
            painel.estado = "NO_WORLD";
            faixa.append(elemento("div", "Executivo do jogo (Jev) desligado: sem dados. Nada aqui é inventado.", { color: "#ff9da2" }));
          } else {
            const st = d.status || {}, comp = st.companion || {};
            painel.estado = st.state || "NO_WORLD";
            const linhas = [
              ["Gravação", (ESTADO_SERIE[st.state] || st.state || "?") + (st.episode ? " · episódio " + st.episode : "")],
              ["Mundo da série", st.world_id || "(nenhum)"],
              ["Servidor roda", d.active_world_id || "(desconhecido)"],
              ["Mundo congelado (tick freeze)", st.tick_state && st.tick_state.frozen === true ? "sim, confirmado" : st.tick_state && st.tick_state.frozen === false ? "não, rodando" : "n/d"],
              ["Ciclo do dia", st.daylight_state && st.daylight_state.cycle === false ? "parado" : st.daylight_state && st.daylight_state.cycle === true ? "correndo" : "n/d"],
              ["Célia", (ESTADO_COMPANHEIRA[comp.state] || comp.state || "?") + (comp.distance != null ? " · " + comp.distance + " blocos de você" : "")
                + (comp.lease ? " · tarefa " + comp.lease.kind + " (" + comp.lease.age + " s)" : "")],
              ["Perfil ativo", d.profile === SERIE_ID ? "Conversa Autônoma" : String(d.profile || "?")],
            ];
            if (st.blocked_reason) linhas.push(["BLOQUEADO", st.blocked_reason]);
            if ((st.degraded || []).length) linhas.push(["Degradado", st.degraded.join(", ")]);
            for (const [k, v] of linhas) { const l = elemento("div"); l.append(elemento("span", k + ": ", { color: "#8fb0c9" }), elemento("span", v, { color: k === "BLOQUEADO" ? "#ff9da2" : "#fff" })); faixa.append(l); }
            if (d.worlds) atualizarMundos(d.worlds);
          }
        } catch (e) {
          painel.ok = false; faixa.replaceChildren(elemento("div", "Sem resposta da central: " + e.message, { color: "#ff9da2" }));
        }
        pintarBotoes();
      };
      caixa.append(parada, faixa, aviso2, botoes, confirmacao, criar, lista);
      form.append(caixa);
      serie.parar = () => { if (serie.timer) { clearInterval(serie.timer); serie.timer = null; } };
      serie.parar();
      ler(); serie.timer = setInterval(ler, 1500);
      limpezas.push(serie.parar);
    }

    function render() {
      if (!draft) return;
      form.replaceChildren(); tabs.replaceChildren();
      const abas = [["social", "Essencial"], ["triggers", "Gatilhos"], ["gameplay", "Minecraft"]];
      if (id === SERIE_ID) abas.push(["serie", "Série"]);
      if (tab !== "serie" && serie.parar) serie.parar();
      for (const [name, title] of abas) {
        const b = btn(title, () => { tab = name; render(); });
        if (tab === name) b.style.background = "#086499";
        tabs.append(b);
      }
      if (!ref.slider) montarCartao();
      atualizarCartao();
      cartao.style.display = tab === "serie" ? "none" : "";  // the Série tab needs the room; the chance card stays on every other tab
      if (tab === "social") {
        const social = draft.social;
        field(form, social, ["interruption", "Permissão para interromper", 0, 100, "range", "Quanto ela se arrisca a falar por cima de você. 0 = nunca."]);
        field(form, social, ["silence_seconds", "Reação ao silêncio (s)", 2, 120, "number", "Segundos de silêncio (de VOCÊS DOIS) antes de ela poder puxar assunto."]);
        const continuo = Object.prototype.hasOwnProperty.call(CONTINUOS, id);
        if (continuo) {
          if (social.max_spontaneous_per_minute == null) social.max_spontaneous_per_minute = CONTINUOS[id];
          field(form, social, ["max_spontaneous_per_minute", "Falas por conta própria / minuto", 0, 30, "number", "Cota por MINUTO: ela segue falando sozinha até este limite. 0 = nunca fala sozinha."]);
          field(form, social, ["spontaneous_cooldown_seconds", "Intervalo entre falas espontâneas (s)", 0, 600, "number", "Tempo mínimo entre duas falas que ela puxa sozinha."]);
        }
        if (id === SERIE_ID) field(form, social, ["barge_in", "Calar quando você começar a falar", 0, 1, "checkbox"]);
        if (!continuo) field(form, social, ["max_spontaneous_per_10m", "Falas espontâneas / 10 min", 0, 60, "number", "Teto de falas por conta própria. 0 = nunca fala sozinha."]);
        for (const entry of [
          ["screen_awareness", "Observar tela", 0, 1, "checkbox"],
          ["script_tracking", "Acompanhar roteiro", 0, 1, "checkbox"],
          ["audience_chat", "Participar do chat", 0, 1, "checkbox"],
          ["respect_concentration", "Respeitar concentração", 0, 1, "checkbox"],
        ]) field(form, social, entry);
        const mais = elemento("details", "", { marginTop: "8px" });
        mais.append(elemento("summary", "Mais opções", { cursor: "pointer", color: "#85d7ff", padding: "6px 8px" }));
        field(mais, social, ["response_words", "Palavras por resposta", 5, 300, "number"]);
        if (!continuo) field(mais, social, ["inference_budget_per_hour", "Limite proativo / hora", 0, 240, "number", "Teto por hora de falas por conta própria."]);
        form.append(mais);
      } else if (tab === "gameplay" && id === SERIE_ID) {
        form.append(elemento("p", "Ela acompanha você de longe sem coleira: dentro da faixa não se mexe; uma tarefa curta pode ir até a zona de trabalho e, só para terminar a ação em curso, até a margem excepcional por pouco tempo. Depois volta sozinha.", { color: "#a0bed6", fontSize: "11px", margin: "0 0 8px" }));
        for (const [chave, padrao] of Object.entries(ZONAS_PADRAO)) if (draft.gameplay[chave] == null) draft.gameplay[chave] = padrao;
        for (const entry of ZONAS_V6) field(form, draft.gameplay, entry);
        form.append(elemento("p", "Ordem exigida: faixa de baixo ≤ ideal ≤ faixa de cima ≤ trabalho ≤ margem; perseguir ≤ margem. Fora disso o servidor recusa ao salvar.", { color: "#8fb0c9", fontSize: "11px" }));
      } else if (tab === "serie" && id === SERIE_ID) {
        montarSerie();
      } else if (tab === "gameplay") {
        const wrap = elemento("label", "Modo de movimento", { display:"flex",justifyContent:"space-between", alignItems:"center",padding:"8px" });
        const select = elemento("select", "", { background: "#0c182c", color: "#fff", padding: "7px", borderRadius: "7px" });
        for (const [value, text] of [["follow","Acompanhar"],["nearby","Permanecer perto"],["free","Exploração livre"]]) {
          const opt = elemento("option", text); opt.value = value; select.append(opt);
        }
        select.value = draft.gameplay.mode;
        select.onchange = () => {
          draft.gameplay.mode = select.value;
          if (select.value === "free") { draft.gameplay.ideal_distance = null; draft.gameplay.max_distance = null; }
          else if (draft.gameplay.ideal_distance == null) { draft.gameplay.ideal_distance = 8; draft.gameplay.max_distance = 24; }
          notify("Alterações não salvas"); render();
        };
        wrap.append(select); form.append(wrap);
        for (const entry of GAME_V6) field(form, draft.gameplay, entry);
        form.append(elemento("p", "Ativar câmera requer autorização do criador e confirmação no Minecraft. Salvar preferências não muda o gamemode por si só.", {color:"#a0bed6",fontSize:"11px"}));
      } else {
        // Uma linha por gatilho (antes: 3 linhas empilhadas cada). Mesmos tres campos, mesma gravacao.
        form.append(elemento("p", "Eventos que podem fazer ela falar. Desmarque para ignorar o evento; prioridade maior fala mais cedo; intervalo é o mínimo entre duas falas do mesmo gatilho.", { color: "#8fb0c9", fontSize: "11px", margin: "0 0 8px" }));
        const colunas = "22px minmax(0,1fr) 120px 64px";
        const cab = elemento("div", "", { display: "grid", gridTemplateColumns: colunas, gap: "8px", padding: "0 6px 4px", fontSize: "10px", letterSpacing: ".06em", color: "#8fb0c9", textTransform: "uppercase" });
        cab.append(elemento("span", ""), elemento("span", "Gatilho"), elemento("span", "Prioridade"), elemento("span", "Intervalo (s)"));
        form.append(cab);
        for (const [key, title] of Object.entries(TRIGGERS_V6)) {
          const obj = draft.triggers[key] || (draft.triggers[key] = {enabled:true,priority:50,cooldown_seconds:25});
          const linha = elemento("div", "", { display: "grid", gridTemplateColumns: colunas, gap: "8px", alignItems: "center", padding: "6px", borderBottom: "1px solid #ffffff15" });
          linha.dataset.gatilho = key;
          const on = document.createElement("input"); on.type = "checkbox"; on.checked = Boolean(obj.enabled);
          on.setAttribute("aria-label", title + " ativado");
          const nome = elemento("span", title, { fontWeight: "600" });
          const pri = document.createElement("input"); pri.type = "range"; pri.min = "0"; pri.max = "100"; pri.step = "1"; pri.value = String(obj.priority);
          pri.setAttribute("aria-label", title + " prioridade"); pri.title = obj.priority + "%"; pri.style.width = "100%";
          const cd = document.createElement("input"); cd.type = "number"; cd.min = "0"; cd.max = "3600"; cd.step = "1"; cd.value = String(obj.cooldown_seconds);
          cd.setAttribute("aria-label", title + " intervalo em segundos");
          cd.style.cssText = "width:100%;padding:4px;border-radius:6px;background:#0c182c;color:#fff;border:1px solid #47607b;box-sizing:border-box;";
          const dim = () => { linha.style.opacity = obj.enabled ? "1" : ".5"; };
          on.addEventListener("change", () => { obj.enabled = on.checked; dim(); notify("Alterações não salvas"); });
          pri.addEventListener("input", () => { obj.priority = Number(pri.value); pri.title = obj.priority + "%"; notify("Alterações não salvas"); });
          cd.addEventListener("input", () => { obj.cooldown_seconds = Number(cd.value); notify("Alterações não salvas"); });
          dim();
          linha.append(on, nome, pri, cd); form.append(linha);
        }
      }
    }
    async function carregar() {
      const response = await fetch("/bridge/behavior/profiles/" + encodeURIComponent(id), { credentials: "same-origin" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const data = await response.json();
      draft = structuredClone(data.settings); revision = data.revision; custom = Boolean(data.customized);
      notify(data.customized ? "Configuração personalizada carregada." : "Padrão carregado."); render();
    }
    foot.append(btn("Salvar", async () => {
      try {
        const r = await fetch("/bridge/behavior/profiles/" + encodeURIComponent(id), {
          method:"PUT", credentials:"same-origin",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({settings:draft,expected_revision:revision})
        });
        if (r.status === 409) throw new Error("este perfil foi alterado em outra janela. Feche o editor, abra de novo e refaça o ajuste.");
        if (!r.ok) throw new Error("HTTP " + r.status + " — " + (await r.text()).slice(0,120));
        revision = (await r.json()).revision; custom = true;
        resumos[id] = { participation: Number(draft.social.participation), customized: true }; atualizarCartao();
        notify("Salvo. As preferências do Minecraft serão aplicadas quando a integração estiver disponível.");
      } catch (e) {notify("Falha ao salvar: " + e.message,true);}
    },true),btn("Cancelar",fechar),btn("Restaurar padrão",async () => {
      try {
        if (!window.confirm("Restaurar os padrões deste perfil?")) return;
        const r = await fetch("/bridge/behavior/profiles/"+encodeURIComponent(id)+"/reset",{method:"POST",credentials:"same-origin"});
        if (!r.ok) throw new Error("HTTP "+r.status);
        await carregar();notify("Padrões restaurados.");
      } catch(e) {notify("Falha ao restaurar: "+e.message,true);}
    }));
    const copy = elemento("select", "", { padding: "9px", borderRadius: "8px", background: "#223751", color: "#fff" });
    const placeholder = elemento("option", "Copiar ajustes de..."); placeholder.value = ""; copy.append(placeholder);
    for (const profile of PERFIS.filter(p => p.id !== id)) {
      const op=elemento("option",profile.nome); op.value=profile.id; copy.append(op);
    }
    copy.onchange = async () => {
      if (!copy.value) return;
      try {
        const r=await fetch("/bridge/behavior/profiles/"+encodeURIComponent(copy.value),{credentials:"same-origin"});
        if (!r.ok) throw new Error("HTTP "+r.status);
        draft=structuredClone((await r.json()).settings);render();notify("Ajustes copiados; clique Salvar para aplicar.");
      } catch(e) {notify("Erro ao copiar: "+e.message,true);}
      copy.value="";
    };
    foot.append(copy);
    try { await carregar(); } catch(e) { notify("Não foi possível abrir perfil: " + e.message,true); }
    x.focus();
  }

  function desenharMenu() {
    const menu = document.getElementById(MENU_ID);
    if (!menu) return;
    menu.replaceChildren();

    const titulo = document.createElement("p");
    titulo.textContent = "PERFIL DA CELIA";
    Object.assign(titulo.style, {
      margin: "0 0 8px", padding: "0 10px", fontSize: "10px", letterSpacing: ".16em",
      color: "rgba(255,255,255,.45)", fontWeight: "700",
    });
    menu.appendChild(titulo);

    for (const perfil of PERFIS) {
      const travado = TRAVADOS.has(perfil.id);
      const ativo = perfilAtual === perfil.id;
      const item = document.createElement("button");
      item.type = "button";
      item.dataset.perfil = perfil.id;
      item.dataset.travado = travado ? "true" : "false";
      item.setAttribute("role", "menuitemradio");
      item.setAttribute("aria-checked", ativo ? "true" : "false");
      // Travado nao e so estilo: o botao fica desabilitado de verdade.
      if (travado) {
        item.disabled = true;
        item.setAttribute("aria-disabled", "true");
        item.title = "Modo Autônomo — ainda está em desenvolvimento";
      }
      Object.assign(item.style, {
        display: "grid", gridTemplateColumns: "16px 1fr auto", alignItems: "center",
        gap: "9px", width: "100%", padding: "8px 10px", border: "0", borderRadius: "8px",
        background: ativo ? "rgba(126,214,255,.14)" : "transparent",
        color: travado ? "rgba(255,255,255,.34)" : "#f2f6fa",
        font: "600 13px/1.25 system-ui, sans-serif", textAlign: "left",
        cursor: travado ? "not-allowed" : "pointer",
      });

      const marca = document.createElement("span");
      marca.textContent = ativo ? "✓" : "";
      marca.style.cssText = "color:#7ed6ff;font-weight:800;";

      const texto = document.createElement("span");
      const nome = document.createElement("span");
      nome.textContent = perfil.nome;
      nome.style.cssText = "display:block;";
      const detalhe = document.createElement("small");
      detalhe.textContent = perfil.detalhe;
      detalhe.style.cssText =
        "display:block;font-weight:400;font-size:11px;color:rgba(255,255,255,.4);margin-top:1px;";
      texto.append(nome, detalhe);
      const resumo = resumos[perfil.id];
      if (resumo) {
        const chance = document.createElement("small");
        chance.dataset.chance = perfil.id;
        chance.style.cssText = "display:flex;align-items:center;gap:6px;margin-top:4px;font-weight:600;font-size:11px;color:#9fd8ff;";
        const trilha = document.createElement("span");
        trilha.style.cssText = "flex:0 0 64px;height:5px;border-radius:4px;background:rgba(255,255,255,.14);overflow:hidden;";
        const enche = document.createElement("span");
        enche.style.cssText = "display:block;height:100%;background:#50ceff;width:" + Math.max(0, Math.min(100, resumo.participation)) + "%;";
        trilha.append(enche);
        const rotulo = document.createElement("span");
        rotulo.textContent = "Chance de falar: " + faixaFala(resumo.participation) + " · " + resumo.participation + "%" + (resumo.customized ? "" : " (padrão)");
        chance.append(trilha, rotulo);
        texto.append(chance);
      }
      if (travado && perfil.status) {
        const status = document.createElement("small");
        status.textContent = perfil.status;
        status.style.cssText =
          "display:block;margin-top:3px;font-size:9px;letter-spacing:.08em;color:rgba(255,205,105,.72);font-weight:800;";
        texto.append(status);
      }

      const cadeado = document.createElement("span");
      cadeado.textContent = travado ? "🔒" : "";
      cadeado.style.cssText = "font-size:12px;opacity:.7;";

      item.append(marca, texto, cadeado);
      if (!travado) {
        item.addEventListener("click", async () => {
          const trocou = await trocarPerfil(perfil.id);
          if (trocou) fecharMenu();
        });
      } else {
        // Clique num travado nao seleciona; so explica.
        item.addEventListener("click", (evento) => {
          evento.preventDefault();
          evento.stopPropagation();
        });
      }
      const linha = elemento("div", "", {display:"flex",alignItems:"center",gap:"3px",padding:"0 4px"});
      item.style.flex = "1";
      const engrenagem = elemento("button", "⚙", {
        flex:"0 0 30px", height:"30px", borderRadius:"8px", cursor:"pointer",
        border:"1px solid #ffffff25", background:"#25435c", color:"#cbeeff", fontSize:"16px",
      });
      engrenagem.type = "button";
      engrenagem.title = "Configurar " + perfil.nome;
      engrenagem.setAttribute("aria-label","Configurar " + perfil.nome);
      engrenagem.addEventListener("click", (e) => {
        e.stopPropagation();
        fecharMenu();
        editarPerfil(perfil.id);
      });
      linha.append(item, engrenagem);
      menu.appendChild(linha);
    }

    const rodape = document.createElement("p");
    rodape.textContent = "Clique no nome para ativar. A engrenagem abre a chance de falar, os limites e o Minecraft deste perfil.";
    Object.assign(rodape.style, {
      margin: "8px 0 0", padding: "8px 10px 0", fontSize: "10.5px", lineHeight: "1.35",
      color: "rgba(255,255,255,.34)", borderTop: "1px solid rgba(255,255,255,.09)",
    });
    menu.appendChild(rodape);
  }

  async function abrirMenu(botao) {
    if (document.getElementById(MENU_ID)) return fecharMenu();
    const menu = document.createElement("div");
    menu.id = MENU_ID;
    menu.setAttribute("role", "menu");
    const caixa = botao.getBoundingClientRect();
    Object.assign(menu.style, {
      position: "fixed", left: Math.round(caixa.left) + "px",
      top: Math.round(caixa.bottom + 6) + "px", zIndex: "2147483004",
      minWidth: "268px", padding: "10px 4px 10px", borderRadius: "12px",
      border: "1px solid rgba(255,255,255,.14)", background: "rgba(14,18,24,.97)",
      boxShadow: "0 18px 44px rgba(0,0,0,.55)", backdropFilter: "blur(10px)",
    });
    document.body.appendChild(menu);
    botao.setAttribute("aria-expanded", "true");
    desenharMenu();
    document.addEventListener("pointerdown", cliqueFora, true);
    document.addEventListener("keydown", teclaEscape, true);
    await Promise.all([lerPerfil(), carregarResumos()]);
    desenharMenu();
  }

  function instalar() {
    if (document.getElementById(BOTAO_ID)) return;
    const modelo = document.querySelector('button[aria-label="Mode Menu"]');
    if (!modelo || !modelo.parentElement) return;

    const botao = document.createElement("button");
    botao.id = BOTAO_ID;
    botao.type = "button";
    botao.className = modelo.className;
    botao.setAttribute("aria-label", "Perfil da Celia");
    botao.setAttribute("aria-haspopup", "menu");
    botao.setAttribute("aria-expanded", "false");
    botao.title = "Perfil da Celia";
    botao.appendChild(icone());
    botao.addEventListener("click", (evento) => {
      evento.preventDefault();
      evento.stopPropagation();
      abrirMenu(botao);
    });
    // 6o encaixe: exatamente onde a seta da imagem do Pai aponta.
    modelo.insertAdjacentElement("afterend", botao);
    lerPerfil();
  }

  window.celiaPerfis = {
    listar: () => PERFIS.map((p) => ({ ...p, travado: TRAVADOS.has(p.id) })),
    atual: () => perfilAtual,
    ler: lerPerfil,
    trocar: trocarPerfil,
    travados: () => [...TRAVADOS],
  };

  new MutationObserver(instalar).observe(document.documentElement, { childList: true, subtree: true });
  if (document.body) instalar();
  else document.addEventListener("DOMContentLoaded", instalar, { once: true });
}());
