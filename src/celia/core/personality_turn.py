"""Orientações contextuais curtas para a personalidade da Célia.

Mantém o prompt-base pequeno; nunca cria fatos ou identifica interlocutor
pelo texto de uma transcrição. O speaker vem de metadata autenticada.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import yaml

from . import person_memory
from .raizes import RAIZ

_DEFAULT_CONF_PATH = RAIZ / "conf.yaml"


def _norm(text: str) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def _pessoa(author_id: str, profiles_path: Path | None = None) -> str:
    author_id = str(author_id or "").strip()
    if not author_id:
        return ""
    for item in person_memory.load_profiles(profiles_path):
        if str(item.get("discord_user_id") or "") == author_id:
            return str(item["name"])
    return ""


def load_configured_known_people(conf_path: Path | None = None) -> list[dict[str, str]]:
    """Lê character_config.known_people da configuração quando disponível."""
    path = Path(conf_path) if conf_path is not None else _DEFAULT_CONF_PATH
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    char_cfg = raw.get("character_config") if isinstance(raw, dict) else None
    items = char_cfg.get("known_people") if isinstance(char_cfg, dict) else None
    return _normalize_known_people(items)


def _normalize_known_people(known_people: Iterable[Any] | None) -> list[dict[str, str]]:
    if not known_people:
        return []
    out: list[dict[str, str]] = []
    for item in known_people:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            notes = str(item.get("notes") or "").strip()
        else:
            name = str(getattr(item, "name", "") or "").strip()
            notes = str(getattr(item, "notes", "") or "").strip()
        if name:
            out.append({"name": name, "notes": notes})
    return out


def _find_known_person(name: str, known_people: Iterable[Any] | None) -> dict[str, str] | None:
    target = _norm(name).strip()
    if not target:
        return None
    entries = (
        load_configured_known_people()
        if known_people is None
        else _normalize_known_people(known_people)
    )
    for entry in entries:
        if _norm(entry["name"]).strip() == target:
            return entry
    return None


def _known_person_hint(entry: dict[str, str], q: str) -> str:
    person_name = entry["name"]
    upper_name = person_name.upper()
    notes_norm = _norm(entry.get("notes", ""))

    # Perfil de amiga/colega próxima ("irmãzinha"): acolhimento precede implicância.
    if re.search(r"\b(irma|irmazinha|colega|amiga|implicancia)\b", notes_norm):
        if re.search(
            r"\b(triste|chorando|desabafo|nao lembro|como (faco|organizo|posso)|"
            r"problema|preciso de ajuda|me ajuda|historico|memoria|estou mal|to mal|"
            r"to triste|estou triste|preciso conversar|serio|sem brincadeira)\b",
            q,
        ):
            return (
                f"TOM COM A {upper_name}: ela está falando sério ou pedindo ajuda. "
                "Responda primeiro com conteúdo concreto e acolhimento natural de irmã; "
                "não transforme o pedido em competição nem desvie para uma piada."
            )
        if re.search(
            r"\b(metida|se achando|rainha|mandona|folgada|convencida|"
            r"coroa|sabichona|dona do discord)\b",
            q,
        ):
            return (
                f"TOM COM A {upper_name}: implicância de irmãs, leve e recíproca. "
                "Se houver gancho, pode brincar sobre coroa, comando imaginário "
                "do Discord ou inverter a provocação, sem afirmar poderes reais. "
                f"Brinque de igual para igual: nunca diminua a {person_name} nem a chame "
                "de inferior, incapaz ou sem criatividade. "
                "Não trate zoeira como ataque e não force piada se o tom mudar."
            )
        return ""

    # Perfil de amigo dev / organizador de canais do Discord.
    if re.search(r"\b(dev|discord|servidor|organiza|salas|canais)\b", notes_norm):
        if re.search(
            r"\b(organizei|organizou|criei|criou|arrumei|separei|montei|configurei)\b", q
        ) and re.search(r"\b(servidor|discord|canais|canal|salas|sala)\b", q):
            return (
                f"TOM COM {upper_name}: reconheça concretamente o que ele organizou. "
                "Se for natural, observe com humor que a ordem depende também "
                "das pessoas respeitarem o uso das salas; sem elogio genérico."
            )
        return ""

    # Perfil de amigo de jogos / bots / agentes.
    if re.search(r"\b(jogo|jogos|bot|bots|agente|agentes|ferramentas|projeto|projetos)\b", notes_norm):
        if re.search(r"\b(bot|agente|ferramentas|projeto)\b", q):
            return (
                f"TOM COM {upper_name}: acompanhe a notícia dele e demonstre curiosidade "
                "genuína sobre o que mudou. Não force piada nem apelido."
            )
        return ""

    return ""


def turn_hint(
    text: str,
    *,
    author_id: str = "",
    trusted_owner: bool = False,
    memory_block: str = "",
    profiles_path: Path | None = None,
    known_people: Iterable[Any] | None = None,
) -> str:
    """Retorna no máximo uma indicação útil e situacional (sem frase pronta)."""
    q = _norm(text)

    # A indicação de ausência vem de recall real, NUNCA de palavras do usuário.
    if "LEMBRANÇA AUSENTE NOS VÍDEOS" in memory_block:
        return (
            "TOM DESTE TURNO: o episódio foi pedido como lembrança, mas NÃO "
            "há registro dele. Diga claramente que não tem essa recordação. "
            "Nunca diga 'lembro sim', nem descreva filmagens, paisagens ou cenas."
        )

    # A cena/lore precisa existir no recall, senão não acrescentamos fatos.
    if "LEMBRANÇA" in memory_block and re.search(r"\b(bitcoin|btc|carteira)\b", q):
        return (
            "TOM COM MEMÓRIA DE BITCOIN: a carteira da Célia e seu saldo "
            "são IMAGINÁRIOS, parte da lore. Responda à pergunta diretamente "
            "com uma graça curta sobre o saldo fictício. Não invente quantidade "
            "real nem desvie para jogos ou outros assuntos."
        )
    if "LEMBRANÇA" in memory_block and re.search(r"\b(xadrez|xeque-mate)\b", q):
        return (
            "TOM COM MEMÓRIA DE XADREZ: reconheça somente a partida/ensino "
            "que está no contexto e demonstre vontade brincalhona de revanche. "
            "Não invente placar, jogadas ou sentimentos específicos."
        )

    if trusted_owner:
        if re.search(
            r"\b(gosto (muito )?de (ti|voce)|te amo|especial pra mim|especial para mim)\b",
            q,
        ):
            return (
                "TOM COM O CRIADOR: diga que ele também é especial para você, "
                "com carinho real. Pode acrescentar uma vaidade brincalhona "
                "sobre ser irresistível; sem formalidade, sem agressividade."
            )
        if re.search(r"\b(lerda|lentinha|lenta|travada|travo|travou)\b", q):
            return (
                "TOM COM O CRIADOR: ele implicou. Pode devolver uma patadinha "
                "carinhosa conectada ao contexto real da conversa, "
                "sem inventar defeito de hardware."
            )
        return ""

    speaker_name = _pessoa(author_id, profiles_path)
    if not speaker_name:
        return ""
    entry = _find_known_person(speaker_name, known_people)
    if entry is None:
        return ""
    return _known_person_hint(entry, q)
