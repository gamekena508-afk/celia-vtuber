"""No seletor de roteiros, STATUS e AÇÃO são coisas diferentes.

Defeito relatado pelo dono em 2026-10-01: o card do roteiro ativo mostrava a
palavra ATIVO duas vezes — no chip azul de status e dentro do botão de ação —
e o botão vinha desabilitado.

Pior que a redundância: isso prendia o dono. Desde que `restore_from_behavior()`
passou a RESTAURAR sem ATIVAR (ordem dele, para o seletor aparecer no início),
o estado normal depois de reiniciar é exatamente este: roteiro carregado — logo
com chip ATIVO — e gravação parada — logo com o seletor na tela. Com o botão
desabilitado não havia como entrar no teleprompter daquele roteiro.

`USAR` chama POST /roteiro/scripts/select, que carrega o roteiro e chama
`roteiro_live.activate()`. É o activate que faz o snapshot virar `active=true`,
e é isso que troca a tela do seletor para o teleprompter
(`$("inactive").hidden = Boolean(snapshot.active)`).

Prova de comportamento executada em headless com o index.html e o app.js reais,
WebSocket e fetch dublados:

    card ativo   -> chip ['ATIVO'] · botão 'USAR' · disabled=False · "ATIVO" 1x
    card inativo -> chip []        · botão 'USAR' · disabled=False · "ATIVO" 0x
    clique em USAR no card JÁ ativo -> POST /roteiro/scripts/select

Este arquivo guarda as regras de forma estática, que é o padrão desta árvore
para frontend não compilado.
"""
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
APP = RAIZ / "frontend" / "roteiro-live" / "app.js"
CSS = RAIZ / "frontend" / "roteiro-live" / "style.css"


def _app() -> str:
    return APP.read_text(encoding="utf-8")


def test_botao_de_acao_se_chama_usar():
    app = _app()
    assert 'pick.textContent = "USAR";' in app, (
        "o botão de ação do seletor deixou de se chamar USAR"
    )


def test_botao_nunca_e_desabilitado_por_estar_ativo():
    """A regra que destrava o dono: ativo não pode virar botão morto."""
    app = _app()
    assert "pick.disabled = pickerBusy;" in app
    assert "pick.disabled = Boolean(item.ativo)" not in app, (
        "o botão voltou a ser desabilitado porque o roteiro já está ativo; "
        "isso impede entrar no teleprompter depois de um reinício"
    )


def test_a_palavra_ativo_so_aparece_no_chip_de_status():
    """CONTROLE: o texto do botão não pode depender de item.ativo."""
    app = _app()
    assert 'pick.textContent = item.ativo ? "ATIVO"' not in app, (
        "o botão voltou a repetir ATIVO; o card mostraria a palavra duas vezes"
    )
    # O chip de status continua existindo, e é o único dono da palavra.
    assert 'tag.className = "picker-tag ativo";' in app
    assert 'tag.textContent = "ATIVO";' in app


def test_chip_ativo_continua_azul():
    """O dono pediu para manter o visual, inclusive o status azul."""
    css = CSS.read_text(encoding="utf-8")
    assert ".picker-tag.ativo { color: var(--criador); }" in css, (
        "o chip de status perdeu a cor azul"
    )


def test_usar_leva_para_a_rota_que_ativa_o_teleprompter():
    """Trocar o rótulo não bastava: o clique precisa levar ao teleprompter."""
    app = _app()
    assert 'pickerAction("/roteiro/scripts/select", {id: alvo.dataset.pick}' in app, (
        "o clique em USAR deixou de chamar a rota de seleção"
    )
    # E é o snapshot ativo que troca a tela do seletor para o teleprompter.
    # A linha literal mudou em 2026-10-01, quando entrou o botão "← ROTEIROS":
    # a decisão passou a considerar também `mostrandoLista`, para um snapshot
    # não expulsar quem foi olhar a lista de propósito. A propriedade protegida
    # é a mesma — quem manda na troca de tela é o estado ativo.
    assert "const verLista = !snapshot.active || mostrandoLista;" in app
    assert '$("inactive").hidden = !verLista;' in app
    assert '$("timeline").hidden = verLista;' in app


def test_a_rota_de_selecao_realmente_ativa():
    """Prova do outro lado: select chama activate, senão a tela nunca troca."""
    rotas = (RAIZ / "src" / "celia" / "roteiro_routes.py").read_text(encoding="utf-8")
    trecho = rotas.split('@router.post("/roteiro/scripts/select")', 1)[1]
    trecho = trecho.split("@router.post", 1)[0]
    assert "roteiro_live.activate(" in trecho, (
        "/roteiro/scripts/select deixou de ativar; o dono escolheria o roteiro "
        "e continuaria preso na tela de seleção"
    )
