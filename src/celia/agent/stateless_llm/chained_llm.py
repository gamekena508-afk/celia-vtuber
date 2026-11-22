"""Fila de fallback: tenta os elos em ordem, pula provedor sem cota e registra tudo.

Usado pelo Jev (seletor de tool, modo "tool") e pela Célia (personagem, modo
"persona"). Regras:
- Elo bloqueado no Contador de Cota (cota, chave, modelo morto...) é pulado na hora.
- Prazo do 1º token: se não chegou conteúdo a tempo, sigla LNT e vai pro próximo.
- Modo persona: depois do 1º token NUNCA troca de modelo (não repete fala).
- Modo tool: a resposta é curta, então espera inteira, valida e só então entrega.
  Nenhuma tool é executada aqui; quem executa é o agente, uma única vez.
- Cada tentativa vira um evento com sigla no var/cota/AAAA-MM-DD.jsonl.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, List

from loguru import logger
from openai import NOT_GIVEN, NotGiven

from .openai_compatible_llm import AsyncLLM
from ...core.model_chain import chain_links
from ...core.quota_ledger import QuotaLedger, classify_error
from ...core.gpu_scheduler import gpu_scheduler
from ...core.local_persona_profile import local_persona_system

ERR_PREFIX = "Error calling the chat endpoint:"
NO_TOOLS = "__API_NOT_SUPPORT_TOOLS__"
TOOL_UNAVAILABLE = "As ferramentas estao temporariamente indisponiveis. Tente novamente em instantes."
PERSONA_UNAVAILABLE = f"{ERR_PREFIX} todos os provedores da fila falharam."


class _Link:
    def __init__(self, spec: dict[str, Any], temperature: float, max_tokens: int, total_sec: float):
        self.model = spec["model"]
        self.max_tokens = int(spec.get("max_tokens") or max_tokens)
        self.ttft_sec = float(spec.get("ttft_sec") or 0)
        self.local_gpu_job = bool(spec.get("local_gpu_job"))
        # Local persona is buffered by _local_persona_events: its first yielded
        # event arrives only after the entire GPU job finishes. Give the HTTP
        # request enough time to outlive that *full-reply* TTFT budget.
        request_timeout = max(1.0, total_sec)
        if self.local_gpu_job:
            request_timeout = max(request_timeout, self.ttft_sec + 10.0)
        self.llm = AsyncLLM(model=self.model, base_url=spec["base_url"], llm_api_key=spec["api_key"],
                            temperature=temperature, max_tokens=self.max_tokens,
                            request_timeout_sec=request_timeout)
        self.llm.reraise_retryable_errors = True   # 429/401 sobem como exceção → sigla certa
        self.llm.allow_gateway_fallback = False    # quem faz fallback é a fila
        self.llm.cold_start_retries = 1            # sem esperar 4,5 s em retry de 404
        self.llm.extra_body = dict(spec.get("extra_body") or {})
        if spec.get("context_tokens"):
            self.llm.context_tokens = int(spec["context_tokens"])
        if self.local_gpu_job:
            # O ator local é só fala, nunca um segundo executor de ferramentas.
            self.llm.support_tools = False


class ChainedLLM(AsyncLLM):
    """Herda de AsyncLLM para o agente tratar como OpenAI-compatible (tools nativas)."""

    def __init__(self, brain: str, cfg: dict[str, Any], ledger: QuotaLedger):
        bcfg = cfg.get(brain) or {}
        specs = chain_links(cfg, brain)
        if not specs:
            raise ValueError(f"Fila '{brain}' sem elos em config/model_chains.yaml")
        self.brain = brain
        self.mode = bcfg.get("mode", "tool" if brain == "jev" else "persona")
        self.ttft_sec = float(bcfg.get("ttft_sec", 3.0))
        self.total_sec = float(bcfg.get("total_sec", 20.0))
        temperature = float(bcfg.get("temperature", 0.0 if self.mode == "tool" else 0.55))
        max_tokens = int(bcfg.get("max_tokens", 1024 if self.mode == "tool" else 4096))
        super().__init__(model=specs[0]["model"], base_url=specs[0]["base_url"],
                         llm_api_key=specs[0]["api_key"], temperature=temperature,
                         max_tokens=max_tokens, request_timeout_sec=self.total_sec)
        self._ledger = ledger
        self._links = [_Link(s, temperature, max_tokens, self.total_sec) for s in specs]
        logger.info(f"[Fila {brain}] {len(self._links)} elos ({self.mode}): "
                    + " → ".join(link.model for link in self._links))

    @staticmethod
    async def _aclose(agen: Any) -> None:
        try:
            await agen.aclose()
        except Exception:
            pass

    @staticmethod
    async def _first_meaningful(agen: Any, buffer: list) -> Any:
        async for event in agen:
            if isinstance(event, list):
                if event:
                    buffer.append(event)
                    return event
                continue
            text = str(event or "")
            if not text:
                continue
            if text.startswith(ERR_PREFIX) or text == NO_TOOLS:
                buffer.append(text)
                return None
            buffer.append(text)
            return text
        return None

    @staticmethod
    def _usable(events: list) -> bool:
        for event in events:
            if isinstance(event, list) and event:
                return True
            if isinstance(event, str) and event.strip() and not event.startswith(ERR_PREFIX) and event != NO_TOOLS:
                return True
        return False

    def _fail_reason(self, link: _Link, buffer: list) -> tuple[str, float | None, str]:
        if link.llm.last_error is not None:
            return classify_error(link.llm.last_error)
        if NO_TOOLS in buffer:
            return "ERR", 600.0, "modelo sem suporte a tools nativas"
        if any(isinstance(e, str) and e.startswith(ERR_PREFIX) for e in buffer):
            return "ERR", 30.0, "erro do provedor"
        return "VAZ", None, "stream terminou sem conteúdo"

    async def _local_persona_events(
        self, link: _Link, messages: List[Dict[str, Any]], system: str | None
    ) -> AsyncIterator[Any]:
        """Serializa conversa e visão na mesma GPU; só entrega a fala completa.

        Acumular ~220 tokens evita falar texto incompleto se o VLM falhar a meio.
        A prioridade e cancelamento de tarefas ficam no gpu_scheduler.
        """
        async def run() -> list[Any]:
            result: list[Any] = []
            async for event in link.llm.chat_completion(
                messages, local_persona_system(system), NOT_GIVEN
            ):
                result.append(event)
            return result

        events = await gpu_scheduler.run_job("brain", run)
        for event in events:
            yield event

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        system: str = None,
        tools: List[Dict[str, Any]] | NotGiven = NOT_GIVEN,
        *,
        prefer_local: bool = False,
    ) -> AsyncIterator[Any]:
        in_chars = len(system or "") + sum(len(str(m.get("content") or "")) for m in messages)
        skipped: list[str] = []
        links = sorted(self._links, key=lambda link: not link.local_gpu_job) if prefer_local else self._links
        for link in links:
            if link.local_gpu_job and (self.mode != "persona" or not gpu_scheduler.enabled):
                # Na rota persona a lista de ferramentas pertence ao Jev;
                # o Gemma so recebe texto, mesmo se o ator recebeu schemas.
                skipped.append(f"{link.model}: requer fila de GPU e modo persona")
                continue
            usable, why = self._ledger.can_use(link.model, self.brain)
            if not usable:
                skipped.append(f"{link.model}: {why}")
                continue
            if link.local_gpu_job:
                link.llm.support_tools = False
                agen = self._local_persona_events(link, messages, system).__aiter__()
            else:
                link.llm.support_tools = True
                agen = link.llm.chat_completion(messages, system, tools).__aiter__()
            started = time.monotonic()
            buffer: list = []
            try:
                first = await asyncio.wait_for(
                    self._first_meaningful(agen, buffer), timeout=link.ttft_sec or self.ttft_sec
                )
            except asyncio.TimeoutError:
                await self._aclose(agen)
                self._ledger.record(self.brain, link.model, "LNT", total_ms=(time.monotonic() - started) * 1000,
                                    in_chars=in_chars, message=f"sem 1º token em {self.ttft_sec:g}s")
                continue
            except asyncio.CancelledError:
                await self._aclose(agen)
                raise
            except Exception as exc:
                await self._aclose(agen)
                code, pause, msg = classify_error(exc)
                self._ledger.record(self.brain, link.model, code, total_ms=(time.monotonic() - started) * 1000,
                                    in_chars=in_chars, message=msg, pause_sec=pause)
                continue
            if first is None:
                await self._aclose(agen)
                code, pause, msg = self._fail_reason(link, buffer)
                self._ledger.record(self.brain, link.model, code, total_ms=(time.monotonic() - started) * 1000,
                                    in_chars=in_chars, message=msg, pause_sec=pause)
                continue
            ttft_ms = (time.monotonic() - started) * 1000

            if self.mode == "tool":
                # Resposta curta: junta tudo dentro do prazo total, valida e só então entrega.
                remaining = max(0.1, self.total_sec - (time.monotonic() - started))

                async def drain() -> None:
                    async for event in agen:
                        buffer.append(event)

                try:
                    await asyncio.wait_for(drain(), timeout=remaining)
                except asyncio.TimeoutError:
                    await self._aclose(agen)
                    self._ledger.record(self.brain, link.model, "LNT", ttft_ms=ttft_ms,
                                        total_ms=(time.monotonic() - started) * 1000, in_chars=in_chars,
                                        message=f"não terminou em {self.total_sec:g}s")
                    continue
                except asyncio.CancelledError:
                    await self._aclose(agen)
                    raise
                except Exception as exc:
                    code, pause, msg = classify_error(exc)
                    self._ledger.record(self.brain, link.model, code, ttft_ms=ttft_ms,
                                        total_ms=(time.monotonic() - started) * 1000, in_chars=in_chars,
                                        message=msg, pause_sec=pause)
                    continue
                events = [e for e in buffer if not (isinstance(e, str) and (e.startswith(ERR_PREFIX) or e == NO_TOOLS))]
                if not self._usable(events):
                    self._ledger.record(self.brain, link.model, "JSN", ttft_ms=ttft_ms,
                                        total_ms=(time.monotonic() - started) * 1000, in_chars=in_chars,
                                        message="sem tool call nem texto utilizável")
                    continue
                out_chars = sum(len(e) for e in events if isinstance(e, str))
                self._ledger.record(self.brain, link.model, "OK", ttft_ms=ttft_ms,
                                    total_ms=(time.monotonic() - started) * 1000,
                                    in_chars=in_chars, out_chars=out_chars)
                for event in events:
                    yield event
                return

            # Persona: entrega o que já chegou e segue o stream. Daqui pra frente não troca de elo.
            out_chars = 0
            code, msg = "OK", ""
            try:
                for event in buffer:
                    out_chars += len(event) if isinstance(event, str) else 0
                    yield event
                async for event in agen:
                    if isinstance(event, str) and event.startswith(ERR_PREFIX):
                        code, _, msg = classify_error(link.llm.last_error or event)
                        msg = f"caiu no meio da fala: {msg}"
                        break
                    out_chars += len(event) if isinstance(event, str) else 0
                    yield event
            except Exception as exc:
                code, _, msg = classify_error(exc)
                msg = f"caiu no meio da fala: {msg}"
                raise
            finally:
                await self._aclose(agen)
                self._ledger.record(self.brain, link.model, code, ttft_ms=ttft_ms,
                                    total_ms=(time.monotonic() - started) * 1000,
                                    in_chars=in_chars, out_chars=out_chars, message=msg)
            return

        self._ledger.record(self.brain, "", "FIM", message="; ".join(skipped)[:200])
        logger.warning(f"[Fila {self.brain}] nenhum elo respondeu. Pulados: {skipped}")
        yield TOOL_UNAVAILABLE if self.mode == "tool" else PERSONA_UNAVAILABLE


def build_chained_llm(brain: str) -> ChainedLLM | None:
    """Fila pronta para o cérebro, ou None se estiver desligada (comportamento antigo)."""
    from ...core.model_chain import brain_enabled, load_config
    from ...core.quota_ledger import get_ledger

    cfg = load_config()
    if not brain_enabled(cfg, brain):
        return None
    return ChainedLLM(brain, cfg, get_ledger(cfg))
