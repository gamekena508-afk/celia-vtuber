/* Máscaras de recorte: dá ao renderizador buffers suficientes pro modelo real.
 *
 * agente: claude | 2026-08-13
 *
 * O SINTOMA
 * ---------
 * Com o modelo `celia_vts` selecionado, a Célia simplesmente NÃO APARECE. O
 * app conecta, o fundo pinta, o canvas existe, o modelo carrega, o loop de
 * update roda — e a tela fica vazia. Nenhum erro de rede, nenhum 404.
 *
 * A CAUSA, dita pelo próprio núcleo Cubism
 * ----------------------------------------
 *     [CSM][E]not supported mask count : 88
 *
 * Esse "88" não é o total de máscaras: em `setupLayoutBounds` o SDK loga
 * `o - s`, ou seja, o EXCEDENTE. Com um buffer de máscara o teto é 36
 * (`ClippingMaskMaxCountOnDefault`), então o modelo pede 36 + 88 = 124
 * máscaras de recorte. Estourado o teto, o SDK aborta o layout e o modelo
 * inteiro deixa de ser desenhado. Falha silenciosa na tela, barulhenta só
 * no console.
 *
 * O MESMO MODELO derruba também o `pixi-live2d-display` do visualizador, por
 * outro caminho (`setupShaderProgram` lendo canal de máscara indefinido).
 * Dois renderizadores independentes, a mesma origem: contagem de máscara.
 * Não é bug de um deles nem textura corrompida — é o modelo pedindo mais do
 * que o padrão entrega.
 *
 * O CONSERTO
 * ----------
 * Com mais de um buffer o teto vira `32 * quantidade` (constante
 * `ClippingMaskMaxCountOnMultiRenderTexture`). Para 124 máscaras:
 * ceil(124 / 32) = 4 buffers. Isto é API pública do SDK —
 * `createRenderer(quantidade)` — e não patch no bundle, que é artefato de
 * build e se perderia no próximo `npm run build`.
 *
 * A quantidade não é chutada: criamos o renderizador, PERGUNTAMOS ao
 * gerenciador de recorte quantos contextos ele montou, e só então
 * recriamos com o número certo. Modelo que não usa máscara não paga nada.
 */
(function instalarMascarasCelia() {
  "use strict";

  const LOG = "[mascaras]";
  const POR_BUFFER = 32; // ClippingMaskMaxCountOnMultiRenderTexture
  const TETO_BUFFER_UNICO = 36; // ClippingMaskMaxCountOnDefault

  let resolvido = null;
  let ultimo = null;

  function pegarGL() {
    // getContext devolve o MESMO contexto já criado para aquele tipo, então
    // isto não cria um segundo contexto — só recupera o que o app abriu.
    const cv = document.getElementById("canvas") || document.querySelector("canvas");
    if (!cv) return null;
    return cv.getContext("webgl2") || cv.getContext("webgl") || null;
  }

  function contarMascaras(renderer) {
    try {
      const cm = renderer && renderer._clippingManager;
      const lista = cm && cm._clippingContextListForMask;
      return lista && typeof lista.getSize === "function" ? lista.getSize() : 0;
    } catch (_) {
      return 0;
    }
  }

  function tentar() {
    let adapter;
    try {
      adapter = typeof window.getLAppAdapter === "function" ? window.getLAppAdapter() : null;
    } catch (_) {
      return;
    }
    const lapp = adapter && typeof adapter.getModel === "function" ? adapter.getModel() : null;
    if (!lapp || typeof lapp.getRenderer !== "function") return;

    const renderer = lapp.getRenderer();
    if (!renderer) return;

    const cubism = lapp._model;
    if (resolvido && resolvido.cubism === cubism) return; // já tratado

    const mascaras = contarMascaras(renderer);
    if (mascaras === 0) {
      resolvido = { cubism, mascaras: 0, buffers: 1, agiu: false };
      ultimo = `${LOG} modelo nao usa mascara de recorte — nada a fazer`;
      console.log(ultimo);
      return;
    }

    if (mascaras <= TETO_BUFFER_UNICO) {
      resolvido = { cubism, mascaras, buffers: 1, agiu: false };
      ultimo = `${LOG} ${mascaras} mascaras cabem no teto padrao (${TETO_BUFFER_UNICO}) — nada a fazer`;
      console.log(ultimo);
      return;
    }

    const buffers = Math.ceil(mascaras / POR_BUFFER);
    const gl = pegarGL();
    if (!gl) {
      ultimo = `${LOG} contexto WebGL ainda nao disponivel; tento de novo`;
      return; // sem marcar resolvido: tenta no proximo ciclo
    }

    try {
      lapp.deleteRenderer();
      lapp.createRenderer(buffers);

      /* Renderizador novo nasce SEM textura e sem alpha pré-multiplicado.
       *
       * `setupTextures()` do LAppModel só executa o corpo se `_state == 20`
       * — a fase de carga. Modelo já pronto está em 22, então chamar direto
       * não fazia nada: nem `bindTexture`, nem `setIsPremultipliedAlpha`.
       * Sintoma: `[CSM][E]NoPremultipliedAlpha is not allowed` em loop.
       *
       * Voltar `_state` para 20 e zerar a contagem é o mesmo caminho que o
       * SDK percorre no primeiro carregamento; o gerenciador de textura tem
       * cache por caminho, então nada é rebaixado da rede. O próprio
       * callback de cada textura devolve `_state` a 22 quando todas entram.
       */
      lapp._textureCount = 0;
      lapp._state = 20;
      lapp.setupTextures();
      lapp.getRenderer().startUp(gl);
      const depois = contarMascaras(lapp.getRenderer());
      const teto = POR_BUFFER * buffers;
      resolvido = { cubism, mascaras: depois, buffers, agiu: true, teto };
      ultimo =
        `${LOG} ${depois} mascaras > teto padrao ${TETO_BUFFER_UNICO}; ` +
        `renderizador recriado com ${buffers} buffers (teto ${teto})`;
      console.log(ultimo);
    } catch (erro) {
      ultimo = `${LOG} falhou ao recriar renderizador: ${erro && erro.message}`;
      console.warn(ultimo, erro);
      resolvido = { cubism, mascaras, buffers: 1, agiu: false, erro: String(erro) };
    }
  }

  // The model is selected at page load. Retry only until its renderer is known;
  // a permanent timer adds work after the four-buffer layout is already ready.
  function tentarAteResolver() {
    tentar();
    if (!resolvido) setTimeout(tentarAteResolver, 1000);
  }
  tentarAteResolver();

  /* Mediu ou não mediu: o canvas pintou pixel do modelo?
   *
   * Screenshot da janela não responde isso, porque o fundo é uma DIV ATRÁS
   * do canvas: canvas 100% transparente e canvas com a Célia desenhada
   * produzem screenshots parecidos pra quem olha rápido. A única resposta
   * honesta é ler o buffer de desenho.
   *
   * `preserveDrawingBuffer` é falso neste app, então `readPixels` só vale
   * DENTRO do quadro, antes da composição. Por isso a leitura acontece num
   * `requestAnimationFrame` — registrado depois do app, logo executa depois
   * do desenho dele no mesmo quadro.
   */
  let pintura = null;
  function medirPintura() {
    const gl = pegarGL();
    if (!gl) return;
    requestAnimationFrame(() => {
      try {
        const w = gl.drawingBufferWidth;
        const h = gl.drawingBufferHeight;
        // Amostra em grade: ler o buffer inteiro a cada medição sairia caro.
        const passo = 4;
        const px = new Uint8Array(w * h * 4);
        gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
        let opacos = 0;
        let amostras = 0;
        for (let y = 0; y < h; y += passo) {
          for (let x = 0; x < w; x += passo) {
            amostras++;
            if (px[(y * w + x) * 4 + 3] > 16) opacos++;
          }
        }
        pintura = {
          buffer: `${w}x${h}`,
          amostras,
          opacos,
          porcento: +((opacos / amostras) * 100).toFixed(2),
          veredito: opacos / amostras > 0.01 ? "PINTOU" : "CANVAS_VAZIO",
          em: new Date().toISOString(),
        };
        console.log(`${LOG} pintura: ${pintura.veredito} (${pintura.porcento}% opaco)`);

        /* Exportar a imagem só quando pedida por `?provaPintura=1`.
         *
         * O `--screenshot` do headless NÃO serve pra este canvas: sem
         * `preserveDrawingBuffer`, ele captura um quadro já descartado e
         * devolve transparente. Medi as duas coisas no mesmo instante — o
         * readPixels acusou 5,11% opaco e o screenshot veio vazio. Quem
         * mente é o screenshot.
         *
         * Aqui os mesmos pixels do readPixels vão pra um canvas 2D (com a
         * inversão de Y que o WebGL exige) e viram PNG de verdade.
         */
        if (!pintura.dataUrl && /(?:\?|&)provaPintura=1/.test(location.search)) {
          const c2 = document.createElement("canvas");
          c2.width = w;
          c2.height = h;
          const ctx = c2.getContext("2d");
          const img = ctx.createImageData(w, h);
          /* O buffer sai com alpha PRÉ-MULTIPLICADO — o renderizador Cubism
           * exige isso (`NoPremultipliedAlpha is not allowed`). `ImageData`
           * espera alpha reto. Copiar cru deixa tudo lavado, esbranquiçado:
           * foi o que aconteceu na primeira captura e eu quase reportei como
           * "cor do modelo errada". Desfazer a pré-multiplicação é o que
           * torna a imagem comparável ao que o olho vê na tela.
           */
          for (let y = 0; y < h; y++) {
            const origem = (h - 1 - y) * w * 4; // WebGL desenha de baixo pra cima
            const destino = y * w * 4;
            for (let x = 0; x < w * 4; x += 4) {
              const a = px[origem + x + 3];
              const k = a === 0 ? 0 : 255 / a;
              img.data[destino + x] = Math.min(255, px[origem + x] * k);
              img.data[destino + x + 1] = Math.min(255, px[origem + x + 1] * k);
              img.data[destino + x + 2] = Math.min(255, px[origem + x + 2] * k);
              img.data[destino + x + 3] = a;
            }
          }
          ctx.putImageData(img, 0, 0);
          pintura.dataUrl = c2.toDataURL("image/png");
        }
      } catch (erro) {
        pintura = { erro: String(erro) };
      }
    });
  }
  const provaPinturaAtiva = /(?:\?|&)provaPintura=1/.test(location.search);

  window.celiaMascaras = {
    aplicar: tentar,
    medirPintura,
    estado: () => {
      // `_state` 20 = carregando textura, 21 = aguardando callbacks,
      // 22 = pronto (é o único em que LAppModel.update() não sai na porta).
      // Sem espelhar isto eu não teria como distinguir "modelo invisível
      // por bug antigo" de "modelo invisível porque EU o deixei em 21".
      let st = null, tex = null, texturasNoRenderizador = null;
      try {
        const a = typeof window.getLAppAdapter === "function" ? window.getLAppAdapter() : null;
        const l = a && a.getModel();
        if (l) {
          st = l._state;
          tex = l._textureCount;
          const r = l.getRenderer();
          const t = r && r._textures;
          texturasNoRenderizador = t && typeof t.getSize === "function" ? t.getSize() : null;
        }
      } catch (_) {}
      return {
        resolvido: !!resolvido,
        mascaras: resolvido ? resolvido.mascaras : null,
        buffers: resolvido ? resolvido.buffers : null,
        teto: resolvido ? resolvido.teto || TETO_BUFFER_UNICO : null,
        recriouRenderizador: resolvido ? !!resolvido.agiu : false,
        estadoLAppModel: st,
        prontoParaDesenhar: st === 22,
        texturasCarregadas: tex,
        texturasNoRenderizador,
        pintura,
        mensagem: ultimo,
      };
    },
  };

  // Framebuffer readback synchronizes the GPU with JavaScript and allocates a
  // full-frame buffer. Keep it for an explicit proof run, never normal use.
  if (provaPinturaAtiva) {
    medirPintura();
    let espelho = null;
    setInterval(() => {
      if (!document.body) return;
      if (!espelho) {
        espelho = document.createElement("div");
        espelho.id = "celia-mascaras-prova";
        espelho.hidden = true;
        document.body.appendChild(espelho);
      }
      espelho.textContent = JSON.stringify(window.celiaMascaras.estado());
    }, 1000);
  }

  console.log(`${LOG} instalado`);
})();
