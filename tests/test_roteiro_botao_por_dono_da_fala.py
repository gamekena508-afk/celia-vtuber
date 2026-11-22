"""Cada fala tem UM botão, e ele é escolhido pelo dono da fala.

Plano do dono, 2026-10-01:

    👤 Criador → [AVANÇAR]
    🤖 Célia  → [▶ TOCAR] → 🔊 TOCANDO… → avança sozinho no fim do áudio

As ações já existiam no backend (`forward_line`, `play_expected`); o que faltava
era a UI contextual. O estado da fala vem de `snapshot.celia.state`: `falando`
enquanto o áudio roda, e `frontend-playback-complete` devolve a `idle`.

OS CINCO BUGS QUE O DONO MANDOU PREVENIR, e como cada um é barrado:

1. clique duplo tocando duas Célias
   -> guarda local `falaPedida` + disabled imediato no clique. O `disabled` do
      snapshot chega tarde demais; dois cliques cabem nessa janela.
2. avançar antes do áudio terminar
   -> o botão do criador fica disabled enquanto `celiaFalando`.
3. áudio terminar mas o roteiro não andar
   -> a borda `falando -> não-falando` dispara `forward_line`.
4. voltar para uma fala da Célia e ela tocar sozinha
   -> o avanço exige `falaPedida === chaveAtiva`. Sem pedido, nada acontece.
5. "Avançar" aparecer numa fala da Célia
   -> o botão é escolhido por `DONOS_CELIA`, nunca os dois na mesma linha.

FLUXO PROVADO em headless com o index.html e o app.js REAIS (socket espionado):

    1 Criador ativo ................. 'AVANÇAR'  forward_line  disabled=False
    2 Célia ativa ................... '▶ TOCAR'  play_expected disabled=False
    3 três cliques rápidos .......... 1 play_expected enviado
    4 Célia falando ................. '🔊 TOCANDO…' disabled=True
    5 fim do áudio .................. 1 forward_line
    6 snapshot idle repetido ........ 0 forward_line extra
    7 voltar sem pedir .............. 0 play_expected, 0 forward_line
    8 Criador com Célia falando ..... 'AVANÇAR' disabled=True
    9 AVANÇAR em linha da Célia ..... 0
"""
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
APP = RAIZ / "frontend" / "roteiro-live" / "app.js"
CSS = RAIZ / "frontend" / "roteiro-live" / "style.css"


def _app() -> str:
    return APP.read_text(encoding="utf-8")


def test_o_botao_e_escolhido_pelo_dono_da_fala():
    app = _app()
    assert "const DONOS_CELIA = new Set([\"celia\", \"célia\"]);" in app
    assert 'botao.textContent = "AVANÇAR";' in app
    assert 'botao.textContent = "▶ TOCAR";' in app
    assert 'botao.dataset.acao = "forward_line";' in app
    assert 'botao.dataset.acao = "play_expected";' in app


def test_avancar_nunca_aparece_numa_fala_da_celia():
    """Bug 5: avançar numa fala dela pularia o áudio sem ninguém ouvir."""
    app = _app()
    corpo = app.split("function botaoDaLinhaAtiva(", 1)[1].split("\n  }", 1)[0]
    # O ramo do AVANÇAR está atrás da negação de DONOS_CELIA e retorna ali.
    assert "if (!DONOS_CELIA.has(speakerKey)) {" in corpo
    ramo_criador = corpo.split("if (!DONOS_CELIA.has(speakerKey)) {", 1)[1].split("return botao;", 1)[0]
    assert 'botao.textContent = "AVANÇAR";' in ramo_criador
    assert "play_expected" not in ramo_criador


def test_clique_duplo_nao_toca_duas_celias():
    """Bug 1: o disabled do snapshot chega tarde; precisa de guarda local."""
    app = _app()
    # ERRO MEU, corrigido: a primeira versão cortava o trecho no primeiro
    # "return;", que é exatamente o `if (falaPedida) return;` procurado — a
    # asserção ficava olhando um pedaço vazio. O código estava certo o tempo
    # todo (headless: três cliques -> um play_expected). Agora o corte é no
    # fim do ramo, pelo `send` que o encerra.
    trecho = app.split('if (botao.dataset.acao === "play_expected") {', 1)[1]
    trecho = trecho.split('send({ action: "play_expected" });', 1)[0]
    assert "if (falaPedida) return;" in trecho, "sumiu a guarda contra clique duplo"
    assert "falaPedida = botao.dataset.linha;" in trecho, "a guarda não é armada"
    assert "botao.disabled = true;" in trecho, "o botão não bloqueia no próprio clique"


def test_avancar_fica_bloqueado_enquanto_a_celia_fala():
    """Bug 2: avançar no meio do áudio atropela a fala dela."""
    app = _app()
    assert "botao.disabled = celiaFalando;" in app


def test_fim_do_audio_avanca_o_roteiro():
    """Bug 3: o áudio acabar e o roteiro ficar parado."""
    app = _app()
    assert 'const terminouDeFalar = estadoCeliaAnterior === "falando" && !celiaFalando;' in app
    assert 'send({ action: "forward_line" });' in app


def test_avanco_automatico_so_vale_para_a_fala_que_eu_pedi():
    """Bug 4: voltar para uma fala dela não pode tocar nem avançar sozinho."""
    app = _app()
    assert "if (terminouDeFalar && falaPedida && falaPedida === chaveAtiva) {" in app, (
        "o avanço automático deixou de exigir que o play tenha sido pedido; "
        "voltar para uma fala da Célia passaria a avançar sozinho"
    )
    # E reage à BORDA, não ao estado parado: senão dispararia a cada snapshot.
    assert "estadoCeliaAnterior = estadoCelia;" in app


def test_navegacao_manual_cancela_a_espera():
    """Navegou no meio do áudio: a espera perdeu o dono."""
    app = _app()
    assert app.count("falaPedida = null;") >= 4, (
        "faltou limpar falaPedida em algum caminho de navegação"
    )
    assert "else if (falaPedida && chaveAtiva && falaPedida !== chaveAtiva) {" in app


def test_o_botao_tem_estilo_proprio_e_cor_da_celia_ao_tocar():
    css = CSS.read_text(encoding="utf-8")
    assert ".script-line .line-action {" in css
    assert ".script-line .line-action.tocando" in css
    assert "var(--celia)" in css.split(".line-action.tocando", 1)[1].split("}", 1)[0]
