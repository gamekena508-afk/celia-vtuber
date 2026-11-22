"""O botão "Roteiro Live" do menu não pode escolher roteiro nem ativar gravação.

Defeito medido em 2026-09-30, relatado pelo dono: com o roteiro 3D carregado,
um clique em "Roteiro Live" fez o roteiro voltar para ia-vs-criador E sumir a
tela de escolha do painel.

Causa: `frontend/celia-roteiro-live.js` chamava `POST /bridge/roteiro/start`.
Essa rota faz duas coisas além de abrir o painel:

  1. força o perfil `roteiro`; o handler de `profile.changed` então chama
     `prepare_operational_script(behavior, None)`, que recarrega
     `roteiros/ia_vs_criador.json` do disco por cima do roteiro escolhido e
     sobrescreve `var/continuous_behavior_script.json`;
  2. marca a gravação como ativa, e o seletor de roteiro do painel só aparece
     quando `!snapshot.active` (`frontend/roteiro-live/app.js`).

Abrir a janela não é escolher roteiro. Quem escolhe é o dono, pelo painel, em
`POST /roteiro/scripts/select` — que usa o perfil `gravacao` e preserva.
"""
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
MENU_JS = RAIZ / "frontend" / "celia-roteiro-live.js"
PAINEL_JS = RAIZ / "frontend" / "roteiro-live" / "app.js"


def test_menu_nao_chama_a_rota_que_sobrescreve_o_roteiro():
    fonte = MENU_JS.read_text(encoding="utf-8")
    # Ignora o bloco de comentário que EXPLICA o defeito: só chamada real conta.
    chamadas = [
        linha
        for linha in fonte.splitlines()
        if "fetch(" in linha and not linha.strip().startswith(("*", "//", "/*"))
    ]
    assert chamadas, "o menu deixou de fazer qualquer fetch; a URL da LAN sumiu?"
    for linha in chamadas:
        assert "/bridge/roteiro/start-info" in linha or "/bridge/roteiro/start" not in linha, (
            "celia-roteiro-live.js voltou a usar POST /bridge/roteiro/start, "
            "que sobrescreve o roteiro do dono com ia_vs_criador e esconde o "
            f"seletor. Linha: {linha.strip()}"
        )


def test_menu_usa_a_rota_somente_leitura():
    fonte = MENU_JS.read_text(encoding="utf-8")
    assert "/bridge/roteiro/start-info" in fonte, (
        "o menu deve ler as lan_urls por GET /bridge/roteiro/start-info, "
        "que não toca em perfil nem em roteiro"
    )
    assert 'method: "POST"' not in fonte and "method:'POST'" not in fonte, (
        "o menu não pode mais fazer POST: abrir o painel é leitura"
    )


def test_menu_continua_abrindo_o_painel():
    fonte = MENU_JS.read_text(encoding="utf-8")
    assert 'window.open("/roteiro/"' in fonte, (
        "consertar a rota não pode ter tirado a função do botão"
    )


def test_seletor_do_painel_depende_de_gravacao_inativa():
    """Documenta a ligação: se isto mudar, o motivo do conserto some.

    O seletor só é carregado com `!snapshot.active`. É por isso que ativar a
    gravação ao abrir o painel escondia a escolha do dono.
    """
    fonte = PAINEL_JS.read_text(encoding="utf-8")
    # A forma literal mudou em 2026-10-01, quando entrou o botão "← ROTEIROS":
    # a condição passou a ser `verLista`, que é `!snapshot.active` OU o dono
    # ter pedido a lista de propósito. A ligação que este teste documenta
    # continua de pé: gravação ativa some com o seletor.
    assert "const verLista = !snapshot.active || mostrandoLista;" in fonte, (
        "a condição de visibilidade do seletor mudou; reavalie se o botão do "
        "menu ainda precisa ser somente leitura"
    )
    assert '$("inactive").hidden = !verLista;' in fonte


# ---------------------------------------------------------------------------
# CAUSA RAIZ — trocar de perfil não pode escolher roteiro.
#
# Ordem do dono, 2026-09-30: "no início é pra eu escolher o roteiro e que este
# bug de volta não se repita." Consertar só o botão do menu não bastava: QUALQUER
# caminho que levasse o perfil a `roteiro` recarregava ia_vs_criador.json do
# disco por cima da escolha dele.
# ---------------------------------------------------------------------------

def _comportamento_com(roteiro_id):
    """Dublê mínimo de ContinuousBehavior com um roteiro já carregado."""

    class _C:
        profile = "gravacao"
        carregado = {}

        class script:
            @staticmethod
            def export():
                return {
                    "id": roteiro_id,
                    "title": "meu roteiro",
                    "version": 1,
                    "start": "01",
                    "beats": [
                        {"id": "01", "title": "Um",
                         "lines": [{"speaker": "Criador", "text": "oi"}], "next": []}
                    ],
                }

        @staticmethod
        def load_script(dados):
            _C.carregado = dados
            return dados

        @staticmethod
        def set_script_position(_beat):
            return None

    return _C


def test_trocar_de_perfil_nao_sobrescreve_o_roteiro_escolhido():
    """O defeito que o dono relatou, na origem.

    `prepare_operational_script(behavior, None)` é o que os dois chamadores de
    troca de perfil usam. Com um roteiro já carregado, ele TEM de manter esse
    roteiro — e não recarregar o canônico do disco.
    """
    from celia.core.roteiro_live import RoteiroLiveRuntime

    runtime = RoteiroLiveRuntime()
    comportamento = _comportamento_com("celia-3d-passo-a-passo-2026-09-30")

    resultado = runtime.prepare_operational_script(comportamento, None)

    assert resultado["id"] == "celia-3d-passo-a-passo-2026-09-30", (
        "trocar de perfil sobrescreveu o roteiro escolhido pelo dono — "
        f"virou {resultado.get('id')!r}"
    )
    assert resultado["id"] != "ia-vs-criador-volta-do-canal"


def test_sem_roteiro_carregado_o_canonico_ainda_serve_de_partida():
    """CONTROLE NEGATIVO: a correção não pode deixar o sistema sem roteiro.

    Sem nada carregado, cair no canônico do disco continua certo — senão uma
    instalação limpa ficaria sem beat nenhum.
    """
    from celia.core.roteiro_live import RoteiroLiveRuntime

    runtime = RoteiroLiveRuntime()

    class _Vazio:
        profile = "gravacao"

        class script:
            @staticmethod
            def export():
                return {}

        @staticmethod
        def load_script(dados):
            return dados

        @staticmethod
        def set_script_position(_beat):
            return None

    resultado = runtime.prepare_operational_script(_Vazio(), None)
    assert resultado.get("beats"), "sem roteiro carregado o canônico tem de entrar"
