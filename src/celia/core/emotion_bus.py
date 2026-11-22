"""Barramento de emoção da Célia — o rosto dela volta a mexer.

agente: claude | 2026-08-11 | Fatia 6 do plano travado

POR QUE ESTE ARQUIVO EXISTE
---------------------------
`publish_emotion()` (track_emotion_client.py) é chamado de 8 lugares do
sistema: motor de emoção, alertas do Minecraft, interrupção, introspecção,
orquestrador, backchannel e a conversa. Ele tenta falar por WebSocket com o
tracker em `ws://127.0.0.1:8770` e, quando falha, grava um JSON em
`var/track/spool/`.

Esse tracker morreu. Pior: ele era `vts_fake_tracker_v11.py`, feito pra rodar
em **Windows** com VTube Studio (API de plugin na 8001) e VB-CABLE. Não existe
neste Linux. Resultado medido em 2026-08-11: 93 arquivos parados no spool —
emoção que a Célia sentiu e que nunca chegou em lugar nenhum.

Só que o rosto dela **já está na tela**: o frontend renderiza Live2D
(`mao_pro`) com 8 expressões, e `model_dict.json` já mapeia as 9 emoções pra
índices de expressão. O caminho de texto (a LLM escrever `[happy]` no meio da
fala) funciona há tempo. O que nunca funcionou é o caminho de **evento** —
emoção que nasce de algo que aconteceu, não de algo que ela disse.

Este módulo é esse caminho. Não é o tracker ressuscitado: é a arbitragem que o
tracker fazia (prioridade, TTL, trava), reescrita contra o Live2D que roda
aqui de verdade.

O QUE ELE DECIDE
----------------
Vários lugares publicam emoção ao mesmo tempo. Sem arbitragem, a última a
chegar ganha e o rosto vira um piscar epilético. As regras, herdadas do
protocolo que o spool documenta em cada evento:

  priority  quem manda mais. Interrupção (90) atropela conversa (70).
  ttl_sec   por quanto tempo um evento transitório vale.
  persistente uma expressão de personagem fica ativa até outra substituí-la.
  lock_sec  janela em que NADA de prioridade menor ou igual pode entrar.
            É o que impede um alerta de Minecraft de cortar uma reação forte
            no meio.

Empate de prioridade: o mais novo ganha, exceto dentro da trava.

O QUE ELE NÃO FAZ
-----------------
Não fala com VTube Studio, não abre socket, não mexe em áudio, não faz
lip-sync. Só decide qual emoção está valendo agora e entrega quando muda.
Quem transporta é o singleton; quem desenha é o navegador.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

# Emoção usada quando nada mais está valendo. É também a única que o
# frontend garante existir no mapa de qualquer modelo.
NEUTRA = "neutral"

# Teto de segurança para eventos transitórios. Expressões de personagem
# persistentes só existem por pedido explícito e não expiram por relógio.
TTL_MAX_S = 30.0


@dataclass(frozen=True)
class EventoEmocao:
    """Um pedido de emoção, já normalizado."""

    emocao: str
    origem: str
    prioridade: int
    ttl_s: float | None
    trava_s: float
    em: float  # time.monotonic() de quando entrou
    persistente: bool = False
    metadados: dict[str, Any] = field(default_factory=dict)

    @property
    def expira_em(self) -> float | None:
        return None if self.persistente else self.em + float(self.ttl_s or 0)

    @property
    def travado_ate(self) -> float:
        return self.em + self.trava_s

    def vivo(self, agora: float) -> bool:
        return self.persistente or agora < float(self.expira_em or 0)


class BarramentoEmocao:
    """Decide qual emoção está valendo e avisa quando isso muda.

    Thread-safe de propósito: `publish_emotion` é chamado de código síncrono
    (alertas do Minecraft rodam em thread própria) enquanto quem transporta é
    assíncrono. O barramento fica no meio e não assume laço de eventos nenhum.
    """

    def __init__(self, ao_mudar: Callable[[EventoEmocao], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._atual: EventoEmocao | None = None
        self._base: EventoEmocao | None = None
        self._ao_mudar = ao_mudar
        # Contadores honestos: dizem o que o sistema fez, não o que se espera.
        self.aceitos = 0
        self.recusados_prioridade = 0
        self.recusados_trava = 0
        self.expirados = 0
        self.emocoes_desconhecidas: dict[str, int] = {}

    # -- inscrição --------------------------------------------------------
    def ao_mudar(self, callback: Callable[[EventoEmocao], None]) -> None:
        """Registra quem leva a mudança pra fora (o singleton, na prática)."""
        with self._lock:
            self._ao_mudar = callback

    # -- entrada ----------------------------------------------------------
    def publicar(
        self,
        emocao: str,
        *,
        origem: str,
        prioridade: int = 50,
        ttl_s: float | None = 4.0,
        trava_s: float = 0.0,
        persistente: bool = False,
        esperado_emocao: str | None = None,
        esperada_origem: str | None = None,
        metadados: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submete uma emoção. Devolve o que foi decidido, sem levantar erro.

        Publicar emoção nunca pode derrubar quem publicou: um alerta de
        Minecraft não vale um crash no meio da partida.
        """
        agora = time.monotonic()
        emocao = (emocao or NEUTRA).strip().lower() or NEUTRA
        persistente = bool(persistente)
        if persistente:
            ttl_s = None
            trava_s = max(0.0, min(float(trava_s or 0.0), TTL_MAX_S))
        else:
            ttl_s = max(0.1, min(float(ttl_s or 0.1), TTL_MAX_S))
            trava_s = max(0.0, min(float(trava_s or 0.0), ttl_s))

        novo = EventoEmocao(
            emocao=emocao,
            origem=origem or "desconhecida",
            prioridade=int(prioridade),
            ttl_s=ttl_s,
            trava_s=trava_s,
            em=agora,
            persistente=persistente,
            metadados=metadados or {},
        )

        with self._lock:
            anterior = self._atual
            if esperado_emocao is not None and (
                anterior is None
                or anterior.emocao != esperado_emocao
                or (esperada_origem is not None and anterior.origem != esperada_origem)
            ):
                return {
                    "ok": False,
                    "motivo": "estado_alterado",
                    "valendo": anterior.emocao if anterior else NEUTRA,
                }
            if anterior is not None and anterior.vivo(agora):
                # Trava: nada de prioridade menor OU IGUAL entra na janela.
                if agora < anterior.travado_ate and novo.prioridade <= anterior.prioridade:
                    self.recusados_trava += 1
                    return {
                        "ok": False,
                        "motivo": "travado",
                        "valendo": anterior.emocao,
                        "ate": round(anterior.travado_ate - agora, 2),
                    }
                # Fora da trava: só prioridade estritamente maior atropela,
                # exceto empate, onde o mais novo ganha (é sempre o caso aqui,
                # já que `novo` acabou de chegar).
                # A próxima emoção persistente é uma decisão explícita de
                # personagem, então substitui a anterior fora de uma trava.
                if not novo.persistente and novo.prioridade < anterior.prioridade:
                    self.recusados_prioridade += 1
                    return {
                        "ok": False,
                        "motivo": "prioridade_menor",
                        "valendo": anterior.emocao,
                    }

            if novo.persistente:
                self._base = novo
            self._atual = novo
            self.aceitos += 1
            callback = self._ao_mudar
            mudou = anterior is None or anterior.emocao != novo.emocao

        # Callback fora do lock: quem escuta pode demorar, e segurar o lock
        # aqui travaria todo publicador do sistema.
        if callback is not None:
            try:
                callback(novo)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[emocao] assinante falhou: {type(exc).__name__}")

        return {
            "ok": True,
            "emocao": novo.emocao,
            "mudou": mudou,
            "persistente": novo.persistente,
        }

    # -- saída ------------------------------------------------------------
    def valendo(self) -> EventoEmocao | None:
        """Emoção válida agora, ou None se nada está valendo (= neutro)."""
        agora = time.monotonic()
        with self._lock:
            atual = self._atual
            if atual is None:
                return None
            if not atual.vivo(agora):
                # Reads must not consume expiry before the broadcaster sees it.
                return self._base
            return atual

    def expirou_agora(self) -> EventoEmocao | None:
        """Consome a expiração: devolve o evento que acabou de vencer, uma vez.

        Quem varre o barramento usa isto pra saber que precisa mandar o rosto
        de volta pro neutro. Chamar de novo depois devolve None.
        """
        agora = time.monotonic()
        with self._lock:
            atual = self._atual
            if atual is not None and not atual.vivo(agora):
                self._atual = self._base
                self.expirados += 1
                return atual
            return None

    def indice_expressao(self, emocao: str, mapa: dict[str, int]) -> int | None:
        """Traduz nome de emoção pro índice de expressão do modelo Live2D.

        Emoção que não existe no mapa NÃO vira silêncio: é contada e registrada.
        Medido em 2026-08-11: `routes.py` publica `think`, que não existe no
        emotionMap do mao_pro — sem esta contagem, esse evento sumiria sem
        deixar rastro e ninguém saberia que o rosto ignorou.
        """
        if not mapa:
            return None
        if emocao in mapa:
            return int(mapa[emocao])
        with self._lock:
            self.emocoes_desconhecidas[emocao] = (
                self.emocoes_desconhecidas.get(emocao, 0) + 1
            )
        return int(mapa[NEUTRA]) if NEUTRA in mapa else None

    def diagnostico(self) -> dict[str, Any]:
        """Números crus, pra quem for conferir em vez de acreditar."""
        atual = self.valendo()
        with self._lock:
            return {
                "valendo": atual.emocao if atual else NEUTRA,
                "origem": atual.origem if atual else None,
                "persistente": atual.persistente if atual else False,
                "aceitos": self.aceitos,
                "recusados_prioridade": self.recusados_prioridade,
                "recusados_trava": self.recusados_trava,
                "expirados": self.expirados,
                "emocoes_desconhecidas": dict(self.emocoes_desconhecidas),
            }


# Instância única do processo. `publish_emotion` é chamado de módulos que não
# têm acesso ao singleton, então o barramento precisa ser alcançável por
# import direto.
barramento = BarramentoEmocao()
