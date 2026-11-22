/*
 * celia-visao-frame.js — o olho dela enche o slot do servidor, UMA FONTE POR VEZ.
 *
 * POR QUE ESTE ARQUIVO EXISTE
 * ---------------------------
 * Decisao 1(a) do Pai em docs/planos/PLANO-VISAO-CELIA.md:
 *   "o navegador e a unica fonte — as ferramentas vision_* passam a ler o
 *    ultimo frame que o Screen ja manda. Zero captura no servidor, imune ao
 *    Wayland."
 *
 * REGRA DO DONO (2026-10-05): TELA E CAMERA SAO FONTES INDEPENDENTES
 * ------------------------------------------------------------------
 * Cada uma tem a sua permissao do navegador e o seu clique. A analise de uma
 * fonte so comeca quando ELE liga aquela fonte na interface (o stream do painel
 * fica vivo) e para quando ele desliga. Ligar uma nao liga a outra.
 *   - painel Screen  -> [role="tabpanel"][id$="content-screen"]
 *   - painel Camera  -> [role="tabpanel"][id$="content-camera"]
 * Este arquivo NUNCA pede permissao, NUNCA abre stream e NUNCA liga nada: so
 * observa o <video> que o proprio painel ja mantem vivo depois do clique.
 *
 * SINAL EXPLICITO
 * ---------------
 * Ao ver o stream de uma fonte ficar vivo, avisa o servidor
 * (POST /bridge/vision/state {source, active:true, width, height}); ao ver o
 * stream morrer (ou a pagina fechar), avisa active:false. So depois do aviso
 * "ligada" os frames sao enviados. O servidor recusa frame de fonte desligada;
 * se ele recusar enquanto o stream ainda esta vivo (ex.: servidor reiniciou),
 * o aviso e reenviado sozinho.
 *
 * CUSTO ZERO DE VRAM. So <canvas> 2d e POST — nada de modelo, nada de GPU.
 *
 * ANCORA QUE SOBREVIVE AO REBUILD
 * -------------------------------
 * Ninguem recompila o bundle desta interface; os nomes de classe sao
 * embaralhados a cada build. Entao a busca e por [role="tabpanel"] mais
 * id$="content-<fonte>", igual ao que ja funciona no celia-audio-fontes.js.
 * Nunca por classe.
 *
 * QUANDO ELE NAO MANDA NADA (de proposito)
 *   - nao existe o painel da fonte na tela
 *   - o <video> nao tem srcObject vivo (fonte desligada)
 *   - a captura foi encerrada pelo usuario
 *   - o POST anterior ainda esta voando
 *   - o interruptor de emergencia `ligado` esta desligado
 */
(function () {
  "use strict";

  const CHAVE = "celia-visao-frame:v1";
  // `ligado` e so a TRAVA DE EMERGENCIA (desliga tudo). Quem manda de verdade e
  // o botao de cada fonte na interface.
  const PADRAO = {
    ligado: true, intervaloMs: 2000, larguraMax: 1280, qualidade: 0.7,
    detectarMudanca: true, limiarMudanca: 0.075,
  };
  const FONTES = ["screen", "camera"];
  const VIGIA_MS = 500; // quanto demora a perceber que o botao foi ligado/desligado

  function config() {
    let salvo = {};
    try { salvo = JSON.parse(localStorage.getItem(CHAVE) || "{}") || {}; } catch (e) { salvo = {}; }
    // Migracao obrigatoria: estado salvo por uma versao anterior nao tem os
    // campos novos, e undefined nao e o mesmo que vazio.
    const fim = {};
    Object.keys(PADRAO).forEach(function (k) {
      fim[k] = (typeof salvo[k] === "undefined") ? PADRAO[k] : salvo[k];
    });
    return fim;
  }

  function salvar(parcial) {
    const novo = Object.assign(config(), parcial || {});
    try { localStorage.setItem(CHAVE, JSON.stringify(novo)); } catch (e) {}
    return novo;
  }

  function novaFonte() {
    return {
      anunciada: false,       // o servidor ja sabe que esta ligada
      anunciando: false,      // POST de estado em voo
      enviando: false,
      enviados: 0,
      recusados: 0,
      falhas: 0,
      ultimoErro: null,
      ultimoEnvioEm: 0,
      ultimoBytes: 0,
      pausadoAte: 0,
      ultimoDiff: 0,
      mudancasEnviadas: 0,
      assinaturaAnterior: null,
      largura: null,
      altura: null,
    };
  }
  const fonte = {};
  FONTES.forEach(function (nome) { fonte[nome] = novaFonte(); });

  function painelDe(nome) {
    return document.querySelector('[role="tabpanel"][id$="content-' + nome + '"]');
  }

  function videoVivo(nome) {
    const painel = painelDe(nome);
    if (!painel) return null;
    const video = painel.querySelector("video");
    if (!video) return null;
    const fluxo = video.srcObject;
    // srcObject vivo com pelo menos uma trilha de video ativa
    if (!fluxo || typeof fluxo.getVideoTracks !== "function") return null;
    const trilhas = fluxo.getVideoTracks().filter(function (t) { return t.readyState === "live"; });
    if (!trilhas.length) return null;
    if (video.readyState < 2) return null;               // ainda sem quadro
    if (!video.videoWidth || !video.videoHeight) return null;
    return video;
  }

  let tela = null;
  function desenhar(video, larguraMax) {
    const escala = Math.min(1, larguraMax / video.videoWidth);
    const w = Math.max(1, Math.round(video.videoWidth * escala));
    const h = Math.max(1, Math.round(video.videoHeight * escala));
    if (!tela) tela = document.createElement("canvas");
    if (tela.width !== w) tela.width = w;
    if (tela.height !== h) tela.height = h;
    const ctx = tela.getContext("2d", { alpha: false, desynchronized: true });
    ctx.drawImage(video, 0, 0, w, h);
    return tela;
  }

  // Detector de mudança do navegador: grade 64x36 (antes 16x9). Com 16x9 uma linha de texto ou uma caixa de diálogo mudando
  // ficava em ~0,0002 de diferença média e era tratada como "tela parada". Agora também mede a maior diferença em UM bloco
  // (8x6) e o gradiente horizontal (assinatura de texto); `relevance` é o maior entre o global e o local. O servidor refaz
  // a medida no quadro real e decide (OCR só, visão, sequência) — isto só decide se vale mandar o sinal de mudança.
  const GW = 64, GH = 36, BC = 8, BR = 6;
  let mini = null;
  function medirMudanca(nome, canvas) {
    const cfg = config();
    const f = fonte[nome];
    if (!cfg.detectarMudanca) return { relevance: null, unchanged: false };
    if (!mini) mini = document.createElement("canvas");
    mini.width = GW; mini.height = GH;
    const ctx = mini.getContext("2d", { alpha: false, willReadFrequently: true });
    ctx.drawImage(canvas, 0, 0, GW, GH);
    const rgba = ctx.getImageData(0, 0, GW, GH).data;
    const atual = new Uint8Array(GW * GH);
    for (let i = 0, j = 0; i < rgba.length; i += 4, j += 1) {
      atual[j] = Math.round(rgba[i] * 0.299 + rgba[i + 1] * 0.587 + rgba[i + 2] * 0.114);
    }
    // A assinatura e POR FONTE: a camera nao pode ser comparada com a tela.
    if (!f.assinaturaAnterior) {
      f.assinaturaAnterior = atual;
      return { relevance: null, unchanged: false };
    }
    const anterior = f.assinaturaAnterior;
    let soma = 0;
    const bw = GW / BC, bh = GH / BR;
    const blocos = new Float64Array(BC * BR);
    for (let y = 0; y < GH; y += 1) {
      for (let x = 0; x < GW; x += 1) {
        const i = y * GW + x;
        const d = Math.abs(atual[i] - anterior[i]);
        soma += d;
        // gradiente horizontal: texto muda a borda sem mexer muito na média de cinza
        const g = x > 0 ? Math.abs(Math.abs(atual[i] - atual[i - 1]) - Math.abs(anterior[i] - anterior[i - 1])) * 2 : 0;
        blocos[Math.floor(y / bh) * BC + Math.floor(x / bw)] += Math.max(d, g);
      }
    }
    f.assinaturaAnterior = atual;
    const diff = soma / (atual.length * 255);
    let localMax = 0;
    for (let k = 0; k < blocos.length; k += 1) localMax = Math.max(localMax, blocos[k] / (bw * bh * 255));
    f.ultimoDiff = Number(diff.toFixed(4));
    f.ultimoDiffLocal = Number(localMax.toFixed(4));
    const limiarLocal = Number(cfg.limiarLocal || 0.06);
    if (diff < Number(cfg.limiarMudanca || 0.075) && localMax < limiarLocal) {
      return { relevance: null, unchanged: true };
    }
    f.mudancasEnviadas += 1;
    return {
      relevance: Math.min(1, Math.max(0, Math.max(diff * 4, localMax * 3))),
      unchanged: false,
    };
  }

  // ── sinal explicito de ligado/desligado ───────────────────────────────────
  function anunciar(nome, ativa, video) {
    const f = fonte[nome];
    if (f.anunciando) return Promise.resolve();
    f.anunciando = true;
    const corpo = { source: nome, active: ativa };
    if (ativa && video) {
      corpo.width = video.videoWidth;
      corpo.height = video.videoHeight;
    }
    return fetch("/bridge/vision/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      f.anunciada = ativa;
      f.ultimoErro = null;
      if (!ativa) f.assinaturaAnterior = null; // religou = comeca sem comparar com frame velho
      if (ativa && video) { f.largura = video.videoWidth; f.altura = video.videoHeight; }
    }).catch(function (e) {
      f.falhas += 1;
      f.ultimoErro = String(e && e.message ? e.message : e);
      f.pausadoAte = Date.now() + Math.min(30000, 2000 * Math.min(8, f.falhas));
    }).then(function () {
      f.anunciando = false;
    });
  }

  // Observa os botoes: stream vivo => avisa "ligada"; stream morto => avisa "desligada".
  function vigiar() {
    const cfg = config();
    FONTES.forEach(function (nome) {
      const f = fonte[nome];
      const video = cfg.ligado ? videoVivo(nome) : null;
      if (video && !f.anunciada) {
        if (Date.now() >= f.pausadoAte) anunciar(nome, true, video);
      } else if (!video && f.anunciada) {
        anunciar(nome, false);
      }
    });
  }

  function enviar(nome, dataUrl, relevance, unchanged) {
    const f = fonte[nome];
    f.enviando = true;
    const corpo = { image: dataUrl, source: nome };
    if (typeof relevance === "number") corpo.relevance = relevance;
    if (unchanged === true) corpo.unchanged = true;
    return fetch("/bridge/vision/frame", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (d) {
      if (d && d.accepted === false) {
        // O servidor nao considera a fonte ligada (reiniciou, timeout). Se o stream
        // ainda esta vivo, o proximo `vigiar` reenvia o sinal "ligada".
        f.recusados += 1;
        f.anunciada = false;
        return;
      }
      f.enviados += 1;
      f.ultimoBytes = (d && d.bytes) || 0;
      f.ultimoEnvioEm = Date.now();
      f.ultimoErro = null;
    }).catch(function (e) {
      f.falhas += 1;
      f.ultimoErro = String(e && e.message ? e.message : e);
      // Recuo: servidor fora do ar nao merece um POST a cada 2 s.
      f.pausadoAte = Date.now() + Math.min(30000, 2000 * Math.min(8, f.falhas));
    }).then(function () {
      f.enviando = false;
    });
  }

  function cicloDe(nome) {
    const cfg = config();
    const f = fonte[nome];
    if (!cfg.ligado) return;
    if (!f.anunciada) return;                           // so manda frame DEPOIS do aviso "ligada"
    if (f.enviando) return;                             // sem empilhar POST
    if (Date.now() < f.pausadoAte) return;              // recuo por falha
    const video = videoVivo(nome);
    if (!video) return;                                 // fonte desligada: silencio honesto
    let dataUrl;
    let relevance = null;
    let unchanged = false;
    try {
      const canvas = desenhar(video, cfg.larguraMax);
      const change = medirMudanca(nome, canvas);
      relevance = change.relevance;
      unchanged = change.unchanged;
      dataUrl = canvas.toDataURL("image/jpeg", cfg.qualidade);
    } catch (e) {
      // Canvas contaminado por origem cruzada, por exemplo. Nao insiste em loop.
      f.falhas += 1;
      f.ultimoErro = "canvas: " + String(e && e.message ? e.message : e);
      f.pausadoAte = Date.now() + 15000;
      return;
    }
    if (!dataUrl || dataUrl.indexOf("data:image/jpeg;base64,") !== 0) return;
    enviar(nome, dataUrl, relevance, unchanged);
  }

  function ciclo() {
    FONTES.forEach(cicloDe);
  }

  let temporizador = null;
  let vigia = null;
  function reagendar() {
    if (temporizador) clearInterval(temporizador);
    temporizador = setInterval(ciclo, Math.max(500, config().intervaloMs));
    if (!vigia) vigia = setInterval(vigiar, VIGIA_MS);
  }
  reagendar();

  // Pagina fechando/recarregando: avisa que as fontes ligadas foram desligadas.
  // sendBeacon sobrevive ao unload; o timeout do servidor cobre o resto.
  window.addEventListener("pagehide", function () {
    FONTES.forEach(function (nome) {
      if (!fonte[nome].anunciada) return;
      try {
        const corpo = new Blob(
          [JSON.stringify({ source: nome, active: false })],
          { type: "application/json" }
        );
        navigator.sendBeacon("/bridge/vision/state", corpo);
      } catch (e) {}
      fonte[nome].anunciada = false;
    });
  });

  function resumoFonte(nome) {
    const f = fonte[nome];
    const v = videoVivo(nome);
    return {
      painelPresente: Boolean(painelDe(nome)),
      videoVivo: Boolean(v),
      resolucao: v ? (v.videoWidth + "x" + v.videoHeight) : null,
      anunciada: f.anunciada,
      enviados: f.enviados,
      recusados: f.recusados,
      falhas: f.falhas,
      ultimoErro: f.ultimoErro,
      ultimoEnvioEm: f.ultimoEnvioEm,
      ultimoBytes: f.ultimoBytes,
      ultimoDiff: f.ultimoDiff,
      mudancasEnviadas: f.mudancasEnviadas,
    };
  }

  window.celiaVisaoFrame = {
    estado: function () {
      const fontes = {};
      let enviados = 0;
      let falhas = 0;
      let ultimoErro = null;
      FONTES.forEach(function (nome) {
        fontes[nome] = resumoFonte(nome);
        enviados += fonte[nome].enviados;
        falhas += fonte[nome].falhas;
        if (fonte[nome].ultimoErro) ultimoErro = fonte[nome].ultimoErro;
      });
      // Chaves antigas continuam existindo (prova-visao-frame.html as le).
      const alguma = FONTES.find(function (n) { return fontes[n].videoVivo; });
      return {
        config: config(),
        fontes: fontes,
        enviados: enviados,
        falhas: falhas,
        ultimoErro: ultimoErro,
        painelPresente: Boolean(fontes.screen.painelPresente || fontes.camera.painelPresente),
        videoVivo: Boolean(alguma),
        resolucao: alguma ? fontes[alguma].resolucao : null,
      };
    },
    // `ligar`/`desligar` mexem so na TRAVA DE EMERGENCIA; o botao de cada fonte manda.
    ligar: function () {
      salvar({ ligado: true });
      FONTES.forEach(function (n) { fonte[n].pausadoAte = 0; });
      reagendar();
      return this.estado();
    },
    desligar: function () {
      salvar({ ligado: false });
      vigiar(); // avisa o servidor na hora que tudo foi desligado
      return this.estado();
    },
    intervalo: function (ms) { salvar({ intervaloMs: ms }); reagendar(); return this.estado(); },
    agora: function () {
      FONTES.forEach(function (n) { fonte[n].pausadoAte = 0; });
      vigiar();
      ciclo();
      return this.estado();
    },
  };
})();
