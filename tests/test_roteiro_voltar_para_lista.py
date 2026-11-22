"""Do teleprompter dá para voltar à lista sem perder o roteiro.

Pedido do dono em 2026-10-01: depois de entrar no teleprompter não havia saída
óbvia para escolher outro roteiro, e a parte de baixo já está cheia de
controles. O caminho passa a ser:

    Lista → [USAR] → Teleprompter → [← ROTEIROS] → Lista

A alternância reutiliza o mecanismo que já existia — `snapshot.active`
controlando o `hidden` de #inactive e #timeline. Voltar é NAVEGAÇÃO, não
mudança de estado: nada é enviado ao servidor, o roteiro continua carregado e
ativo e o take segue de pé.

O risco real era o snapshot: chegam vários por segundo, e sem um flag local o
primeiro deles jogaria o dono de volta ao teleprompter no mesmo instante em que
ele saísse. Daí `mostrandoLista`.

Fluxo provado em headless com o index.html e o app.js REAIS (WebSocket e fetch
dublados):

    1. no teleprompter          lista=False teleprompter=True  botão=True
    2. clicou em ← ROTEIROS     lista=True  teleprompter=False botão=False
    3. chega snapshot ativo     lista=True  teleprompter=False  <- não expulsa
    4. escolheu outro + USAR    lista=False teleprompter=True  botão=True
    5. Esc                      lista=True  teleprompter=False botão=False
"""
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
APP = RAIZ / "frontend" / "roteiro-live" / "app.js"
INDEX = RAIZ / "frontend" / "roteiro-live" / "index.html"
CSS = RAIZ / "frontend" / "roteiro-live" / "style.css"


def test_o_botao_existe_no_topo_e_nao_embaixo():
    """O dono pediu no topo: a parte de baixo já tem controles demais."""
    index = INDEX.read_text(encoding="utf-8")
    assert 'id="back-to-list"' in index, "o botão de voltar sumiu"
    topo = index.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]
    assert 'id="back-to-list"' in topo, "o botão saiu do topbar"
    # Canto superior ESQUERDO: primeiro do topbar, antes do status de conexão.
    assert topo.index('id="back-to-list"') < topo.index('id="connection"')


def test_o_rotulo_e_uma_seta_com_roteiros():
    index = INDEX.read_text(encoding="utf-8")
    assert "← ROTEIROS" in index


def test_voltar_nao_fala_com_o_servidor():
    """Não pode apagar roteiro nem resetar progresso."""
    app = APP.read_text(encoding="utf-8")
    corpo = app.split("function abrirLista()", 1)[1].split("\n  }", 1)[0]
    for proibido in ("fetch(", "pickerAction(", "send(", "/bridge/", "deactivate"):
        assert proibido not in corpo, (
            f"voltar à lista passou a chamar {proibido!r}; isso mexe no estado "
            "da gravação, e o pedido era só de navegação"
        )


def test_snapshot_ativo_nao_expulsa_quem_esta_na_lista():
    """O defeito que o flag existe para impedir."""
    app = APP.read_text(encoding="utf-8")
    assert "const verLista = !snapshot.active || mostrandoLista;" in app
    assert '$("inactive").hidden = !verLista;' in app
    assert '$("timeline").hidden = verLista;' in app
    assert '$("inactive").hidden = Boolean(snapshot.active);' not in app, (
        "voltou a decidir a tela só pelo snapshot; o próximo evento expulsaria "
        "o dono da lista"
    )


def test_escolher_um_roteiro_sai_da_lista():
    app = APP.read_text(encoding="utf-8")
    trecho = app.split("if (alvo.dataset.pick) {", 1)[1].split("} else", 1)[0]
    assert "mostrandoLista = false;" in trecho, (
        "clicar em USAR não limpa o flag; o dono ficaria preso na lista"
    )


def test_esc_faz_o_mesmo_e_nao_rouba_tecla_de_campo():
    app = APP.read_text(encoding="utf-8")
    assert 'event.key !== "Escape"' in app
    assert 'alvo.tagName === "INPUT"' in app and "isContentEditable" in app, (
        "o Esc precisa ignorar quem está digitando"
    )
    assert "abrirLista();" in app


def test_o_visual_existente_foi_mantido():
    """Sem cor nova, sem caixa, sem modal: mesma gramática do CONTROLES."""
    css = CSS.read_text(encoding="utf-8")
    assert ".back-to-list {" in css
    assert "var(--muted)" in css.split(".back-to-list {", 1)[1].split("}", 1)[0]
    app = APP.read_text(encoding="utf-8")
    for proibido in ("<dialog", "showModal", "createElement(\"dialog\")"):
        assert proibido not in app, "apareceu modal; o dono pediu que não"
