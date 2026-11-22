"""Defesa conservadora contra falsa lembrança de vídeo na saída final.

Não é um juiz geral de fatos. Só atua quando o recuperador já indicou
explicitamente ausência da cena NOS VÍDEOS e o pedido é sobre gravação.
Nunca apaga o corpus e não nega a existência do evento fora desses vídeos.
"""
from __future__ import annotations

import re
import unicodedata


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in value if not unicodedata.combining(c)).lower()


_VIDEO = re.compile(r"\b(video|videos|episodio|episodios|gravacao|gravamos|gravou|gravei|"
                    r"filmou|filmamos|filmei|filmagem|filmagens)\b")
_UNCERTAIN = re.compile(
    r"\b(nao (?:me )?(?:lembro|recordo|achei|encontrei|tenho|sei|posso confirmar)|"
    r"sem registro|nenhum registro|nao consta|nao ha registro|nao aparece|"
    r"nao tenho certeza|nao consigo confirmar)\b"
)


def guard_unverified_video_memory(
    request: str,
    answer: str,
    video_context: str,
    *,
    independent_evidence: bool = False,
) -> str:
    """Evita afirmar uma filmagem não localizada; outras fontes ficam livres.

    Sem a indicação explícita de ausência, não faz nada. Respostas que já
    admitem incerteza também permanecem intactas. Não interfere em ferramentas
    ou em respostas sobre fatos gerais.
    """
    if not answer or "LEMBRANÇA AUSENTE NOS VÍDEOS" not in video_context:
        return answer
    if independent_evidence or not _VIDEO.search(_norm(request)):
        return answer
    if _UNCERTAIN.search(_norm(answer)):
        return answer
    return (
        "Não encontrei esse episódio nas minhas lembranças de vídeo. "
        "Se foi em outra conversa ou gravação, me dá uma pista para conferir."
    )
