"""O roteiro do vídeo "XTTS no Windows" tem de existir como roteiro, não só como Markdown.

Causa medida em 2026-10-18: o seletor de `/roteiro/` lista `roteiros/*.json` com `id` e `beats`
(`core/roteiro_catalogo.roteiros_em_disco`). `ROTEIRO_YOUTUBE_XTTS_WINDOWS.md` é documento de
leitura: nenhuma rota ou manifesto o lê, então ele nunca podia aparecer. O `.md` continua sendo
a fonte; o `.json` é o formato que o teleprompter entende (Criador = fala obrigatória, Célia e
rubricas = opcionais, como `celia_3d_passo_a_passo.json`).
"""
import json
import re
from pathlib import Path

from celia.core import roteiro_catalogo as cat

RAIZ = Path(__file__).resolve().parents[1]
MD = RAIZ / "roteiros" / "ROTEIRO_YOUTUBE_XTTS_WINDOWS.md"
JS = RAIZ / "roteiros" / "roteiro_youtube_xtts_windows.json"
ID = "xtts-windows-youtube-2026-10-18"


def _roteiro():
    return json.loads(JS.read_text(encoding="utf-8"))


def test_catalogo_lista_o_roteiro_xtts_com_falas(monkeypatch):
    monkeypatch.setattr(cat, "ROTEIROS_DIR", RAIZ / "roteiros")
    item = next(i for i in cat.roteiros_em_disco() if i["id"] == ID)
    assert item["titulo"].startswith("Como Gerar Voz com IA Localmente no Windows")
    assert item["beats"] == item["beats_com_fala"] == 11
    assert item["rascunho"] is False


def test_beats_formam_uma_cadeia_valida_a_partir_do_start():
    d = _roteiro()
    ids = [b["id"] for b in d["beats"]]
    assert len(ids) == len(set(ids)) and d["start"] == ids[0]
    for atual, proximo in zip(d["beats"], d["beats"][1:]):
        assert atual["next"] == [proximo["id"]]
    assert d["beats"][-1]["next"] == []


def test_toda_fala_de_criador_e_celia_existe_literalmente_no_markdown():
    md = re.sub(r"\s+", " ", MD.read_text(encoding="utf-8").replace("**", ""))
    falas = [
        l["text"]
        for b in _roteiro()["beats"]
        for l in b["lines"]
        if l["speaker"] in ("Criador", "Célia")
    ]
    assert len(falas) >= 50
    ausentes = [t for t in falas if re.sub(r"\s+", " ", t) not in md]
    assert not ausentes, ausentes[:3]


def test_criador_e_obrigatorio_e_celia_e_rubrica_sao_opcionais():
    for b in _roteiro()["beats"]:
        criador = [l for l in b["lines"] if l["speaker"] == "Criador"]
        assert criador and all(not l.get("optional") for l in criador)
        outros = [l for l in b["lines"] if l["speaker"] != "Criador"]
        assert all(l.get("optional") is True for l in outros)


def test_controle_negativo_markdown_sem_json_nao_aparece(tmp_path, monkeypatch):
    # só o .md numa pasta de roteiros: o catálogo não pode enxergá-lo (é o defeito original)
    (tmp_path / MD.name).write_text(MD.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(cat, "ROTEIROS_DIR", tmp_path)
    assert cat.roteiros_em_disco() == []
