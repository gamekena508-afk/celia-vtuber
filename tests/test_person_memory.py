"""Personas sociais por identidade estavel, sem inferir falante por nome."""
from __future__ import annotations

import json

from celia.core import person_memory as pm


def test_identidade_por_id_e_nome_canonico(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"profiles": [
        {"discord_user_id": "1", "name": "Amiga", "kind": "outra_ia", "relationship": "Irmãs.", "facts": []},
        {"discord_user_id": "2", "name": "AmigoJogos", "kind": "humano", "relationship": "Conhecido.", "facts": []},
        {"discord_user_id": "3", "name": "AmigoDev", "kind": "humano", "relationship": "Humano.", "facts": []},
    ]}), encoding="utf-8")
    r = pm.context_block("oi", speaker="Outro", discord_author_id="1", path=path)
    assert "Amiga (outra IA)" in r
    assert "AmigoJogos" not in r
    assert pm.context_block("oi", speaker="Amiga", discord_author_id="invalido", path=path) == ""
    text = pm.context_block("Quem é o AmigoJogos?", speaker="Criador", path=path)
    assert "AmigoJogos (humano)" in text
    assert "AmigoDev" not in text
    assert "AmigoDev (humano)" in pm.context_block("O AmigoDev fez o servidor.", path=path)
    assert "Amiga" not in pm.context_block("A Hari chegou.", path=path)


def test_nenhum_perfil_nao_quebra(tmp_path):
    assert pm.context_block("oi", path=tmp_path / "inexistente.json") == ""
