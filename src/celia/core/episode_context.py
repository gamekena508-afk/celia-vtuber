"""Memória de trabalho do episódio: o que está acontecendo na série, com fonte e validade.

Medido na tentativa de 2026-10-21 14:21-14:25: Criador explicou a série (14:22:45-14:23:04: "vou fazer uma série", "o objetivo é fazer a série",
"zera Minecraft", "no sobrevivência", "vou dar um pouco de assistência só"), mas a ficha só guardava citações cruas por 60-180 s e um "tema" por
palavra-chave ("jogo/partida"). Às 14:25:40, "Está preparado, Célia? Para essa aventura?" chegou a um modelo sem nenhum traço da aventura.

Duas fontes, nunca misturadas:
  * DITO POR CRIADOR (``EpisodeMemory``): frases do dono que declaram série/objetivo/modo/papel, guardadas VERBATIM, atribuídas e marcadas como "não
    confirmado pelo jogo". Valem pela sessão (``STATEMENT_VALID_S``), somem em ``Jev.reset()``. Nada é reescrito nem inferido além do padrão.
  * CONFIRMADO (``episode_context``): leitura do daemon do jogo (fase da série, mundo, retenção do corpo, movimento) com idade. Velho ou ausente =
    indisponível; nunca inventado.
"""
from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

STATEMENT_VALID_S = 45 * 60.0
IMMEDIATE_VALID_S = 10 * 60.0     # pedido do momento ("pega madeira", "vamos construir"): vale minutos, não a sessão
CANCELLED_SHOWN_S = 10 * 60.0     # o cancelamento aparece um tempo para ela saber que NÃO há objetivo, depois some
SNAPSHOT_VALID_S = 120.0
MAX_STATEMENTS = 6
SHOW_STATEMENTS = 4
QUOTE_LIMIT = 110


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text or ""))
    return " ".join("".join(ch for ch in folded if not unicodedata.combining(ch)).lower().split())


@dataclass(frozen=True)
class Statement:
    kind: str   # objetivo | serie | modo | papel | imediato | cancelado
    text: str   # verbatim, curto
    ts: float
    world_epoch: int = 0


# O objetivo é DEFINIDO ("o objetivo é…", "zera…", "vamos zerar…"); só mencionar a palavra ("não cancela o objetivo") não grava nada nem tira o verdadeiro.
_OBJ_DEFINE = re.compile(r"\b(?:o )?objetivo (?:agora |de hoje |dessa serie |da serie )?(?:e|sera|vai ser)\b")
_OBJETIVO = re.compile(r"\b(?:zera|zerar|zeramos|zerando)\b|\b(?:vamos|vou) (?:terminar|vencer|completar) (?:o )?(?:jogo|minecraft)\b")
_SERIE = re.compile(r"\bserie\b")
_MODO = re.compile(r"\bsobreviv\w*")
_PAPEL = re.compile(r"\b(vou|vamos)\s+(te\s+)?(dar|ajudar)\b|\bassistencia\b")
# "Moniqueft"/"Monecraft" chegam normalizados pelo ASR (regra minecraft_alias); aqui só o nome certo e termos do jogo.
# Cancelamento/mudança EXPLÍCITOS. "esquece"/"cancela" soltos não bastam: precisam apontar o objetivo/série/plano E haver algo guardado para cancelar.
_CANCELA = re.compile(r"\b(esquece|esqueca|cancela|cancele|desisti|deixa pra la|deixa isso|nao vai ser mais|nao e mais|mudei de ideia|muda de ideia|mudou o plano)\b")
_ALVO_CANCELA = re.compile(r"\b(objetivo|serie|plano|aventura|tudo isso|isso tudo)\b")
_NAO_CANCELA = re.compile(r"\bnao (cancela|esquece|desiste)\b")
_MUDA = re.compile(r"\b(agora (o )?objetivo e|o novo objetivo e|o objetivo agora e|muda(r)? o objetivo para|vamos mudar o objetivo|agora o plano e)\b")
_IMEDIATO = re.compile(r"^(?:ei |oi )?(?:celia )?(?:bora|vamos|agora vamos|primeiro vamos|vamos agora|pega|pegue|constroi|construa|coleta|corta|minera)\b")
_JOGO = re.compile(r"\b(minecraft|jogo|jogar|jogando|mundo|dragao|aventura|survival|serie)\b")


class EpisodeMemory:
    """O que o dono disse sobre o episódio, por sessão (um por ``Jev``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[Statement] = []

    def reset(self) -> None:
        with self._lock:
            self._items.clear()

    def _valid(self, now: float, epoch: int = 0) -> list[Statement]:
        """Validade por tipo: série/objetivo/modo/papel valem a sessão (45 min); o pedido imediato vale 10 min E some na troca de mundo; o cancelamento aparece 10 min."""
        ok = []
        for s in self._items:
            idade = now - s.ts
            if s.kind == "imediato" and (idade > IMMEDIATE_VALID_S or s.world_epoch != epoch):
                continue
            if s.kind == "cancelado" and idade > CANCELLED_SHOWN_S:
                continue
            if idade > STATEMENT_VALID_S:
                continue
            ok.append(s)
        return ok

    def observe_owner(self, text: str, *, now: float) -> None:
        """Só chamar com fala de owner autenticado. Padrão conservador: sem contexto de série/jogo, uma palavra solta ("objetivo") não conta."""
        verbatim = " ".join(str(text or "").split())
        plain = _plain(verbatim)
        if not verbatim or len(plain.split()) < 2:
            return
        epoch = episode_context.epoch()
        with self._lock:
            ativos = self._valid(now, epoch)
            guardado = [s for s in ativos if s.kind != "cancelado"]
            tem_serie = any(s.kind in {"serie", "objetivo", "cancelado"} for s in ativos)
            # 1) Cancelamento explícito: o que valia deixa de valer (não só "o mais novo vence"); fica o registro de que foi cancelado.
            if _CANCELA.search(plain) and not _NAO_CANCELA.search(plain) and _ALVO_CANCELA.search(plain) and guardado:
                self._items = [Statement("cancelado", verbatim[:QUOTE_LIMIT + 30], now, epoch)]
                return
            # 2) Mudança explícita de objetivo: o anterior sai, o novo entra.
            if _MUDA.search(plain):
                self._items = [s for s in ativos if s.kind not in {"objetivo", "imediato", "cancelado"}] + [Statement("objetivo", verbatim[:QUOTE_LIMIT + 30], now, epoch)]
                self._items = self._items[-MAX_STATEMENTS:]
                return
            kind = None
            if _OBJ_DEFINE.search(plain) or (_OBJETIVO.search(plain) and (_JOGO.search(plain) or tem_serie)):
                kind = "objetivo"
            elif _SERIE.search(plain) and re.search(r"\b(vou|vamos|fazer|fazendo|gravar|nossa|essa|disso|jogara)\b", plain):
                kind = "serie"
            elif _MODO.search(plain) and (tem_serie or _JOGO.search(plain)):
                kind = "modo"
            elif _PAPEL.search(plain) and tem_serie:
                kind = "papel"
            elif _IMEDIATO.search(plain) and (tem_serie or _JOGO.search(plain)):
                kind = "imediato"
            if kind is None:
                return
            if any(_plain(s.text) == plain for s in ativos):
                return
            base = [s for s in ativos if not (s.kind == "cancelado" or (kind == "imediato" and s.kind == "imediato"))]  # algo novo substitui o cancelado/o imediato anterior
            self._items = (base + [Statement(kind, verbatim[:QUOTE_LIMIT + 30], now, epoch)])[-MAX_STATEMENTS:]

    def statements(self, now: float) -> list[Statement]:
        """A frase mais recente de CADA tipo (objetivo, série, modo, papel), em ordem de fala. Só as mais recentes empurrariam o objetivo para fora
        quando ele repete a introdução (medido: "Zera Minecraft" saiu da ficha por causa de "Gente, vou fazer uma série isso aqui")."""
        with self._lock:
            por_tipo: dict[str, Statement] = {}
            for s in self._valid(now, episode_context.epoch()):
                por_tipo[s.kind] = s
            return sorted(por_tipo.values(), key=lambda s: s.ts)[-SHOW_STATEMENTS:]

    def tokens(self, now: float) -> set[str]:
        """Palavras do episódio, para decidir se um resultado de ferramenta tem a ver com a cena."""
        saida: set[str] = set()
        for s in self.statements(now):
            saida |= {t for t in re.findall(r"[a-z0-9]+", _plain(s.text)) if len(t) > 3}
        return saida


# --------------------------------------------------------------------------------------------- retrato confirmado (daemon do jogo) ----------
_PHASE_LABEL = {"presentation": "introdução (conversa e presença; sem progressão)", "adventure": "aventura (jogando)",
                "pause": "pausa na gravação", "ended": "episódio encerrado", "idle": "sem episódio em andamento"}


class _Retrato:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fixo: dict[str, Any] | None = None
        self._dados: dict[str, Any] | None = None
        self._lido_em = 0.0
        self._tentou_em = 0.0
        self._mundo = ""
        self._epoca = 0

    def usar_retrato_fixo(self, retrato: dict[str, Any] | None) -> None:
        """Teste/replay: retrato determinístico no lugar do daemon vivo."""
        with self._lock:
            self._fixo = retrato

    def definir(self, dados: dict[str, Any] | None, *, now: float | None = None) -> None:
        with self._lock:
            if dados is not None and dados.get("mundo") and self._mundo and dados["mundo"] != self._mundo:
                self._epoca += 1   # troca de mundo: pedido imediato e resultado do corpo do mundo anterior deixam de valer
            if dados is not None and dados.get("mundo"):
                self._mundo = dados["mundo"]
            self._dados, self._lido_em = dados, time.monotonic() if now is None else now

    def epoch(self) -> int:
        with self._lock:
            return int(self._fixo.get("mundo_epoca", 0)) if self._fixo is not None else self._epoca

    def atual(self, *, now: float | None = None) -> dict[str, Any] | None:
        agora = time.monotonic() if now is None else now
        with self._lock:
            if self._fixo is not None:
                return dict(self._fixo)
            if self._dados is None or agora - self._lido_em > SNAPSHOT_VALID_S:
                return None
            return {**self._dados, "idade_s": agora - self._lido_em, "mundo_epoca": self._epoca}

    def precisa_ler(self, *, now: float | None = None, max_idade: float = 8.0, recuo_falha: float = 20.0) -> bool:
        """Lê no máximo a cada ``max_idade`` s; depois de uma falha espera ``recuo_falha`` s (daemon fora do ar não pode custar latência em todo turno)."""
        agora = time.monotonic() if now is None else now
        with self._lock:
            if self._fixo is not None:
                return False
            espera = max_idade if self._dados is not None else recuo_falha
            return agora - max(self._lido_em, self._tentou_em) > espera or (self._tentou_em == 0.0 and self._lido_em == 0.0)

    def ler_do_daemon(self) -> None:
        """Bloqueante (loopback, curto). Falha = fica sem retrato: dado ausente aparece como indisponível, nunca inventado."""
        if os.getenv("CELIA_EPISODE_CONTEXT", "1") == "0":
            return
        from .minecraft_series import daemon_request

        resposta = daemon_request("GET", "/possession/perceive", None, 0.8)
        if not resposta.get("ok"):
            return  # mantém o ÚLTIMO retrato válido (vence por idade em SNAPSHOT_VALID_S); a tentativa já foi registrada e o recuo evita insistir
        politica = resposta.get("policy") or {}
        companheiro = (resposta.get("companion") or {}).get("presence") or {}
        self.definir({
            "serie": resposta.get("series"), "fase": politica.get("phase"), "mundo": resposta.get("world_id"),
            "retido": bool(politica.get("held") or companheiro.get("held")), "movimentos": companheiro.get("moves"),
            "acao_atual": (resposta.get("perception") or {}).get("action"),
        })

    def agendar(self) -> bool:
        """Dispara a leitura do daemon EM SEGUNDO PLANO, se for preciso, e volta na hora. A fala nunca espera: usa o último retrato válido (ou nenhum).
        Devolve True se uma leitura foi disparada. Sem loop rodando, não faz nada."""
        import asyncio

        if not self.precisa_ler():
            return False
        tarefa = getattr(self, "_tarefa", None)
        if tarefa is not None and not tarefa.done():
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        with self._lock:
            self._tentou_em = time.monotonic()
        self._tarefa = loop.create_task(self._ler_em_thread())
        return True

    async def _ler_em_thread(self) -> None:
        import asyncio

        try:
            await asyncio.wait_for(asyncio.to_thread(self.ler_do_daemon), timeout=1.5)
        except Exception:  # noqa: BLE001 - leitura de contexto nunca quebra a fala; o último retrato válido segue valendo até vencer
            pass


episode_context = _Retrato()


def rotulo_da_fase(fase: str | None) -> str | None:
    return _PHASE_LABEL.get(str(fase or ""))
