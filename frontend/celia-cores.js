/* Cores por art mesh: traz do VTube Studio o que o SDK Cubism não lê.
 *
 * agente: claude | 2026-08-13
 *
 * O QUE EU DESCOBRI OLHANDO AS TEXTURAS
 * -------------------------------------
 * As 13 texturas do modelo real da Célia são quase brancas. Medi a média
 * RGB dos pixels opacos: (226,223,231), (218,215,224), (197,192,200)... Não
 * é bug de renderização — é um modelo de base feito pra ser COLORIDO na
 * hora, e a coloração mora no VTube Studio, não no modelo.
 *
 * O VTS guarda isso em `<modelo>.vtube.json`, em
 * `ArtMeshDetails.ArtMeshMultiplyAndScreenColors`: 12 entradas no formato
 * `"ID": "RRGGBBAA|RRGGBBAA"` (multiply | screen). O SDK Cubism for Web
 * ignora `.vtube.json` inteiro — ele só conhece `.model3.json`. Resultado:
 * no VTube Studio a Célia sai colorida; na Célia, crua.
 *
 * Os 12 IDs são nomes de art mesh, e casam 1-para-1 com os drawables do
 * modelo — conferi os 12 contra `core.drawables.ids`, todos acharam índice.
 * Então não é adaptação por semelhança: é o mesmo identificador dos dois
 * lados.
 *
 * COMO APLICA
 * -----------
 * `setMultiplyColorByRGBA` / `setScreenColorByRGBA` já existem no SDK que o
 * bundle carrega, e só passam a valer com
 * `setOverwriteFlagForDrawableMultiplyColors(indice, true)`. Sem a flag, o
 * SDK descarta a cor do usuário e usa a do modelo.
 *
 * ESCOPO HONESTO: isto reproduz a configuração de cor QUE O PAI JÁ TEM no
 * VTube Studio dele. Não inventa paleta, não escolhe cor. Se ele mudar a
 * cor no VTS e salvar, basta recopiar o `.vtube.json`.
 */
(function instalarCoresCelia() {
  "use strict";

  const LOG = "[cores]";
  let resultado = null;
  let tentando = false;
  let dados = null; // entradas já resolvidas em índice, prontas pra reaplicar
  let reaplicacoes = 0;

  /* Índices de drawable que NÃO devem ser desenhados. Ver o bloco
   * MARCA_DAGUA abaixo: esconder por cor não funciona neste shader.
   */
  const naoDesenhar = new Set();
  const marcaResolvida = [];
  let rendererComHook = null;
  let puladosNoQuadro = 0;

  /* Envolve `drawMeshWebGL` para pular os índices marcados.
   *
   * Reinstala sempre que o renderizador muda de identidade: o
   * `celia-mascaras.js` destrói e recria o renderizador para dar conta das
   * 124 máscaras, e um hook preso ao objeto antigo morreria em silêncio —
   * exatamente o tipo de falha calada que me custou a tarde.
   */
  function instalarPuloDeDesenho() {
    const lapp = pegarLApp();
    if (!lapp || typeof lapp.getRenderer !== "function") return;
    const r = lapp.getRenderer();
    if (!r || typeof r.drawMeshWebGL !== "function") return;
    if (rendererComHook === r) return;

    const original = r.drawMeshWebGL.bind(r);
    r.drawMeshWebGL = function celiaPulaDesenho(modelo, indice) {
      if (naoDesenhar.has(indice)) {
        puladosNoQuadro++;
        return;
      }
      return original(modelo, indice);
    };
    rendererComHook = r;
    console.log(`${LOG} hook de desenho instalado (pulando ${naoDesenhar.size} mesh)`);
  }
  setInterval(instalarPuloDeDesenho, 500);

  /* MARCA-D'ÁGUA — ordem do Pai, 13/08/2026: "tira a marca da água da cara
   * dela, tem que deixar com cor transparente ou deletar".
   *
   * OS ALVOS: `face_skin4` (índice 1230) e `face_skin2` (índice 1231), ambos
   * na textura 3, ordens de desenho 1213 e 1212 — os dois drawables mais no
   * topo do modelo inteiro.
   *
   * O Max achou o `face_skin4` e mediu: pulando só o draw call dele, o
   * gradiente na faixa do texto cai de 8.804 para 6.286 (−28,6%), repetindo
   * em 6.285. Eu implementei só esse e dei por resolvido — o gradiente
   * bateu 5.81, dentro da faixa de limpo, e a olhada rápida mostrou rosto.
   *
   * ERRADO. Sobrava texto. `face_skin2` desenha a MESMA marca numa camada
   * translúcida (opacidade 0.39): o que resta dela não é um bloco de texto,
   * são as letras esfaceladas, que eu olhei e chamei de "sujeira do
   * renderizador por software". O Pai ampliou e mostrou que eram letras.
   *
   * Duas lições, e a segunda dói mais:
   *   - o gradiente caiu o bastante pra parecer limpo porque o que sobrou é
   *     esparso. Métrica agregada não enxerga resíduo esparso — some no
   *     meio da média;
   *   - eu VI os pontos, não entendi, e batizei de artefato. Dar nome de
   *     "artefato" pro que eu não expliquei é a forma educada de desistir.
   * Ordem do Pai, direta: "é face_skin2 e face_skin4, deixe estas duas
   * transparentes ou delete elas".
   *
   * POR QUE EU NÃO ACHAVA — e por que `multiply alpha 0` era um instrumento
   * quebrado. O fragment shader do bundle, lido direto do arquivo:
   *
   *     texColor.rgb = texColor.rgb * u_multiplyColor.rgb;
   *     vec4 color   = texColor * u_baseColor;
   *     gl_FragColor = vec4(color.rgb, color.a);
   *
   * `u_multiplyColor.a` NUNCA é usado — só o RGB entra na conta. Então
   * `setMultiplyColorByRGBA(i,1,1,1,0)` não esconde coisa nenhuma; a
   * transparência real vem de `u_baseColor.a`, que é alimentado pela
   * opacidade do drawable. Eu confirmava por releitura que o alpha era
   * 0.000, dava por escondido, e o mesh continuava sendo desenhado. Onze
   * hipóteses caíram por causa disso: o alvo mudava, o instrumento não
   * funcionava em nenhum.
   *
   * O MECANISMO QUE FUNCIONA: não desenhar. `drawMeshWebGL(model, indice)` é
   * o funil único de todo desenho, inclusive quando o mesh está sendo gerado
   * como máscara. Envolvê-lo e retornar cedo para o índice alvo remove o
   * mesh de verdade, é reversível numa linha e não toca no `.moc3`, que é
   * binário e do artista.
   */
  const MARCA_DAGUA = ["face_skin4", "face_skin2"];

  function hexParaRGBA(hex) {
    // "2A3742FF" -> {r,g,b,a} em 0..1
    const n = hex.trim();
    if (!/^[0-9a-fA-F]{8}$/.test(n)) return null;
    return {
      r: parseInt(n.slice(0, 2), 16) / 255,
      g: parseInt(n.slice(2, 4), 16) / 255,
      b: parseInt(n.slice(4, 6), 16) / 255,
      a: parseInt(n.slice(6, 8), 16) / 255,
    };
  }

  function pegarLApp() {
    try {
      const a = typeof window.getLAppAdapter === "function" ? window.getLAppAdapter() : null;
      return a && typeof a.getModel === "function" ? a.getModel() : null;
    } catch (_) {
      return null;
    }
  }

  /* Escrever a cor no modelo. Separado do carregamento de propósito: precisa
   * poder rodar de novo.
   *
   * `celia-mascaras.js` destrói e recria o renderizador quando o modelo pede
   * mais buffers de máscara. Aplicar UMA vez deixava o resultado à mercê de
   * quem rodasse primeiro — e eu vi as duas pontas dessa corrida: numa
   * captura a marca-d'água tinha sumido, na seguinte estava de volta, com o
   * mesmo código. Não é intermitência aceitável: é aplicar e nunca conferir.
   */
  function aplicar() {
    if (!dados) return 0;
    const { cubism, entradas } = dados;
    let n = 0;
    for (const e of entradas) {
      cubism.setMultiplyColorByRGBA(e.i, e.mul.r, e.mul.g, e.mul.b, e.mul.a);
      cubism.setScreenColorByRGBA(e.i, e.scr.r, e.scr.g, e.scr.b, e.scr.a);
      cubism.setOverwriteFlagForDrawableMultiplyColors(e.i, true);
      cubism.setOverwriteFlagForDrawableScreenColors(e.i, true);
      n++;
    }
    return n;
  }

  /* A cor continua valendo? Pergunta ao modelo, não à minha memória. */
  function desviou() {
    if (!dados) return false;
    const { cubism, entradas } = dados;
    for (const e of entradas) {
      try {
        if (!cubism.getOverwriteFlagForDrawableMultiplyColors(e.i)) return true;
        const c = cubism.getMultiplyColor(e.i);
        if (Math.abs(c.a - e.mul.a) > 0.005) return true;
      } catch (_) {
        return true;
      }
    }
    return false;
  }

  async function tentar() {
    // Já carregado: o trabalho agora é vigiar, não recarregar.
    if (dados) {
      if (desviou()) {
        reaplicacoes++;
        const n = aplicar();
        console.warn(`${LOG} cor foi desfeita por outro passo — ${n} reaplicadas (${reaplicacoes}x)`);
      }
      return;
    }
    if (tentando || resultado) return;
    const lapp = pegarLApp();
    const cubism = lapp && lapp._model;
    if (!cubism || typeof cubism.setMultiplyColorByRGBA !== "function") return;
    const core = typeof cubism.getModel === "function" ? cubism.getModel() : null;
    if (!core || !core.drawables) return;

    // Caminho do .vtube.json deduzido do próprio modelo carregado: o SDK
    // guarda a pasta em `_modelHomeDir` e o nome do .moc3 no settings.
    // Nada de caminho fixo — trocar de modelo continua funcionando.
    const dir = lapp._modelHomeDir;
    let moc = null;
    try {
      moc = lapp._modelSetting && lapp._modelSetting.getModelFileName();
    } catch (_) {}
    if (!dir || !moc) return;

    tentando = true;
    const alvo = dir + moc.replace(/\.moc3$/i, "") + ".vtube.json";
    try {
      const resp = await fetch(alvo);
      if (!resp.ok) {
        resultado = { aplicadas: 0, motivo: `sem .vtube.json (HTTP ${resp.status}) em ${alvo}` };
        console.log(`${LOG} ${resultado.motivo} — modelo fica com a cor nativa`);
        return;
      }
      const vtube = await resp.json();
      const lista =
        (vtube.ArtMeshDetails && vtube.ArtMeshDetails.ArtMeshMultiplyAndScreenColors) || [];
      if (!lista.length) {
        resultado = { aplicadas: 0, motivo: "o .vtube.json nao tem cor por art mesh" };
        console.log(`${LOG} ${resultado.motivo}`);
        return;
      }

      const ids = Array.from(core.drawables.ids);
      const entradas = [];
      const perdidas = [];
      for (const item of lista) {
        const i = ids.indexOf(item.ID);
        if (i < 0) {
          perdidas.push(item.ID);
          continue;
        }
        const partes = String(item.Value || "").split("|");
        const mul = hexParaRGBA(partes[0] || "");
        const scr = hexParaRGBA(partes[1] || "");
        if (!mul || !scr) {
          perdidas.push(`${item.ID} (valor ilegivel: ${item.Value})`);
          continue;
        }
        entradas.push({ id: item.ID, i, mul, scr, origem: "vtube.json" });
      }

      // Ordem do Pai: a marca sai. Não por cor — por não desenhar.
      for (const nome of MARCA_DAGUA) {
        const i = ids.indexOf(nome);
        if (i < 0) {
          perdidas.push(`${nome} (marca-d'agua: drawable nao existe)`);
          continue;
        }
        naoDesenhar.add(i);
        marcaResolvida.push(`${nome}#${i}`);
      }

      dados = { cubism, entradas };
      aplicar();

      resultado = {
        arquivo: alvo,
        total: lista.length,
        aplicadas: entradas.length,
        quais: entradas.map((e) => `${e.id}#${e.i}`),
        marcaDagua: marcaResolvida,
        naoEncontradas: perdidas,
      };
      console.log(`${LOG} ${entradas.length} cores aplicadas (${lista.length} do VTS + marca-d'agua)`);
      if (perdidas.length) console.warn(`${LOG} sem drawable correspondente:`, perdidas);
    } catch (erro) {
      resultado = { aplicadas: 0, motivo: String(erro) };
      console.warn(`${LOG} falhou:`, erro);
    } finally {
      tentando = false;
    }
  }

  setInterval(tentar, 1000);

  /* Diagnóstico: `?ocultar=id1,id2` esconde art meshes por nome.
   *
   * CORRIGIDO em 13/08 — a versão anterior usava `multiply alpha 0` e NÃO
   * escondia nada, porque o shader ignora esse alpha. Eu rodei onze testes
   * com ele e li os onze resultados como "não é esse mesh", quando o certo
   * era "meu instrumento não faz nada". Instrumento quebrado não dá
   * resposta errada: dá onze respostas erradas seguidas, todas plausíveis.
   *
   * Agora usa o mesmo caminho da marca-d'água: pular o draw call.
   */
  let ocultados = null;
  function ocultarPorDiagnostico() {
    if (ocultados) return;
    const m = /(?:\?|&)ocultar=([^&]+)/.exec(location.search);
    if (!m) return;
    const lapp = pegarLApp();
    const cubism = lapp && lapp._model;
    const core = cubism && typeof cubism.getModel === "function" ? cubism.getModel() : null;
    if (!core) return;
    const ids = Array.from(core.drawables.ids);
    const pedidos = decodeURIComponent(m[1]).split(",").map((s) => s.trim()).filter(Boolean);
    const feitos = [];
    for (const nome of pedidos) {
      const i = ids.indexOf(nome);
      if (i < 0) {
        feitos.push(`${nome}: NAO EXISTE`);
        continue;
      }
      naoDesenhar.add(i);
      feitos.push(`${nome}#${i}: draw call pulado`);
    }
    ocultados = feitos;
    console.warn(`${LOG} DIAGNOSTICO ocultar=`, feitos);
  }
  setInterval(ocultarPorDiagnostico, 1000);

  /* Diagnóstico: `?listarRegiao=x0,y0,x1,y1` lista os drawables VIVOS que
   * cobrem uma caixa em espaço de modelo, por ordem de desenho.
   *
   * Existe porque eu queimei dez tentativas listando isso num probe offline,
   * onde o modelo só tem a expressão somada aos padrões. No app vivo rodam
   * também idle, piscada, física e a camada de identidade — o conjunto de
   * drawables visíveis NÃO é o mesmo. Medir fora do lugar onde o defeito
   * aparece foi o erro; isto corrige o instrumento.
   */
  let listagem = null;
  function listarRegiao() {
    if (listagem) return;
    const m = /(?:\?|&)listarRegiao=([^&]+)/.exec(location.search);
    if (!m) return;
    const [x0, y0, x1, y1] = decodeURIComponent(m[1]).split(",").map(Number);
    const lapp = pegarLApp();
    const cubism = lapp && lapp._model;
    const core = cubism && typeof cubism.getModel === "function" ? cubism.getModel() : null;
    if (!core) return;
    const D = core.drawables;
    const ids = Array.from(D.ids);
    const achados = [];
    for (let i = 0; i < ids.length; i++) {
      if (!(D.dynamicFlags[i] & 1)) continue;
      if (D.opacities[i] < 0.02) continue;
      const vp = D.vertexPositions[i];
      const n = D.vertexCounts[i];
      let bx0 = 1e9, by0 = 1e9, bx1 = -1e9, by1 = -1e9;
      for (let k = 0; k < n; k++) {
        const x = vp[k * 2], y = vp[k * 2 + 1];
        if (x < bx0) bx0 = x; if (x > bx1) bx1 = x;
        if (y < by0) by0 = y; if (y > by1) by1 = y;
      }
      const ix = Math.max(0, Math.min(bx1, x1) - Math.max(bx0, x0));
      const iy = Math.max(0, Math.min(by1, y1) - Math.max(by0, y0));
      const cob = (ix * iy) / ((x1 - x0) * (y1 - y0));
      if (cob > 0.2) {
        achados.push({
          id: ids[i], i, cobre: +(cob * 100).toFixed(0),
          ordem: D.renderOrders[i], opac: +D.opacities[i].toFixed(2),
          tex: D.textureIndices[i],
        });
      }
    }
    achados.sort((a, b) => b.ordem - a.ordem);
    listagem = achados;
    console.warn(`${LOG} DIAGNOSTICO listarRegiao:`, achados);
  }
  setInterval(listarRegiao, 1500);

  /* Diagnóstico: `?ocultarParte=id1,id2` zera a opacidade da PARTE dona do
   * drawable, a cada quadro.
   *
   * Caminho diferente do multiply alpha: o multiply age no shader, a parte
   * age antes, na composição. Medi que o multiply alpha 0 NÃO estava
   * apagando `face_skin2` — ele continuava listado vivo, opacidade 0.39,
   * cobrindo 96% do rosto. Quando um mecanismo não responde, o certo é
   * trocar de mecanismo, não repetir o mesmo com outro alvo.
   */
  let partesZeradas = null;
  function ocultarParte() {
    const m = /(?:\?|&)ocultarParte=([^&]+)/.exec(location.search);
    if (!m) return;
    const lapp = pegarLApp();
    const cubism = lapp && lapp._model;
    const core = cubism && typeof cubism.getModel === "function" ? cubism.getModel() : null;
    if (!core || typeof cubism.setPartOpacityByIndex !== "function") return;
    const dids = Array.from(core.drawables.ids);
    const pais = core.drawables.parentPartIndices;
    const alvos = decodeURIComponent(m[1]).split(",").map((s) => s.trim()).filter(Boolean);
    const feitos = [];
    for (const nome of alvos) {
      const i = dids.indexOf(nome);
      if (i < 0) { feitos.push(`${nome}: NAO EXISTE`); continue; }
      const parte = pais ? pais[i] : -1;
      if (parte < 0) { feitos.push(`${nome}: sem parte pai`); continue; }
      cubism.setPartOpacityByIndex(parte, 0);
      feitos.push(`${nome}#${i} -> parte ${parte} zerada`);
    }
    if (!partesZeradas) console.warn(`${LOG} DIAGNOSTICO ocultarParte:`, feitos);
    partesZeradas = feitos;
  }
  setInterval(ocultarParte, 200);

  let espelho = null;
  setInterval(() => {
    if (!document.body) return;
    if (!espelho) {
      espelho = document.createElement("div");
      espelho.id = "celia-cores-prova";
      espelho.hidden = true;
      document.body.appendChild(espelho);
    }
    espelho.textContent = JSON.stringify(window.celiaCores.estado());
  }, 1000);

  /* Releitura: a cor continua valendo depois que tudo mais se instalou?
   *
   * Aplicar e declarar vitória seria só metade. Outros arquivos recriam o
   * renderizador e empilham camadas de parâmetro; se algum deles derrubasse
   * a flag de sobrescrita, a cor sumiria sem aviso. Aqui a mesma pergunta é
   * refeita ao modelo, lendo de volta o que ele realmente vai usar.
   */
  function conferir() {
    const lapp = pegarLApp();
    const cubism = lapp && lapp._model;
    if (!cubism || !resultado || !resultado.quais) return null;
    const core = typeof cubism.getModel === "function" ? cubism.getModel() : null;
    if (!core) return null;
    const ids = Array.from(core.drawables.ids);
    const conferidas = [];
    for (const marca of resultado.quais) {
      const nome = marca.split("#")[0];
      const i = ids.indexOf(nome);
      if (i < 0) continue;
      try {
        const flag = cubism.getOverwriteFlagForDrawableMultiplyColors(i);
        const c = cubism.getMultiplyColor(i);
        conferidas.push({
          id: nome,
          sobrescrevendo: !!flag,
          multiply: `${c.r.toFixed(3)},${c.g.toFixed(3)},${c.b.toFixed(3)},a=${c.a.toFixed(3)}`,
        });
      } catch (_) {}
    }
    return conferidas;
  }

  window.celiaCores = {
    aplicar: tentar,
    conferir,
    estado: () => {
      const base = resultado || { aplicadas: null, motivo: "ainda nao rodou" };
      return {
        ...base,
        reaplicacoes,
        desviouAgora: desviou(),
        // prova de que o hook está vivo: se `pulados` não sobe, não pulou nada
        hookDeDesenho: !!rendererComHook,
        naoDesenhar: Array.from(naoDesenhar),
        pulados: puladosNoQuadro,
        listagem,
        releitura: conferir(),
      };
    },
  };

  console.log(`${LOG} instalado`);
})();
