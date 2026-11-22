"""Texto de infraestrutura nunca é fala da Célia.

Medido na tentativa de gravação de 2026-10-21 (14:21-14:25): 9 das 15 chamadas reais de conversa enviaram ao modelo
``{'role': 'assistant', 'content': 'Error calling the chat endpoint: todos os provedores da fila falharam.'}`` como se fosse o que ela tinha dito, e o
aviso "🔴 Todas as rotas de IA estão indisponíveis" ficou no histórico como mensagem de IA. A causa de código: depois da geração o agente fazia
``_add_message(complete_response, "assistant")`` mesmo quando o texto era a falha do provedor.

Regra: o registro ORIGINAL (arquivo de histórico, trace de auditoria) não é tocado. O filtro vale na ENTRADA da memória e na MONTAGEM do contexto
(memória de diálogo, evento "Célia disse", recuperação de contexto e exemplos de estilo).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

# Nomes de remetente que são o sistema, nunca a personagem (core/avisos.py grava com name="Aviso").
NOMES_DE_SISTEMA = frozenset({"aviso", "sistema", "system", "debug", "infra"})

# Início do texto (sem acento, minúsculo). Só padrões vistos nos registros reais ou emitidos pelo próprio código.
_PREFIXOS = (
    "error calling the chat endpoint",
    "provider failure",
    "as ferramentas estao temporariamente indisponiveis",
    "todas as rotas de ia estao indisponiveis",
    "o reconhecimento de voz esta indisponivel",
    "minha conexao de conversa esta ocupada",
    "conexao de conversa esta ocupada",
    "internal agent error:",
    "rate limit exceeded",
    "no active credentials",
    "the current request exceeds the configured context budget",
    "todos os provedores da fila falharam",
    "todas as chaves api",
    "todas as chaves de api",
    "all api keys",
    "nenhuma chave api",
)
# Aviso de infraestrutura formatado pelo avisos.py: bolinha de status + texto ("🔴 ...", "🟡 ...").
_BOLINHA = re.compile(r"^[\U0001F534\U0001F7E1\U0001F7E0⚠️\s]+")


def _dobrar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.lower().split())


def is_infrastructure_text(texto: Any, *, nome: str | None = None) -> bool:
    """True para erro de provedor, aviso de infraestrutura e diagnóstico. Fala real da Célia, mesmo curta ou ríspida, é False."""
    if nome and str(nome).strip().casefold() in NOMES_DE_SISTEMA:
        return True
    bruto = str(texto or "").strip()
    if not bruto:
        return False
    sem_bolinha = _BOLINHA.sub("", bruto)
    dobrado = _dobrar(sem_bolinha)
    return any(dobrado.startswith(p) for p in _PREFIXOS)


def _papel_e_nome(mensagem: dict[str, Any]) -> tuple[str, str | None]:
    return str(mensagem.get("role") or "").lower(), mensagem.get("name")


def clean_dialogue(mensagens: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cópia da lista sem as falas de IA que são infraestrutura. As falas do Criador ficam: o que ele disse continua sendo contexto mesmo quando
    ela não conseguiu responder. Não altera os dicionários originais nem a lista recebida."""
    limpas = []
    for m in mensagens:
        papel, nome = _papel_e_nome(m)
        conteudo = m.get("content")
        if isinstance(conteudo, list):
            conteudo = " ".join(str(p.get("text") or "") for p in conteudo if isinstance(p, dict))
        if papel in {"assistant", "ai"} and is_infrastructure_text(conteudo, nome=nome):
            continue
        limpas.append(m)
    return limpas


_LETTER_WORD = re.compile(r"[^\W\d_]+")


def remove_mixed_script_words(text: str) -> str:
    """Remove model artifacts such as a word half in Latin and half in Arabic."""
    def clean(match: re.Match[str]) -> str:
        word = match.group()
        latin = [unicodedata.name(char, "").startswith("LATIN ") for char in word]
        return "" if any(latin) and not all(latin) else word

    cleaned = _LETTER_WORD.sub(clean, text)
    if cleaned == text:
        return text
    cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"([.!?])\.+", r"\1", cleaned)
    return " ".join(cleaned.split()).lstrip(".,;:!? ")
