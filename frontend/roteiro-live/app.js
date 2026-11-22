(() => {
  const $ = (id) => document.getElementById(id);
  const connection = $("connection");
  const advancedControls = $("advanced-controls");
  const menuToggle = $("menu-toggle");
  let socket;
  let reconnectTimer;
  let reconnectAttempt = 0;
  let connecting = false;
  let leaving = false;
  // Só de tela: o dono saiu do teleprompter para olhar a lista. O servidor
  // continua com o roteiro ativo e o take de pé. Declarado aqui, antes de
  // render(), porque `let` tem zona morta temporal.
  let mostrandoLista = false;
  // Qual linha EU mandei tocar. O avanço automático só acontece para ela.
  //
  // Sem isto, qualquer transição "falando -> idle" avançaria o roteiro — e
  // voltar para uma fala da Célia já concluída avançaria sozinho, sem ninguém
  // pedir. O alvo é limpo em toda navegação manual.
  let falaPedida = null;
  // Estado anterior da Célia, para detectar o FIM do áudio (a borda), e não o
  // estado parado. Reagir a "idle" sem olhar o anterior dispara para sempre.
  let estadoCeliaAnterior = "idle";
  let manualPause = false;
  let renderedBeatId;
  let wakeLock;
  let forceStopPending = false;
  const shortcutStorageKey = "celia:roteiro-live:shortcuts:v1";
  const defaultShortcuts = Object.freeze({
    back: "F6",
    retake: "F7",
    good: "F8",
    celia: "F9",
    stop: "Ctrl+P",
  });
  let shortcuts = {...defaultShortcuts};
  try {
    shortcuts = {
      ...shortcuts,
      ...(JSON.parse(localStorage.getItem(shortcutStorageKey) || "{}") || {}),
    };
  } catch (_) { shortcuts = {...defaultShortcuts}; }

  function setConnection(text, live = false, error = false) {
    connection.textContent = text;
    connection.classList.toggle("live", live);
    connection.classList.toggle("error", error);
  }

  function escapeText(value) { return String(value ?? ""); }

  function recordingClock(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const minutes = Math.floor(total / 60);
    return String(minutes).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
  }

  function normalizeShortcut(value) {
    const parts = String(value || "").split("+").map((item) => item.trim()).filter(Boolean);
    const mods = [];
    let key = "";
    for (const part of parts) {
      const lower = part.toLowerCase();
      if (lower === "ctrl" || lower === "control") mods.push("Ctrl");
      else if (lower === "alt") mods.push("Alt");
      else if (lower === "shift") mods.push("Shift");
      else if (lower === "meta" || lower === "cmd" || lower === "command") mods.push("Meta");
      else key = part.length === 1 ? part.toUpperCase() : part.toUpperCase();
    }
    return [...new Set(mods), key].filter(Boolean).join("+");
  }

  function shortcutFromEvent(event) {
    const parts = [];
    if (event.ctrlKey) parts.push("Ctrl");
    if (event.altKey) parts.push("Alt");
    if (event.shiftKey) parts.push("Shift");
    if (event.metaKey) parts.push("Meta");
    const key = event.key.length === 1 ? event.key.toUpperCase() : event.key.toUpperCase();
    if (!["CONTROL", "ALT", "SHIFT", "META"].includes(key)) parts.push(key);
    return parts.join("+");
  }

  function saveShortcuts() {
    localStorage.setItem(shortcutStorageKey, JSON.stringify(shortcuts));
  }

  function hydrateShortcutInputs() {
    document.querySelectorAll("[data-shortcut]").forEach((input) => {
      input.value = shortcuts[input.dataset.shortcut] || "";
    });
  }

  function setForceStopMessage(text) {
    const message = $("force-stop-message");
    message.hidden = !text;
    message.textContent = text || "";
  }

  function render(snapshot) {
    $("profile").textContent = `PERFIL: ${escapeText(snapshot.profile || "—").toUpperCase()}`;
    const takeNumber = String(snapshot.take_id || "").split("-take-").at(-1) || "—";
    const takeMark = String(snapshot.take_mark || "active").toUpperCase();
    $("take-state").textContent = `TOMADA ${takeNumber} • ${takeMark}`;
    // Nada de nome de roteiro chumbado aqui. O literal "IA VS CRIADOR" fazia o
    // cabeçalho anunciar um roteiro que não era o carregado — a mesma confusão
    // que o padrão silencioso do backend já causava.
    $("script-title").textContent = snapshot.script?.title || "ROTEIRO LIVE";
    $("script-subtitle").textContent = snapshot.script?.subtitle
      || (snapshot.active ? "" : "nenhum roteiro ativo");
    // Voltar para a lista é NAVEGAÇÃO, não mudança de estado da gravação.
    // `mostrandoLista` sobrepõe o snapshot só na tela: o roteiro continua
    // carregado e ativo no servidor, o take segue de pé e nada é resetado.
    // Sem este flag o próximo snapshot (chegam vários por segundo) jogaria o
    // dono de volta ao teleprompter no mesmo instante em que ele saísse.
    const verLista = !snapshot.active || mostrandoLista;
    $("inactive").hidden = !verLista;
    $("timeline").hidden = verLista;
    const voltar = $("back-to-list");
    // O botão só existe quando há para onde voltar: gravação ativa e
    // teleprompter na tela. Na lista ele sumiria de qualquer forma.
    if (voltar) voltar.hidden = !snapshot.active || mostrandoLista;
    // O seletor só existe quando não há gravação em curso. Carrega uma vez ao
    // aparecer, e recarrega sempre que a gravação termina — a marca de "gravado"
    // pode ter mudado de outro aparelho.
    if (verLista) {
      if (!pickerLoaded) loadPicker();
    } else {
      pickerLoaded = false;
    }
    const route = (snapshot.route || []).map((item) => item.title);
    $("route").textContent = (route.length > 4 ? ["…", ...route.slice(-4)] : route).join("  ›  ") || "—";
    $("follow-state").textContent = snapshot.auto_follow ? "AUTO" : "MANUAL";
    $("recording-clock").textContent = recordingClock(snapshot.recording_elapsed_s);
    const recovery = snapshot.recovery || {};
    $("play-expected").disabled = !recovery.planned_celia;
    $("repeat-celia").disabled = !(recovery.can_replay || recovery.planned_celia);
    $("regenerate-voice").disabled = !recovery.can_regenerate;
    $("new-response").disabled = !recovery.can_new_response;

    // Estado da fala da Célia. Precisa ser lido ANTES das linhas, porque o
    // botão da linha ativa depende dele.
    const estadoCelia = String(snapshot.celia?.state || "idle").toLowerCase();
    const celiaFalando = estadoCelia === "falando";

    const beat = snapshot.current_beat;
    const beatChanged = renderedBeatId !== undefined && renderedBeatId !== beat?.id;
    renderedBeatId = beat?.id;
    $("beat-title").textContent = beat?.title || "Aguardando roteiro";
    const scriptLines = $("script-lines");
    let activeLine = false;
    let chaveAtiva = null;
    scriptLines.replaceChildren(...(beat?.lines || []).map((line, indice) => {
      const item = document.createElement("div");
      const speakerKey = String(line.speaker || "").toLowerCase().replace("é", "e");
      const isDone = line.status === "done";
      const isActive = !isDone && !activeLine;
      if (isActive) activeLine = true;
      item.className = `script-line ${speakerKey} ${isDone ? "line-done" : "line-pending"}${isActive ? " line-active" : ""}`;
      const speaker = document.createElement("span");
      speaker.className = "line-speaker";
      speaker.textContent = line.speaker || "";
      const text = document.createElement("span");
      text.className = "line-text";
      text.textContent = line.text || "";
      item.append(speaker, text);
      // UM botão por linha ativa, escolhido pelo DONO DA FALA.
      // Criador fala ao vivo: o único avanço possível é ele dizer que acabou.
      // Célia: quem fala é o áudio, então o botão toca — e o fim do áudio é
      // que avança. Nunca os dois na mesma linha: "Avançar" numa fala da Célia
      // pularia a fala dela sem ninguém ouvir.
      if (isActive) {
        chaveAtiva = chaveDaLinha(beat?.id, indice);
        item.append(botaoDaLinhaAtiva(speakerKey, chaveAtiva, celiaFalando));
      }
      return item;
    }));

    // FIM DO ÁUDIO AVANÇA O ROTEIRO — mas só o áudio que EU pedi.
    //
    // Reage à BORDA falando -> não-falando, não ao estado parado: olhar só
    // "idle" dispararia a cada snapshot, para sempre. E a chave trava o caso
    // de voltar para uma fala da Célia já concluída, que não pode tocar nem
    // avançar sozinha.
    const terminouDeFalar = estadoCeliaAnterior === "falando" && !celiaFalando;
    if (terminouDeFalar && falaPedida && falaPedida === chaveAtiva) {
      falaPedida = null;
      send({ action: "forward_line" });
    } else if (falaPedida && chaveAtiva && falaPedida !== chaveAtiva) {
      // O dono navegou enquanto o áudio rodava: a espera perdeu o dono.
      falaPedida = null;
    }
    estadoCeliaAnterior = estadoCelia;

    const next = (snapshot.next_beats || [])[0];
    $("next-title").textContent = next?.title || "—";
    const nextLines = $("next-lines");
    nextLines.replaceChildren(...(next?.lines || []).slice(0, 2).map((line) => {
      const item = document.createElement("p");
      item.className = "next-line";
      item.textContent = `${line.speaker || ""}: ${line.text || ""}`;
      return item;
    }));
    $("criador-partial").textContent = snapshot.criador?.partial || "";
    $("criador-final").textContent = snapshot.criador?.partial ? "" : (snapshot.criador?.last_final || "");
    const hasCriadorText = Boolean(snapshot.criador?.partial || snapshot.criador?.last_final);
    const scriptMatch = Boolean(snapshot.criador_match?.matched);
    $("criador-partial").classList.toggle("is-speaking", Boolean(snapshot.criador?.partial));
    $("criador-partial").classList.toggle("is-script", scriptMatch);
    $("criador-final").classList.toggle("is-script", scriptMatch);
    $("criador-script-status").textContent = hasCriadorText ? (scriptMatch ? "ROTEIRO" : "IMPROVISO") : "—";
    $("criador-script-status").classList.toggle("is-script", scriptMatch);
    $("celia-text").textContent = snapshot.celia?.last_text || "";
    const celiaState = String(snapshot.celia?.state || "idle").toUpperCase();
    $("celia-state").textContent = celiaState.replaceAll("_", " ");
    $("celia-text").classList.toggle("tts-error", celiaState === "TTS_ERROR");
    if (snapshot.last_event?.type === "roteiro.force_stop") {
      forceStopPending = false;
      setForceStopMessage("FORCE-STOP • PLAYBACK CANCELADO");
    } else if (!forceStopPending) {
      setForceStopMessage("");
    }

    const history = $("history");
    history.replaceChildren(...(snapshot.history || []).slice(-6).map((item) => {
      const line = document.createElement("p");
      const speakerKey = String(item.speaker || "").toLowerCase().replace("é", "e");
      line.className = `history-line ${speakerKey}${item.script_match ? " history-script" : ""}`;
      const speaker = document.createElement("strong");
      speaker.textContent = item.speaker || "";
      line.append(speaker, document.createTextNode(item.text || ""));
      return line;
    }));

    const branchMap = snapshot.branch_options || {};
    const branches = $("branches");
    const buttons = $("branch-buttons");
    const entries = Object.entries(branchMap);
    branches.hidden = !snapshot.active || entries.length === 0;
    buttons.replaceChildren(...entries.map(([label, id]) => {
      const button = document.createElement("button");
      button.type = "button";
      const names = { criador: "CRIADOR VENCEU", celia: "CÉLIA VENCEU", draw: "EMPATE" };
      button.textContent = names[label] || label.replaceAll("_", " ").toUpperCase();
      button.addEventListener("click", () => send({ action: "branch", beat_id: id, confirm: true }));
      return button;
    }));

    if (beatChanged && !manualPause) {
      $("current").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function send(payload) {
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
  }

  /* ---- Seletor de roteiro -------------------------------------------------
     Vive na tela de entrada: aparece quando nenhum roteiro está ativo e some
     quando a gravação começa. Duas regras do Pai viram mecanismo aqui:
       · a interface nunca o joga num roteiro já marcado como gravado;
       · marcar gravado é só o botão dele — evidência de log informa, não decide.
     Tudo que ele vê sai do servidor; este arquivo não deduz "gravado" de nada. */
  let pickerBusy = false;
  let pickerLoaded = false;

  function pickerSay(text, isError) {
    const box = $("picker-message");
    if (!box) return;
    box.textContent = text || "";
    box.classList.toggle("erro", Boolean(isError));
  }

  function evidenceLine(item) {
    const e = item.evidencia || {};
    const partes = [
      `${e.sessoes || 0} ${e.sessoes === 1 ? "sessão" : "sessões"}`,
      `${e.tomadas_boas || 0} boa${e.tomadas_boas === 1 ? "" : "s"}`,
      `${e.beats_vistos || 0}/${item.beats} beats vistos`,
    ];
    return `histórico: ${partes.join(" · ")}`;
  }

  function renderPicker(data) {
    const list = $("picker-list");
    const hint = $("picker-hint");
    if (!list || !hint) return;
    const itens = data.roteiros || [];
    const sugestao = data.sugestao || {};

    if (!itens.length) {
      hint.textContent = "Nenhum roteiro em roteiros/.";
      list.replaceChildren();
      return;
    }
    hint.textContent = sugestao.todos_gravados
      ? (sugestao.id
          ? "Todos já foram gravados — fica no último que tu escolheu."
          : "Todos já foram gravados. Escolhe na mão qual refazer.")
      : "Toca em ESCOLHER. Os já gravados ficam de lado.";

    list.replaceChildren(...itens.map((item) => {
      const row = document.createElement("div");
      row.className = "picker-row"
        + (item.rascunho ? " is-rascunho" : "")
        + (item.ativo ? " is-active" : "")
        + (item.gravado ? " is-gravado" : "")
        + (item.id === sugestao.id ? " is-sugerido" : "");
      row.dataset.id = item.id;

      const name = document.createElement("div");
      name.className = "picker-name";
      name.textContent = item.titulo || item.id;
      if (item.ativo) {
        const tag = document.createElement("span");
        tag.className = "picker-tag ativo";
        tag.textContent = "ATIVO";
        name.append(tag);
      }
      if (item.gravado) {
        const tag = document.createElement("span");
        tag.className = "picker-tag gravado";
        tag.textContent = "GRAVADO";
        name.append(tag);
      }
      if (item.rascunho) {
        // Roteiro sem nenhuma fala abriria o teleprompter vazio. Fica na lista,
        // escolhível na mão, mas avisado — e o servidor nunca o sugere.
        const tag = document.createElement("span");
        tag.className = "picker-tag rascunho";
        tag.textContent = "RASCUNHO";
        name.append(tag);
      }

      const actions = document.createElement("div");
      actions.className = "picker-actions";
      const pick = document.createElement("button");
      pick.type = "button";
      pick.className = "picker-pick";
      // STATUS e AÇÃO são coisas diferentes.
      //
      // O chip azul ATIVO, logo acima, já diz qual roteiro está carregado.
      // O botão repetia "ATIVO" e vinha desabilitado, então o mesmo card
      // mostrava a palavra duas vezes e não oferecia ação nenhuma.
      //
      // Pior que a redundância: isso prendia o dono. Desde que o boot passou a
      // RESTAURAR sem ATIVAR (para o seletor aparecer, ordem dele em
      // 2026-09-30), o estado normal depois de reiniciar é exatamente este —
      // roteiro carregado, logo com chip ATIVO, e gravação parada, logo com o
      // seletor na tela. Com o botão desabilitado não havia como entrar no
      // teleprompter daquele roteiro.
      //
      // USAR fica sempre clicável: ele chama /roteiro/scripts/select, que
      // carrega o roteiro e chama roteiro_live.activate(). É o activate que
      // faz o snapshot virar active=true, e é isso que troca a tela do
      // seletor para o teleprompter.
      pick.textContent = "USAR";
      pick.disabled = pickerBusy;
      pick.dataset.pick = item.id;
      const mark = document.createElement("button");
      mark.type = "button";
      mark.className = "picker-mark" + (item.gravado ? " on" : "");
      mark.textContent = item.gravado ? "DESMARCAR" : "JÁ GRAVEI";
      mark.disabled = pickerBusy;
      mark.dataset.mark = item.id;
      mark.dataset.gravado = item.gravado ? "1" : "";
      actions.append(pick, mark);

      const meta = document.createElement("div");
      meta.className = "picker-meta";
      meta.textContent = item.rascunho
        ? `v${item.versao ?? "?"} · ${item.beats} beats · nenhum com fala · ${item.arquivo}`
        : `v${item.versao ?? "?"} · ${item.beats} beats · ${item.arquivo}`;

      const evid = document.createElement("div");
      evid.className = "picker-evidence";
      evid.textContent = evidenceLine(item);

      row.append(name, meta, evid, actions);
      return row;
    }));
  }

  async function pickerFetch(path, options) {
    const response = await fetch(path, {
      cache: "no-store",
      credentials: "same-origin",
      ...options,
    });
    if (response.status === 401 || response.status === 403) {
      window.location.replace("/roteiro/");
      throw new Error("sessao_expirada");
    }
    if (!response.ok) throw new Error(`http_${response.status}`);
    return response.json();
  }

  async function loadPicker() {
    try {
      renderPicker(await pickerFetch("/roteiro/scripts"));
      pickerLoaded = true;
      pickerSay("");
    } catch (error) {
      if (String(error.message) === "sessao_expirada") return;
      const hint = $("picker-hint");
      if (hint) hint.textContent = "Não consegui ler a lista de roteiros.";
      pickerSay("Erro ao falar com a Célia. Puxa a tela pra baixo pra tentar de novo.", true);
    }
  }

  async function pickerAction(path, body, aviso) {
    if (pickerBusy) return;
    pickerBusy = true;
    pickerSay(aviso);
    try {
      renderPicker(await pickerFetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      }));
      pickerSay("");
    } catch (error) {
      if (String(error.message) !== "sessao_expirada") {
        pickerSay("Não deu certo. Nada foi trocado.", true);
      }
    } finally {
      pickerBusy = false;
      if (!pickerBusy) await loadPicker();
    }
  }

  $("picker-list")?.addEventListener("click", (event) => {
    const alvo = event.target.closest("button");
    if (!alvo) return;
    if (alvo.dataset.pick) {
      // Escolheu um roteiro: sai da lista e volta ao teleprompter assim que o
      // próximo snapshot chegar com active=true.
      mostrandoLista = false;
      pickerAction("/roteiro/scripts/select", {id: alvo.dataset.pick}, "Trocando o roteiro…");
    } else if (alvo.dataset.mark) {
      const marcar = !alvo.dataset.gravado;
      pickerAction(
        "/roteiro/scripts/gravado",
        {id: alvo.dataset.mark, gravado: marcar},
        marcar ? "Marcando como gravado…" : "Tirando a marca…",
      );
    }
  });

  // Identidade estável de uma linha dentro do roteiro. Serve para saber se o
  // áudio que terminou é o da linha que continua ativa — se o dono navegou no
  // meio, a chave muda e o avanço automático não dispara.
  function chaveDaLinha(beatId, indice) {
    return `${beatId || ""}#${indice}`;
  }

  // Quem é o dono da fala manda no botão.
  const DONOS_CELIA = new Set(["celia", "célia"]);

  function botaoDaLinhaAtiva(speakerKey, chave, celiaFalando) {
    const botao = document.createElement("button");
    botao.type = "button";
    botao.className = "line-action";
    botao.dataset.linha = chave;

    if (!DONOS_CELIA.has(speakerKey)) {
      // Criador — e também AÇÃO/OLHAR, que são rubricas que ele executa.
      // Nunca "Tocar": não há áudio para essas linhas.
      botao.textContent = "AVANÇAR";
      botao.dataset.acao = "forward_line";
      // Enquanto a Célia fala, avançar atropelaria o áudio dela.
      botao.disabled = celiaFalando;
      return botao;
    }

    // Célia. Quem fala é o áudio.
    botao.dataset.acao = "play_expected";
    if (celiaFalando) {
      botao.textContent = "🔊 TOCANDO…";
      // Bloqueado SÓ durante a reprodução: é o que impede duas Célias.
      botao.disabled = true;
      botao.classList.add("tocando");
    } else {
      botao.textContent = "▶ TOCAR";
      botao.disabled = false;
    }
    return botao;
  }

  $("script-lines")?.addEventListener("click", (event) => {
    const botao = event.target.closest(".line-action");
    if (!botao || botao.disabled) return;
    event.preventDefault();
    if (botao.dataset.acao === "play_expected") {
      // Guarda local contra clique duplo: o `disabled` só chega no próximo
      // snapshot, e dois cliques rápidos cabem nessa janela.
      if (falaPedida) return;
      falaPedida = botao.dataset.linha;
      botao.disabled = true;
      botao.textContent = "🔊 TOCANDO…";
      botao.classList.add("tocando");
      send({ action: "play_expected" });
      return;
    }
    // Avançar é navegação manual: cancela qualquer espera de fim de áudio.
    falaPedida = null;
    falaPedida = null;
    send({ action: "forward_line" });
  });

  function abrirLista() {
    if (mostrandoLista) return;
    mostrandoLista = true;
    // Força recarregar: a marca de "já gravei" pode ter mudado de outro
    // aparelho enquanto o dono estava no teleprompter.
    pickerLoaded = false;
    $("inactive").hidden = false;
    $("timeline").hidden = true;
    const voltar = $("back-to-list");
    if (voltar) voltar.hidden = true;
    loadPicker();
  }

  $("back-to-list")?.addEventListener("click", (event) => {
    event.preventDefault();
    abrirLista();
  });

  // Esc faz o mesmo caminho do botão. Só quando há gravação ativa e o dono não
  // está digitando — não pode roubar o Esc de um campo nem fechar nada mais.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    const alvo = event.target;
    const digitando = alvo && (
      alvo.tagName === "INPUT" || alvo.tagName === "TEXTAREA" || alvo.isContentEditable
    );
    if (digitando) return;
    const voltar = $("back-to-list");
    // `hidden` do botão é a mesma condição de "dá para voltar": uma fonte só.
    if (!voltar || voltar.hidden) return;
    event.preventDefault();
    abrirLista();
  });

  function requestForceStop() {
    if (socket?.readyState !== WebSocket.OPEN) {
      setForceStopMessage("FORCE-STOP • SEM CONEXAO AUTENTICADA");
      return;
    }
    forceStopPending = true;
    setForceStopMessage("FORCE-STOP • CANCELANDO AUDIO");
    send({ action: "force_stop", source: "roteiro.ctrl_p" });
  }

  function scheduleReconnect() {
    if (leaving || reconnectTimer) return;
    setConnection("ROTEIRO • RECONECTANDO", false, true);
    const ceiling = Math.min(30000, 1500 * 2 ** Math.min(reconnectAttempt++, 5));
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, ceiling * (0.75 + Math.random() * 0.25));
  }

  async function sessionValid() {
    const response = await fetch("/roteiro/status", {
      credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(5000),
    });
    if (response.status === 401 || response.status === 403) {
      leaving = true;
      window.location.replace("/roteiro/");
      return false;
    }
    if (!response.ok) throw new Error(`status:${response.status}`);
    return true;
  }

  async function connect() {
    if (leaving || connecting || (socket && socket.readyState < WebSocket.CLOSING)) return;
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    connecting = true;
    try {
      if (!await sessionValid() || leaving) return;
    } catch (_) {
      scheduleReconnect();
      return;
    } finally {
      connecting = false;
    }
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${protocol}://${location.host}/roteiro/ws`);
    socket.addEventListener("open", () => {
      setConnection("ROTEIRO • CONECTADO", true);
      requestWakeLock();
    });
    socket.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "roteiro.snapshot") {
          reconnectAttempt = 0;
          render(message.snapshot || {});
        }
      } catch (_) { /* malformed event is ignored; the next snapshot recovers */ }
    });
    socket.addEventListener("close", async (event) => {
      if (leaving) return;
      if (event.code === 4401 || event.code === 1008) {
        leaving = true;
        window.location.replace("/roteiro/");
        return;
      }
      try { if (!await sessionValid()) return; } catch (_) { /* network retry below */ }
      scheduleReconnect();
    });
    socket.addEventListener("error", () => setConnection("ROTEIRO • ERRO DE REDE", false, true));
  }

  async function requestWakeLock() {
    if (!navigator.wakeLock?.request) return;
    try { wakeLock = await navigator.wakeLock.request("screen"); } catch (_) { wakeLock = null; }
  }

  function runShortcut(name) {
    if (name === "back") {
      manualPause = true;
      send({ action: "back_line" });
    } else if (name === "retake") {
      manualPause = true;
      send({ action: "retake_self" });
    } else if (name === "good") {
      send({ action: "mark_good" });
    } else if (name === "celia") {
      send({ action: "play_expected" });
    } else if (name === "stop") {
      requestForceStop();
    }
  }

  $("back").addEventListener("click", () => {
    manualPause = true;
    falaPedida = null;
    send({ action: "back_line" });
  });
  $("forward-line").addEventListener("click", () => {
    manualPause = true;
    falaPedida = null;
    send({ action: "forward_line" });
  });
  $("retake").addEventListener("click", () => {
    manualPause = true;
    send({ action: "retake_self" });
  });
  $("mark-good").addEventListener("click", () => send({ action: "mark_good" }));
  $("mark-discarded").addEventListener("click", () => send({ action: "mark_discarded" }));
  $("play-expected").addEventListener("click", () => send({ action: "play_expected" }));
  $("repeat-celia").addEventListener("click", () => send({ action: "repeat_celia" }));
  $("regenerate-voice").addEventListener("click", () => send({ action: "regenerate_voice" }));
  $("new-response").addEventListener("click", () => send({ action: "new_response" }));
  $("game-pause").addEventListener("click", () => send({ action: "game_pause" }));
  $("game-resume").addEventListener("click", () => send({ action: "game_resume" }));
  $("game-retry").addEventListener("click", () => send({ action: "game_retry_commentary" }));
  $("game-skip").addEventListener("click", () => send({ action: "game_skip_commentary" }));
  $("open-ttt").addEventListener("click", () => send({
    action: "open_game", game: "tic-tac-toe", mode: "criador-vs-celia",
  }));
  $("open-checkers").addEventListener("click", () => send({
    action: "open_game", game: "checkers", mode: "criador-vs-celia",
  }));
  $("open-chess").addEventListener("click", () => send({
    action: "open_game", game: "chess", mode: "criador-vs-celia",
  }));
  $("back-word").addEventListener("click", () => {
    const anchor = $("word-anchor").value.trim();
    if (!anchor) return;
    manualPause = true;
    send({ action: "back_to_word", anchor });
  });
  $("add-marker").addEventListener("click", () => {
    const note = $("marker-note").value.trim();
    send({ action: "marker", kind: "manual", note });
    $("marker-note").value = "";
  });
  $("next-control").addEventListener("click", () => {
    manualPause = true;
    send({ action: "next" });
  });
  $("live").addEventListener("click", () => {
    manualPause = false;
    send({ action: "live" });
  });
  menuToggle.addEventListener("click", () => {
    const expanded = menuToggle.getAttribute("aria-expanded") === "true";
    menuToggle.setAttribute("aria-expanded", String(!expanded));
    advancedControls.hidden = expanded;
  });
  $("timeline").addEventListener("touchstart", () => {
    manualPause = true;
  }, { passive: true });

  hydrateShortcutInputs();
  document.querySelectorAll("[data-shortcut]").forEach((input) => {
    input.addEventListener("change", () => {
      const name = input.dataset.shortcut;
      const normalized = normalizeShortcut(input.value);
      shortcuts[name] = normalized || defaultShortcuts[name] || "";
      input.value = shortcuts[name];
      saveShortcuts();
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.repeat) return;
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
    const combo = shortcutFromEvent(event);
    const entry = Object.entries(shortcuts).find(([, value]) => normalizeShortcut(value) === combo);
    if (!entry) return;
    event.preventDefault();
    runShortcut(entry[0]);
  }, true);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") requestWakeLock(); });
  window.addEventListener("beforeunload", () => {
    leaving = true;
    clearTimeout(reconnectTimer);
    socket?.close();
    if (wakeLock) wakeLock.release?.();
  });
  connect();
})();
