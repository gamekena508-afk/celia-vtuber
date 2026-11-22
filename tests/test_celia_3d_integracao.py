"""Contratos estaticos da integracao 3D na interface (ordem do Pai, 2026-10-07). Rapidos, sem navegador.
A prova real (nada renderiza antes da escolha; 3D sem Live2D; menu so com aprovadas) e tests/celia_3d_integracao_roteiro.py (Chrome contra :12393)."""
import json
import re
from pathlib import Path

FRONT = Path(__file__).resolve().parents[1] / "frontend"


def _ler(nome):
    return (FRONT / nome).read_text(encoding="utf-8")


def test_portao_e_o_primeiro_script_e_o_bundle_fica_adiado():
    html = _ler("index.html")
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert "celia-escolha.js" in scripts[0], "o portao tem de ser o primeiro script do <head>"
    assert not re.search(r'<script[^>]*type="module"[^>]*assets/main-', html), "o bundle compilado nao pode carregar antes da escolha"
    assert re.search(r'id="celia-bundle"[^>]*type="application/x-celia-adiado"[^>]*data-src="\./assets/main-', html)


def test_index_nao_carrega_nenhum_script_3d_direto():
    assert "celia-3d-" not in _ler("index.html"), "o 3D so entra pelo portao, depois da escolha"


def test_portao_pet_e_atalho_explicito_e_sem_persistencia():
    js = _ler("celia-escolha.js")
    # R15 (2026-10-07, ordem do Pai): o Pet NAO e mais sempre 2D; segue o corpo publicado em /bridge/body (2D se nao houver resposta).
    assert 'Q.has("pet")' in js and "iniciarPet()" in js and "/bridge/body" in js and '|| "2d"' in js
    assert '["2d", "3d"].includes(Q.get("corpo"))' in js
    assert "localStorage" not in js, "a escolha e a cada abertura: nada e lembrado"
    assert "model_info" in js and "set-model-and-conf" in js


def test_aprovadas_so_tem_acoes_marcadas_v():
    dados = json.loads(_ler("celia-3d-aprovadas.json"))
    assert len(dados["aprovadas"]) == len(set(dados["aprovadas"])) == 17
    assert not {"andar", "correr", "mortal", "paz", "comemorar", "cumprimentar"} & set(dados["aprovadas"])  # marcadas ?
    assert not [k for k in dados["aprovadas"] if k in ("kk_soco", "kk_espada_corte", "kk_pistola_mirar")]
    exportado = Path.home() / "Downloads" / "celia3d-curadoria.json"
    if exportado.exists():
        assert sorted(json.loads(exportado.read_text(encoding="utf-8"))["aprovadas"]) == sorted(dados["aprovadas"])


def test_modo_app_da_bancada_e_o_portao_de_acoes_do_corpo():
    b = _ler("celia-3d-bancada.js")
    assert 'const APP = Q.has("app")' in b and "api.corpo.acoesPermitidas = APROVADAS" in b
    assert "e.source !== window.parent" in b and "APROVADAS.has(m.k)" in b
    c = _ler("celia-3d-corpo.js")
    assert 'ACOES[nome].tipo !== "micro" && !this.acoesPermitidas.has(nome)' in c
    i = _ler("celia-3d-interface.js")
    assert "e.source !== quadro.contentWindow" in i and "#celia-3d-menu" in i


def test_r15_interruptor_legenda_e_painel_de_fluxo():
    idx = _ler("index.html")
    assert "celia-corpo-interruptor.js" in idx
    sw = _ler("celia-corpo-interruptor.js")
    assert 'role", "switch"' in sw and "celiaEscolha.trocar" in sw and "#d6403f" in sw and "#2faa54" in sw
    assert 'Q.has("pet")' in sw, "o interruptor nunca aparece no Pet"
    flow = _ler("celia-debug-flow.js")
    assert "document.body.appendChild" not in flow and "montarEm" in flow, "o painel de fluxo nao flutua mais na tela"
    assert "montarEm" in _ler("celia-runtime-settings.js")
    pal = _ler("celia-caixotes-palco.js")
    assert "legendaVisivel: false" in pal and '"Thinking..."' in pal


def test_r15_pet_nativo_3d_fica_fixo_e_o_palco_deixa_passar():
    assert "PET_NATIVO ? null" in _ler("celia-3d-interface.js")
    assert '"celia-3d-quadro"' in _ler("celia-palco.js")


def test_r16_cores_emocao_e_escolha_limpa():
    assert "celia-mic-cor.js" in _ler("index.html")
    mic = _ler("celia-mic-cor.js")
    assert "#12306e" in mic and "#58b7ff" in mic and "-12-12z" in mic and "Raise hand" in mic
    assert "#17181d" in _ler("celia-corpo-interruptor.js"), "botao do interruptor escuro"
    assert "emoção: " in _ler("celia-debug-boxes.js") and '"celia-emotion"' in _ler("celia-debug-boxes.js")
    esc = _ler("celia-escolha.js")
    assert "#celia-debug-boxes" in esc and "#celia-body-hud" in esc and "data-celia-corpo" in esc
    assert "setInterval(positionHud" in _ler("celia-live2d-body.js") or "setInterval(positionHud, 1000)" in _ler("celia-live2d-body.js")


def test_r17_pet_e_interface_exclusivos():
    idx = _ler("index.html")
    assert "celia-modo-menu.js" in idx
    pet = _ler("celia-browser-pet.js")
    assert "/bridge/desktop-pet/state" in pet and "state.alive" in pet and "adoptRunningPet" in pet
    assert 'postModo("janela")' in pet and 'postModo("pet")' in pet and "petSeenOn = false" in pet
    esc = _ler("celia-escolha.js")
    assert "/bridge/mode" in esc and "celiaPetNative.close" in esc and "m.em > carga" in esc
    menu = _ler("celia-modo-menu.js")
    assert '"Live Mode"' in menu and '"Pet Mode"' in menu and "setMode" not in menu.replace("nao chama setMode", "")


def test_r18_configuracoes_limpas_sem_tocar_em_opcoes():
    assert "celia-config-extra.js" in _ler("index.html")
    ex = _ler("celia-config-extra.js")
    assert "Voz atual" in ex and "/debug/observability/runtime" in ex and "somente leitura" in ex.lower()
    assert ("\\u00c2\\u00a9" in ex) or ("\u00c2\u00a9" in ex), "reparo exato do copyright quebrado (escrito como escape ou como caractere)"
    assert "addEventListener(\"click\"" in ex and ".click()" not in ex, "so le: nunca clica nem salva"
    rt = _ler("celia-runtime-settings.js")
    assert "Histórico recente" in rt and "Revisor do guardião (LLM)" not in rt and "Historico" not in rt
