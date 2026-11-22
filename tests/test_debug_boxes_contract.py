"""As caixas de debug NÃO podem virar uma segunda superfície de legenda.

docs/CONTRATO_LEGENDA_PET.md: o Pet tem exatamente UMA legenda
(frontend/celia-legenda-embutida.js). As caixas de debug são um overlay da página,
desligado por padrão, que só lê o stream de observabilidade que já existe.
"""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BOXES = RAIZ / "frontend" / "celia-debug-boxes.js"
INDEX = RAIZ / "frontend" / "index.html"

PROIBIDOS = (
    "BrowserWindow",
    "CELIA_SEPARATE_SUBTITLE",
    "SUBTITLE_URL",
    "subtitle:",
    "toggle_subtitles",
    "pet:toggle-subtitles",
    "desktop_subtitle.json",
    "obs_subtitles",
    "celia-legenda-embutida",
)


def test_caixas_de_debug_nao_reintroduzem_segunda_legenda():
    codigo = re.sub(r"/\*.*?\*/", "", BOXES.read_text(encoding="utf-8"), flags=re.S)
    for simbolo in PROIBIDOS:
        assert simbolo not in codigo, f"{simbolo} não pode aparecer em celia-debug-boxes.js"


def test_caixas_so_leem_o_stream_existente_e_ligam_sozinhas_como_o_debug_flow():
    codigo = BOXES.read_text(encoding="utf-8")
    assert "/debug/observability/events" in codigo
    assert "new WebSocket" not in codigo  # nenhuma conexão nova além do SSE
    # Ligadas por padrão; só somem se o dono pediu (?debugboxes=0 / desligar()).
    assert 'getItem(CHAVE) !== "off"' in codigo
    assert 'debugboxes") === "0"' in codigo


def test_index_carrega_as_caixas_com_versao_para_invalidar_cache():
    html = INDEX.read_text(encoding="utf-8")
    assert re.search(r'celia-debug-boxes\.js\?v=[\w-]+', html)
    assert re.search(r'celia-visao-frame\.js\?v=visao[\w-]+', html)
