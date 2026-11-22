/* Identidade visual: a expressão-base que faz o modelo SER a Célia.
 *
 * agente: claude | 2026-08-13 | ordem do Pai
 *
 * O PONTO
 * -------
 * O modelo `celia_vts` é uma base customizável. Sozinho, ele não é a Célia:
 * é o boneco cru. O que define o visual dela — cabelo, franja, roupa,
 * acessórios, o sorriso de base — é a expressão `Fancy vee`, com
 * 102 parâmetros. Sem ela ativa, aparece outra personagem.
 *
 * QUEM DECIDE QUAL É A BASE
 * -------------------------
 * Não este arquivo. O próprio VTube Studio guarda a resposta:
 *
 *     "SavedActiveExpressions": ["Fancy vee.exp3.json"]
 *
 * É o que o Pai deixou ligado quando salvou o modelo. Ler daí, em vez de
 * chumbar "Fancy vee" no código, significa que ele troca o visual no VTS,
 * recopia o `.vtube.json`, e a Célia acompanha. Nome fixo aqui viraria
 * mentira no dia em que ele mudasse de ideia.
 *
 * POR QUE NÃO BASTA CHAMAR setExpression("Fancy vee")
 * ---------------------------------------------------
 * O gerenciador de expressão do SDK toca UMA por vez. No instante em que a
 * emoção dela vira `angry`, a `Fancy vee` é substituída e ela deixa de
 * parecer a Célia justo quando reage. Identidade não pode competir com
 * emoção pelo mesmo slot.
 *
 * Então a base entra como CAMADA: a cada quadro, logo antes do flush pro
 * core, os 102 valores são somados — mesma semântica `Add` do arquivo de
 * expressão. A emoção continua livre pra usar o slot do gerenciador.
 *
 * O CONFLITO, MEDIDO
 * ------------------
 * Comparei os parâmetros da base contra cada emoção. A interseção é de
 * 6 parâmetros, e só em duas emoções:
 *
 *     raiva     conflita em 6: Param16, Param345, Param96, Param99,
 *                              ParamBrowLY, ParamMouthForm
 *     Pleading  conflita em 1: ParamMouthForm
 *     neutral, Stars, Hearts, Spirals, Blush: nenhum
 *
 * São todos parâmetros de rosto — sobrancelha e boca. É exatamente onde a
 * emoção DEVE ganhar: quando ela está com raiva, a boca da raiva vale mais
 * que a boca do visual padrão. Somar os dois daria uma careta que ninguém
 * desenhou. Por isso a base pula o parâmetro que a expressão ativa já toca.
 *
 * LIMITE CONHECIDO: durante o fade de entrada/saída de uma emoção, a
 * contribuição dela é parcial, mas a base é pulada por inteiro. Dá um
 * pequeno degrau de alguns quadros na boca. Preferi isso a somar errado.
 */
(function instalarIdentidadeCelia() {
  "use strict";

  const LOG = "[identidade]";

  let instalado = null;
  let expressaoAtiva = null;
  let quadros = 0;
  let ultimo = null;

  function pegarLApp() {
    try {
      const a = typeof window.getLAppAdapter === "function" ? window.getLAppAdapter() : null;
      return a && typeof a.getModel === "function" ? a.getModel() : null;
    } catch (_) {
      return null;
    }
  }

  async function tentar() {
    if (instalado) return;
    const lapp = pegarLApp();
    const cubism = lapp && lapp._model;
    const core = cubism && typeof cubism.getModel === "function" ? cubism.getModel() : null;
    const cfg = lapp && lapp._modelSetting;
    if (!core || !cfg || typeof cfg.getExpressionCount !== "function") return;

    const dir = lapp._modelHomeDir;
    let moc = null;
    try {
      moc = cfg.getModelFileName();
    } catch (_) {}
    if (!dir || !moc) return;

    instalado = { pendente: true }; // trava reentrada enquanto o fetch corre
    try {
      const vtubeUrl = dir + moc.replace(/\.moc3$/i, "") + ".vtube.json";
      const resp = await fetch(vtubeUrl);
      if (!resp.ok) {
        ultimo = `${LOG} sem .vtube.json (HTTP ${resp.status}) — nenhuma base a aplicar`;
        console.log(ultimo);
        instalado = { ativo: false, motivo: ultimo };
        return;
      }
      const vtube = await resp.json();
      const salvas = vtube.SavedActiveExpressions || [];
      if (!salvas.length) {
        ultimo = `${LOG} o VTube Studio nao deixou expressao ativa salva`;
        console.log(ultimo);
        instalado = { ativo: false, motivo: ultimo };
        return;
      }

      // Mapa nome -> arquivo, do próprio model3.json já carregado pelo SDK.
      const porArquivo = new Map();
      const total = cfg.getExpressionCount();
      for (let i = 0; i < total; i++) {
        const nome = cfg.getExpressionName(i);
        const arq = cfg.getExpressionFileName(i);
        porArquivo.set(String(arq).split("/").pop(), { nome, arq });
      }

      // Quais parâmetros cada expressão toca — necessário pra saber o que a
      // emoção ativa já está controlando, e não somar por cima.
      const tocadosPor = new Map();
      await Promise.all(
        Array.from(porArquivo.values()).map(async ({ nome, arq }) => {
          try {
            const r = await fetch(dir + arq);
            if (!r.ok) return;
            const j = await r.json();
            tocadosPor.set(nome, new Set((j.Parameters || []).map((p) => p.Id)));
          } catch (_) {}
        })
      );

      // Carrega os valores da(s) base(s).
      const ids = Array.from(core.parameters.ids);
      const mins = core.parameters.minimumValues;
      const maxs = core.parameters.maximumValues;
      const valores = core.parameters.values;

      const camada = [];
      const nomesBase = [];
      const semDrawable = [];
      for (const arquivo of salvas) {
        const achado = porArquivo.get(String(arquivo).split("/").pop());
        if (!achado) {
          semDrawable.push(arquivo);
          continue;
        }
        nomesBase.push(achado.nome);
        const r = await fetch(dir + achado.arq);
        if (!r.ok) {
          semDrawable.push(`${arquivo} (HTTP ${r.status})`);
          continue;
        }
        const j = await r.json();
        for (const p of j.Parameters || []) {
          const i = ids.indexOf(p.Id);
          if (i < 0) continue;
          camada.push({ id: p.Id, i, v: p.Value, min: mins[i], max: maxs[i] });
        }
      }

      if (!camada.length) {
        ultimo = `${LOG} base sem parametro aplicavel: ${semDrawable.join(", ")}`;
        console.warn(ultimo);
        instalado = { ativo: false, motivo: ultimo };
        return;
      }

      // Registrar qual expressão está ativa. `LAppModel.setExpression` é o
      // funil único: o adapter e o bundle passam os dois por aqui.
      const originalSet = lapp.setExpression.bind(lapp);
      lapp.setExpression = function celiaIdentidadeSetExpression(nome) {
        expressaoAtiva = nome;
        return originalSet(nome);
      };

      // Camada aplicada no último instante antes do flush pro core.
      const updateOriginal = cubism.update.bind(cubism);
      cubism.update = function celiaIdentidadeUpdate() {
        try {
          // Se a própria base virou a expressão ativa, o gerenciador já a
          // está aplicando — somar de novo dobraria tudo.
          if (!nomesBase.includes(expressaoAtiva)) {
            const conflitantes = tocadosPor.get(expressaoAtiva) || null;
            for (const p of camada) {
              if (conflitantes && conflitantes.has(p.id)) continue;
              const v = valores[p.i] + p.v;
              valores[p.i] = v < p.min ? p.min : v > p.max ? p.max : v;
            }
          }
          quadros++;
        } catch (erro) {
          console.warn(`${LOG} falhou no quadro:`, erro);
        }
        return updateOriginal();
      };

      instalado = { ativo: true, nomesBase, camada, tocadosPor, semDrawable };
      // A emoção pode ter sido aplicada antes desta camada terminar de
      // instalar. Avise o dono do rosto para ele reafirmar a expressão atual
      // agora que o filtro de conflitos já está ativo.
      window.dispatchEvent(new CustomEvent("celia-identidade-ready", {
        detail: { base: nomesBase.slice() },
      }));
      ultimo =
        `${LOG} base do VTube Studio aplicada: ${nomesBase.join(", ")} ` +
        `(${camada.length} parametros, como camada permanente)`;
      console.log(ultimo);
      if (semDrawable.length) console.warn(`${LOG} nao localizadas:`, semDrawable);
    } catch (erro) {
      ultimo = `${LOG} falhou: ${erro && erro.message}`;
      console.warn(ultimo, erro);
      instalado = { ativo: false, motivo: ultimo };
    }
  }

  setInterval(tentar, 1000);

  let espelho = null;
  setInterval(() => {
    if (!document.body) return;
    if (!espelho) {
      espelho = document.createElement("div");
      espelho.id = "celia-identidade-prova";
      espelho.hidden = true;
      document.body.appendChild(espelho);
    }
    espelho.textContent = JSON.stringify(window.celiaIdentidade.estado());
  }, 1000);

  window.celiaIdentidade = {
    estado: () => {
      if (!instalado || instalado.pendente) return { ativo: false, motivo: "ainda carregando" };
      if (!instalado.ativo) return { ativo: false, motivo: instalado.motivo };
      const amostra = {};
      for (const p of instalado.camada.slice(0, 5)) amostra[p.id] = p.v;
      return {
        ativo: true,
        base: instalado.nomesBase,
        parametros: instalado.camada.length,
        quadros,
        expressaoAtiva,
        pulados: instalado.tocadosPor.get(expressaoAtiva)
          ? Array.from(instalado.tocadosPor.get(expressaoAtiva)).filter((id) =>
              instalado.camada.some((p) => p.id === id)
            )
          : [],
        amostra,
        naoLocalizadas: instalado.semDrawable,
      };
    },
  };

  console.log(`${LOG} instalado`);
})();
