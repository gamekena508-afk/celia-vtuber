/* Tracking de cabeça para modelos que não usam os nomes canônicos do Cubism.
 *
 * agente: claude | 2026-08-13
 *
 * POR QUE ESTE ARQUIVO EXISTE
 * ---------------------------
 * O modelo real da Célia (`celia_vts`, exportado do VTube Studio) nomeia os
 * parâmetros de cabeça como `AngleX` / `AngleY` / `AngleZ`. O SDK Cubism que
 * o bundle usa escreve em `ParamAngleX` / `ParamAngleY` / `ParamAngleZ` —
 * os nomes padrão. Medido no navegador, com o core que o produto carrega:
 *
 *     AngleX      EXISTE (idx 291)      ParamAngleX   AUSENTE
 *     AngleY      EXISTE (idx 292)      ParamAngleY   AUSENTE
 *     AngleZ      EXISTE (idx 293)      ParamAngleZ   AUSENTE
 *
 * `CubismModel.addParameterValueById` com id inexistente não quebra: o SDK
 * guarda num balde de "parâmetro que não existe" e devolve. Ou seja, a
 * cabeça não seguia nada e NINGUÉM reclamava — falha silenciosa, o pior
 * tipo. Este arquivo é a ponte.
 *
 * O QUE ELE NÃO FAZ
 * -----------------
 * Não escreve em parâmetro que o SDK já alcança. `ParamEyeBallX` (idx 335) e
 * `ParamBodyAngleX` (idx 298) EXISTEM neste modelo, então o bundle já os
 * move; escrever de novo aqui dobraria o valor. A regra é: só assume o
 * parâmetro que o SDK comprovadamente não alcança.
 *
 * Para o `mao_pro` — que usa os nomes canônicos — este arquivo se instala e
 * não faz nada: todo alvo é descartado na verificação. Trocar de modelo não
 * exige mexer aqui.
 *
 * ONDE ELE ENGANCHA
 * -----------------
 * `LAppModel.update()` termina com `this._model.update()`, que é o flush pro
 * core. Envolvemos ESSE método na instância de `CubismModel`: nosso valor
 * entra no último instante antes do flush, no mesmo quadro, depois de
 * motion/expressão/física. Não tocamos no bundle minificado — patch em
 * artefato de build se perde no próximo `npm run build` e não dá diff.
 *
 * LIMITE CONHECIDO: a física roda ANTES do nosso write, então cabelo e
 * acessórios reagem ao ângulo com um quadro de atraso. A 60 fps são ~16 ms.
 * Aceito de propósito — a alternativa era reescrever a ordem do SDK.
 *
 * Faixas de saída: tiradas do `.vtube.json` do próprio modelo
 * (ParameterSettings), não inventadas. FaceAngleX -> AngleX vai de -40 a 40,
 * FaceAngleY -> AngleY de -30 a 30, e por aí.
 */
(function instalarTrackingCelia() {
  "use strict";

  const LOG = "[tracking]";

  /* alvo    = parâmetro deste modelo
   * canonico = parâmetro que o SDK REALMENTE escreve; se ELE existir, o SDK
   *            já resolve e nós saímos do caminho pra não dobrar o valor.
   *            `null` = o SDK não escreve equivalente nenhum, então o alvo é
   *            sempre nosso.
   * valor    = mesma fórmula que o bundle aplica ao canônico, com a faixa
   *            declarada no .vtube.json do modelo
   *
   * A lista de canônicos saiu de LAppModel.update() no bundle, lida linha a
   * linha. Ela escreve em exatamente seis: ParamAngleX, ParamAngleY,
   * ParamAngleZ, ParamBodyAngleX, ParamEyeBallX, ParamEyeBallY. Body Y e
   * Body Z NÃO estão na lista.
   *
   * Isto já me pegou: eu havia posto `ParamBodyAngleZ` como guarda do
   * `Param94`. Esse parâmetro existe neste modelo, então o guarda descartou
   * a regra "porque o SDK dá conta" — e o SDK não dá, nunca escreve nele.
   * Resultado: rotação Z do corpo ficaria parada. Guarda só vale para
   * parâmetro que o SDK comprovadamente escreve.
   */
  const MAPA = [
    { alvo: "AngleX",  canonico: "ParamAngleX", valor: (m) => m._dragX * 40 },
    { alvo: "AngleY",  canonico: "ParamAngleY", valor: (m) => m._dragY * 30 },
    { alvo: "AngleZ",  canonico: "ParamAngleZ", valor: (m) => m._dragX * m._dragY * -30 },
    { alvo: "Param93", canonico: null,          valor: (m) => m._dragY * 10 },
    { alvo: "Param94", canonico: null,          valor: (m) => m._dragX * m._dragY * -10 },
  ];

  let instalado = null;
  let quadros = 0;

  function pegarAdapter() {
    try {
      const f = window.getLAppAdapter;
      return typeof f === "function" ? f() : null;
    } catch (_) {
      return null;
    }
  }

  function tentarInstalar() {
    const adapter = pegarAdapter();
    if (!adapter || typeof adapter.getModel !== "function") return false;

    const lapp = adapter.getModel();
    const cubism = lapp && lapp._model;
    const core = cubism && typeof cubism.getModel === "function" ? cubism.getModel() : null;
    if (!core || !core.parameters) return false;

    // Já instalado nesta mesma instância? Trocar de modelo cria outra.
    if (instalado && instalado.core === core) return true;

    const ids = Array.from(core.parameters.ids);
    const valores = core.parameters.values;
    const mins = core.parameters.minimumValues;
    const maxs = core.parameters.maximumValues;

    const ativos = [];
    const descartados = [];
    for (const regra of MAPA) {
      const i = ids.indexOf(regra.alvo);
      const jaTem = regra.canonico !== null && ids.indexOf(regra.canonico) >= 0;
      if (i < 0) {
        descartados.push(`${regra.alvo}: nao existe neste modelo`);
      } else if (jaTem) {
        descartados.push(`${regra.alvo}: SDK ja escreve ${regra.canonico}`);
      } else {
        ativos.push({ ...regra, i, min: mins[i], max: maxs[i], canonico: regra.canonico || "(SDK nao escreve)" });
      }
    }

    if (!ativos.length) {
      console.log(`${LOG} nada a remapear neste modelo — SDK da conta sozinho`);
      instalado = { core, ativos: [], descartados, hook: false };
      return true;
    }

    const updateOriginal = cubism.update.bind(cubism);
    cubism.update = function celiaTrackingUpdate() {
      try {
        for (const a of ativos) {
          // The director already supplied this frame's driver before physics.
          // Retain mouse tracking as fallback when the director is unavailable.
          if (window.celiaMotionDirector?.ownsParameter?.(a.alvo, core)) continue;
          let v = a.valor(lapp);
          if (!Number.isFinite(v)) v = 0;
          valores[a.i] = v < a.min ? a.min : v > a.max ? a.max : v;
        }
        quadros++;
      } catch (erro) {
        console.warn(`${LOG} falhou no quadro:`, erro);
      }
      return updateOriginal();
    };

    instalado = { core, cubism, lapp, ativos, descartados, hook: true };
    console.log(
      `${LOG} remapeado: ${ativos.map((a) => `${a.canonico}->${a.alvo}`).join(", ")}`
    );
    for (const d of descartados) console.log(`${LOG} ignorado — ${d}`);
    return true;
  }

  // O modelo demora pra carregar (o da Célia tem 160 MB de .moc3) e pode ser
  // trocado em runtime. Reavaliar sempre é barato e cobre os dois casos.
  setInterval(tentarInstalar, 1000);

  /* Espelho em DOM do estado.
   *
   * Sem isto, provar o tracking exige um navegador com depurador acoplado.
   * Com isto, `--dump-dom` num headless já entrega o estado inteiro em texto,
   * e o teste não depende de eu conseguir olhar a tela — o que nesta máquina
   * (Wayland) nem sempre acontece. Fica `hidden`: é instrumento, não UI.
   */
  let espelho = null;
  setInterval(() => {
    if (!espelho) {
      if (!document.body) return;
      espelho = document.createElement("div");
      espelho.id = "celia-tracking-prova";
      espelho.hidden = true;
      document.body.appendChild(espelho);
    }
    try {
      espelho.textContent = JSON.stringify(window.celiaTracking.estado());
    } catch (erro) {
      espelho.textContent = JSON.stringify({ erro: String(erro) });
    }
  }, 1000);

  // Superfície de prova: sem isto, testar tracking exige mexer o mouse e
  // olhar. Com isto, dá pra provar por script que o valor chegou no core.
  window.celiaTracking = {
    instalar: tentarInstalar,
    estado: () => {
      if (!instalado) return { instalado: false };
      const ids = Array.from(instalado.core.parameters.ids);
      const vals = instalado.core.parameters.values;
      const lidos = {};
      for (const a of instalado.ativos) lidos[a.alvo] = vals[a.i];
      return {
        instalado: true,
        hook: instalado.hook,
        quadros,
        ativos: instalado.ativos.map((a) => `${a.canonico} -> ${a.alvo} (idx ${a.i}, ${a.min}..${a.max})`),
        descartados: instalado.descartados,
        valoresAgora: lidos,
        totalParametros: ids.length,
        autotestes: window.celiaTracking ? window.celiaTracking.autotestes || null : null,
      };
    },
    /* Empurra um olhar artificial e força UM quadro, pra provar sem mouse.
     *
     * Não adianta só escrever `_dragX`: `LAppModel.update()` começa
     * recalculando `_dragX` a partir do `_dragManager`, então um valor
     * plantado é apagado antes do nosso hook ler. E `setDragging()` passa
     * pelo amortecimento do `CubismTargetPoint`, que leva vários quadros pra
     * convergir — inútil num headless que roda 9 quadros na sessão inteira.
     *
     * Então: planta o drag e chama `cubism.update()` na hora. Isso dispara
     * exatamente o nosso hook, no mesmo caminho do quadro real. Prova o que
     * é NOSSA responsabilidade — dado um drag, o parâmetro certo recebe o
     * valor certo. De onde vem o drag continua sendo trabalho do SDK.
     */
    simularOlhar: (x, y) => {
      if (!instalado || !instalado.hook) return false;
      instalado.lapp._dragX = x;
      instalado.lapp._dragY = y;
      instalado.cubism.update();
      return true;
    },
  };

  /* Autoteste por `?provaTracking=1`.
   *
   * Saber que o hook instalou não é saber que a cabeça MEXE. Aqui um olhar
   * artificial é empurrado e o valor é lido de volta do core, no parâmetro
   * de destino. Se `AngleX` sair de zero, o caminho inteiro — drag, hook,
   * clamp, escrita no core — está vivo. É o mesmo teste que eu faria com o
   * mouse, só que verificável por script.
   */
  if (/(?:\?|&)provaTracking=1/.test(location.search)) {
    let fase = 0;
    setInterval(() => {
      if (!instalado || !instalado.hook) return;
      // Direita, esquerda-e-baixo, e volta ao centro: o terceiro caso prova
      // que o valor RETORNA, não que ficou preso num extremo.
      const casos = [
        { nome: "olhando pra direita", x: 1, y: 0, esperado: { AngleX: 40 } },
        { nome: "esquerda e pra baixo", x: -1, y: 0.5, esperado: { AngleX: -40, AngleY: 15 } },
        { nome: "de volta ao centro", x: 0, y: 0, esperado: { AngleX: 0, AngleY: 0 } },
      ];
      const caso = casos[fase % casos.length];
      fase++;
      window.celiaTracking.simularOlhar(caso.x, caso.y);
      const lidos = window.celiaTracking.estado().valoresAgora;
      const confere = Object.entries(caso.esperado).every(
        ([k, v]) => Math.abs((lidos[k] || 0) - v) < 0.01
      );
      const historico = window.celiaTracking.autotestes || [];
      historico.push({ caso: caso.nome, esperado: caso.esperado, lidos, confere });
      window.celiaTracking.autotestes = historico.slice(-6);
      window.celiaTracking.ultimoAutoteste = historico[historico.length - 1];
    }, 2000);
  }

  console.log(`${LOG} instalado`);
})();
