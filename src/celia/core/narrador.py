"""A mente que FALA — narra em tempo real o que a mente que FAZ está fazendo.

agente: claude | 2026-08-11 | Fatia 7 do plano travado

O QUE O PAI PEDIU
-----------------
Duas mentes. A que FAZ executa ferramenta, busca, imagem, memória. A que
FALA é a voz dela e narra o passo enquanto acontece: "tô pesquisando…",
"tô gerando a imagem…". O contrato entre elas é uma fila de eventos com
início, progresso e fim de cada ferramenta — não é um modelo chamando o
outro no meio da frase.

O QUE JÁ EXISTIA
----------------
A fila de eventos já existe: `tool_executor.execute_tools()` emite
`tool_call_status` com `running` / `completed` / `error`, e
`single_conversation.py` repassa pro navegador. Metade do contrato já
estava construída. O que faltava é a voz.

A MEDIÇÃO QUE DECIDIU A ARQUITETURA
-----------------------------------
Cronometrado no 9router em 2026-08-11, com `stream:false` (o gateway faz
streaming por padrão — sem esse campo a resposta nem parseia):

    gemini/gemini-3-flash-preview          2,16s   funciona, 189 tokens de raciocínio
    gemini/gemini-3.1-flash-lite-preview   4,00s   funciona, 0 de raciocínio
    gemini/gemma-4-31b-it                  6,38s   devolve VAZIO
    nvidia/minimaxai/minimax-m2.7            —     HTTP 410 Gone

O flash mais rápido leva 2,16s. Ferramenta rápida (hora, memória) termina
em menos que isso. Narrar só por LLM faria ela anunciar "tô pesquisando"
DEPOIS da busca ter acabado — mentira sobre o presente, pior que silêncio.

Daí as duas camadas:

  1. IMEDIATA (0 ms). Frase de banco, por ferramenta, disparada no mesmo
     instante em que a ferramenta começa. Sem rede, sem custo, sem falha
     possível. É esta que o Pai vai ouvir na maioria das vezes.

  2. ENRIQUECIDA (~2 s, opcional). O flash escreve uma frase com mais
     personalidade. Só entra no ar se a ferramenta AINDA estiver rodando
     quando a resposta chegar. Chegou tarde, é descartada — ela não fala
     no passado como se fosse presente.

Desligada por padrão (`CELIA_NARRADOR_LLM=1` liga): a camada imediata já
entrega o comportamento, e cada chamada custa tempo e cota.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
import urllib.request
from typing import Any, Callable, Awaitable

from loguru import logger

# ── Banco de frases ──────────────────────────────────────────────────────
# Em pt-BR informal, na voz dela. Várias por ferramenta pra não virar
# papagaio: ouvir "tô pesquisando" na décima vez seguida entrega o truque.
FRASES: dict[str, list[str]] = {
    "busca": ["tô pesquisando…", "deixa eu procurar isso…", "tô dando uma olhada na web…"],
    "imagem": ["já tô gerando…", "tô fazendo a arte já…", "peguei as referências, tô fazendo…"],
    "discord": ["já tô mandando lá…", "tô postando no Discord…"],
    "visao": ["tô olhando a tela…", "deixa eu ver isso…", "tô analisando a imagem…"],
    "memoria": ["tô lembrando…", "deixa eu puxar da memória…", "tô procurando isso que guardei…"],
    "minecraft": ["tô vendo o jogo…", "deixa eu olhar o mundo…", "tô conferindo lá no Minecraft…"],
    "sistema": ["tô mexendo aqui no PC…", "deixa eu resolver isso na máquina…"],
    "legenda": ["tô ajustando a legenda…"],
    "enquete": ["tô montando a enquete…"],
    "agente": ["tô passando pro agente…", "deixa eu delegar isso…"],
    "_generico": ["tô trabalhando nisso…", "só um segundo…", "deixa comigo…"],
}

# Nome da ferramenta -> família de frase. Casamento por trecho, porque os
# nomes reais vêm dos MCPs e mudam (`vision_extract_text`, `ddg-search`…).
PISTAS: list[tuple[tuple[str, ...], str]] = [
    (("search", "busca", "ddg", "web"), "busca"),
    (("image", "imagem", "edicao", "draw", "gerar"), "imagem"),
    (("discord", "postar_imagem", "postar_mensagem"), "discord"),
    (("vision", "visao", "screenshot", "ocr"), "visao"),
    (("memor", "lembr"), "memoria"),
    (("minecraft", "mine", "bot"), "minecraft"),
    (("system", "sistema", "automation", "command", "file", "folder"), "sistema"),
    (("legenda", "subtitle"), "legenda"),
    (("enquete", "poll"), "enquete"),
    (("opencode", "agent"), "agente"),
]

# Emoção que ela mostra enquanto trabalha. Fecha o laço com a Fatia 6:
# o rosto acompanha a narração em vez de ficar parado.
EMOCAO_TRABALHANDO = "confused"  # a mais próxima de "concentrada" no mapa do mao_pro


def familia(nome_ferramenta: str) -> str:
    alvo = (nome_ferramenta or "").lower()
    for chaves, fam in PISTAS:
        if any(k in alvo for k in chaves):
            return fam
    return "_generico"


def frase_imediata(nome_ferramenta: str) -> str:
    return random.choice(FRASES[familia(nome_ferramenta)])


class Narrador:
    """Consome a fila de eventos de ferramenta e produz fala.

    Não fala pela Célia na conversa: narra o BASTIDOR. A resposta real
    continua vindo da mente que faz. Por isso a narração vai num canal
    próprio (`celia-narracao`) e não entra no histórico.
    """

    def __init__(self, enviar: Callable[[dict], Awaitable[None]]) -> None:
        self._enviar = enviar
        self._rodando: dict[str, dict[str, Any]] = {}
        self.narradas = 0
        self.enriquecidas_no_ar = 0
        self.enriquecidas_tarde = 0
        self.falhas_llm = 0

    # -- entrada ----------------------------------------------------------
    async def evento(self, ev: dict[str, Any]) -> None:
        """Recebe um `tool_call_status`. Nunca levanta: narrar é acessório."""
        try:
            estado = (ev.get("status") or "").lower()
            tool_id = ev.get("tool_id") or "?"
            nome = ev.get("tool_name") or "?"
            if estado == "running":
                await self._comecou(tool_id, nome)
            elif estado in ("completed", "success", "done", "error"):
                await self._terminou(tool_id, nome, erro=(estado == "error"))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[Narrador] evento falhou: {type(exc).__name__}")

    # -- camada 1: imediata -----------------------------------------------
    async def _comecou(self, tool_id: str, nome: str) -> None:
        texto = frase_imediata(nome)
        self._rodando[tool_id] = {"nome": nome, "em": time.monotonic()}
        self.narradas += 1
        await self._falar(texto, nome, "imediata")

        # O rosto acompanha (Fatia 6). Prioridade baixa: qualquer emoção de
        # verdade — susto, alegria — atropela a cara de concentração.
        try:
            from .emotion_bus import barramento

            barramento.publicar(
                EMOCAO_TRABALHANDO, origem="narrador", prioridade=30, ttl_s=8.0
            )
        except Exception:  # noqa: BLE001
            pass

        if os.getenv("CELIA_NARRADOR_LLM", "").strip() in ("1", "true", "yes"):
            asyncio.create_task(self._enriquecer(tool_id, nome))

    async def _terminou(self, tool_id: str, nome: str, *, erro: bool) -> None:
        inicio = self._rodando.pop(tool_id, None)
        if erro:
            await self._falar("essa não deu certo…", nome, "erro")
            return
        # Ferramenta que demorou merece um "pronto". A rápida não: encher de
        # confirmação a cada chamada vira ruído.
        if inicio and (time.monotonic() - inicio["em"]) >= 3.0:
            await self._falar("pronto!", nome, "fim")

    # -- camada 2: enriquecida --------------------------------------------
    async def _enriquecer(self, tool_id: str, nome: str) -> None:
        """Pede uma frase melhor ao flash. Descarta se chegar tarde."""
        try:
            texto = await asyncio.wait_for(
                asyncio.to_thread(self._chamar_llm, nome), timeout=6.0
            )
        except Exception:  # noqa: BLE001
            self.falhas_llm += 1
            return
        if not texto:
            self.falhas_llm += 1
            return
        # A regra que a medição impôs: só fala no presente se ainda é presente.
        if tool_id not in self._rodando:
            self.enriquecidas_tarde += 1
            logger.debug(f"[Narrador] frase do LLM chegou tarde, descartada: {texto!r}")
            return
        self.enriquecidas_no_ar += 1
        await self._falar(texto, nome, "enriquecida")

    def _chamar_llm(self, nome: str) -> str | None:
        modelo = os.getenv("CELIA_NARRADOR_MODELO", "gemini/gemini-3-flash-preview")
        base = os.getenv("CELIA_NARRADOR_BASE_URL", "http://127.0.0.1:20127/v1")
        corpo = json.dumps(
            {
                "model": modelo,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Você é a Célia, VTuber, falando com o Criador enquanto "
                            f"executa a ferramenta '{nome}'. Diga UMA frase curta em "
                            "pt-BR informal narrando o que está fazendo AGORA. "
                            "Máximo 8 palavras. Só a frase, sem aspas."
                        ),
                    }
                ],
                # `stream:false` é obrigatório: o gateway faz SSE por padrão e
                # a resposta não parseia como JSON. Medido, não suposto.
                "stream": False,
                # Orçamento folgado de propósito: o flash gasta ~189 tokens
                # raciocinando antes de escrever. Com 16, devolve string vazia.
                "max_tokens": 220,
            }
        ).encode()
        req = urllib.request.Request(
            f"{base}/chat/completions", data=corpo,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read())
        texto = (d["choices"][0]["message"]["content"] or "").strip().strip('"')
        return texto[:120] or None

    # -- saída ------------------------------------------------------------
    async def _falar(self, texto: str, ferramenta: str, camada: str) -> None:
        await self._enviar(
            {
                "type": "celia-narracao",
                "text": texto,
                "tool": ferramenta,
                "camada": camada,
            }
        )
        logger.info(f"[Narrador] 🗣️ ({camada}) {texto}")

    def diagnostico(self) -> dict[str, Any]:
        return {
            "narradas": self.narradas,
            "enriquecidas_no_ar": self.enriquecidas_no_ar,
            "enriquecidas_tarde": self.enriquecidas_tarde,
            "falhas_llm": self.falhas_llm,
            "rodando_agora": len(self._rodando),
        }


# Instância única do processo: o estado de "o que está rodando agora" precisa
# sobreviver entre o evento de início e o de fim, que chegam em chamadas
# separadas. Um narrador novo a cada evento nunca saberia o que já começou.
_narrador: Narrador | None = None


def obter_narrador(enviar_texto) -> Narrador:
    """Devolve o narrador do processo, criando na primeira chamada.

    `enviar_texto` é o `websocket_send` da conversa (recebe str). O narrador
    fala em dict, então a conversão mora aqui, num lugar só.
    """
    global _narrador
    if _narrador is None:
        async def enviar(msg: dict) -> None:
            await enviar_texto(json.dumps(msg, ensure_ascii=False))

        _narrador = Narrador(enviar)
        logger.info("[Narrador] 🗣️ mente que FALA ligada à fila de ferramentas.")
    return _narrador
