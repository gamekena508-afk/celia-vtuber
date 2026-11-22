"""👁️ Análise visual assíncrona do último frame relevante da Célia.

O navegador continua sendo a única fonte de captura. Este módulo apenas interpreta
frames que o produtor barato já marcou como relevantes. Nunca cria fala direta:
o resumo entra no CognitiveLoop e segue CandidateIntent -> árbitro -> EventQueue.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from typing import Any

from loguru import logger

from .training_trace import training_trace
from .brain_router import brain_router
from .gpu_scheduler import gpu_scheduler
from .local_vision import local_vision_client
from .vision_policy import vision_policy, VisionUnavailable
from .observability import observability
from .vision_cache import vision_cache
from .vision_sources import normalize_source, vision_sources
from . import visual_scene as vscene
from .visual_ocr import mask_text, ocr_text, run_ocr


class ScreenVisionAnalyzer:
    def __init__(self) -> None:
        self._generation = 0
        self._in_flight = 0
        self._last_started = 0.0
        self._last_result: dict[str, Any] = {}
        self._last_result_signature: vscene.FrameSignature | None = None
        self._last_result_ts = 0.0
        self._disabled_until = 0.0
        self._last_provider = ""
        self._last_model = ""
        # Fontes independentes (tela/câmera): cada uma tem a sua geração e o seu
        # último frame, mas o teto de chamadas e o cooldown são COMPARTILHADOS.
        self._latest_gen_by_source: dict[str, int] = {}
        self._last_frame_by_source: dict[str, str] = {}
        self._last_frame_source = ""
        self._last_result_source = ""
        self._started_window: deque[float] = deque()
        self._emit_last: dict[tuple, float] = {}
        self._emit_count: dict[tuple, int] = {}
        # Memória visual (2026-10-18): assinatura por fonte, buffer de frames, cena atual e diagnóstico do último ciclo.
        self.scene = vscene.SceneMemory()
        self.ring = vscene.FrameRing()
        self._sig_by_source: dict[str, vscene.FrameSignature] = {}
        self._assess_by_source: dict[str, dict[str, Any]] = {}
        self._kinds_recent: deque[tuple[float, str]] = deque(maxlen=12)
        self._pending: dict[str, dict[str, Any]] = {}
        self._explicit_generations: set[int] = set()
        self._frame_seq = 0
        self._inflight_sig: dict[str, vscene.FrameSignature] = {}
        self._last_ocr_started = 0.0
        self._ocr_inflight = 0
        self.last_run: dict[str, Any] = {}
        self._last_delivered = ""
        self.metrics = {
            "ocr_only": 0,
            "skipped_duplicate": 0,
            "second_pass": 0,
            "blurry_capped": 0,
            "skipped_static": 0,
            "sequences": 0,
            "followup_scheduled": 0,
            "requested": 0,
            "skipped_low_relevance": 0,
            "skipped_cooldown": 0,
            "skipped_source_off": 0,
            "skipped_budget": 0,
            "cleared_on_off": 0,
            "completed": 0,
            "failed": 0,
            "stale_dropped": 0,
        }

    # ── direct address support (decision 3, 2026-10-02) ──────────────────
    # The owner's rule: a question sent straight to Célia carries the last
    # analysis, and the NEXT analysis starts once her reply cycle (TTS
    # generation) is over. The frame slot lives in bridge_routes; it hands the
    # latest frame here so the refresh does not depend on screen relevance.
    def remember_frame(self, image: str, source: str = "screen") -> None:
        canonical = normalize_source(source) or "screen"
        if image:
            self._last_frame_by_source[canonical] = image
            self._last_frame_source = canonical
            self._assess(image, canonical)

    def _recent_counts(self, now: float, window_s: float = 8.0) -> dict[str, int]:
        counts = {vscene.KIND_BURST: 0, vscene.KIND_TEXT: 0, vscene.KIND_MAJOR: 0, vscene.KIND_LOCAL: 0}
        for at, kind in self._kinds_recent:
            if now - at <= window_s and kind in counts:
                counts[kind] += 1
        return counts

    def _assess(self, image: str, source: str) -> dict[str, Any] | None:
        """Mede a mudança deste quadro (barato, sem rede) e decide o que ele merece. Guarda em `_assess_by_source`."""
        sig = vscene.signature_of(image)
        if sig is None:
            self._assess_by_source.pop(source, None)
            return None
        now = time.monotonic()
        previous = self._sig_by_source.get(source)
        change = vscene.compare(previous, sig)
        self._sig_by_source[source] = sig
        self._kinds_recent.append((now, change.kind))
        frame = self.ring.push(source, image, sig, now, change.kind, change.global_diff)
        counts = self._recent_counts(now)
        has_scene = bool(self.scene.state.summary) and self.scene.state.source == source
        profile, why = vscene.classify_profile(
            burst_recent=counts[vscene.KIND_BURST], text_changes_recent=counts[vscene.KIND_TEXT],
            major_recent=counts[vscene.KIND_MAJOR], has_scene=has_scene, explicit=False,
        )
        age = (now - self.scene.state.analyzed_at) if self.scene.state.analyzed_at else None
        decision = vscene.decide(profile, change, has_scene=has_scene, scene_age_s=age, explicit=False)
        if previous is None:
            # Primeiro quadro da fonte: não há referência para medir mudança. Quem pede a 1ª análise é o sinal de relevância do
            # navegador ou o pedido explícito (contrato existente: quadro sem `relevance` nunca agenda).
            decision = vscene.Decision(profile, vscene.ACTION_SKIP, "primeiro quadro: sem referencia para medir mudanca")
        if change.kind in (vscene.KIND_MAJOR, vscene.KIND_BURST) and has_scene:
            # Cena nova: o que foi reconhecido antes deixa de valer até ser reconfirmado. Em movimento contínuo (rajadas repetidas)
            # a cena flui em vez de trocar: só marca para reavaliar, sem esquecer tudo a cada quadro.
            self.scene.invalidate(f"mudanca {change.kind}", now, hard=profile != vscene.PROFILE_MOTION)
            if profile != vscene.PROFILE_MOTION and self._last_result_source == source:
                self._last_result = {}
                self._last_result_ts = 0.0
                self.scene.state.last_frame_id = self._frame_seq + 1
                vision_cache.clear()
                from .cognitive_loop import cognitive_loop
                cognitive_loop.clear_screen_context()
                from .visual_identity import visual_identity
                visual_identity.clear()
        self._frame_seq += 1   # ordem de chegada de TODO quadro (o buffer não numera os redundantes; a cena precisa de ordem total)
        record = {
            "image_id": id(image), "frame_id": self._frame_seq, "ring_id": frame.frame_id if frame else None, "change": change, "decision": decision,
            "profile_reason": why, "at": now, "quality": vscene.image_quality(image),
        }
        self._assess_by_source[source] = record
        return record

    def assessment_for(self, image: str, source: str) -> dict[str, Any] | None:
        rec = self._assess_by_source.get(source)
        return rec if rec and rec.get("image_id") == id(image) else None

    def decision_for_frame(self, source: str) -> vscene.Decision | None:
        rec = self._assess_by_source.get(source)
        return rec["decision"] if rec else None

    def last_analysis(self) -> tuple[dict[str, Any], float | None]:
        if not self._last_result or not self._last_result_ts:
            return {}, None
        return dict(self._last_result), time.monotonic() - self._last_result_ts

    def request_refresh_after_reply(self) -> None:
        self._refresh_after_reply = True

    def refresh_if_requested(self) -> bool:
        if not getattr(self, "_refresh_after_reply", False):
            return False
        self._refresh_after_reply = False
        source = self._last_frame_source
        if not (source and vision_sources.is_active(source)):
            source = next(
                (
                    s
                    for s in vision_sources.active_sources()
                    if self._last_frame_by_source.get(s)
                ),
                "",
            )
        frame = self._last_frame_by_source.get(source, "") if source else ""
        if not frame:
            logger.info(
                "👁️ refresh pós-resposta pedido, mas não há fonte ligada com frame"
            )
            return False
        scheduled = self.schedule(frame, relevance=1.0, source=source, explicit=True)
        logger.info(f"👁️ refresh pós-resposta agendado={scheduled}")
        return scheduled

    def reset_session(self) -> None:
        """Invalida análise antiga e zera diagnóstico visual da nova gravação."""
        self._generation += 1
        self._latest_gen_by_source.clear()
        self._started_window.clear()
        self._last_result_source = ""
        self._last_started = 0.0
        self._last_result = {}
        self._last_result_ts = 0.0
        self._disabled_until = 0.0
        self._last_provider = ""
        self._last_model = ""
        vision_cache.clear()
        self.scene.reset()
        self.ring.clear()
        self._sig_by_source.clear()
        self._assess_by_source.clear()
        self._kinds_recent.clear()
        self._pending.clear()
        self.last_run = {}
        for key in self.metrics:
            self.metrics[key] = 0

    @staticmethod
    def _budget_max() -> int:
        try:
            return int(os.getenv("CELIA_VISION_MAX_CALLS_PER_MIN", "12"))
        except ValueError:
            return 12

    def schedule(
        self,
        image: str,
        *,
        relevance: float,
        source: str = "screen",
        explicit: bool = False,
    ) -> bool:
        """Agenda análise sem bloquear o POST do frame. Retorna se foi aceita.

        `source`: "screen" ou "camera". Fonte desligada NUNCA agenda análise.
        `explicit`: pedido direto (ex.: refresh pós-resposta) ignora só o teto por
        minuto; continua exigindo fonte ligada e respeitando o cooldown.
        """
        if os.getenv("CELIA_SCREEN_VISION", "1").strip().lower() in {"0", "false", "no"}:
            return False
        canonical = normalize_source(source)
        if canonical is None or not vision_sources.is_active(canonical):
            self.metrics["skipped_source_off"] += 1
            self._emit_throttled(
                "vision.frame", "skipped", canonical or str(source), "source_off"
            )
            return False
        source = canonical
        relevance = max(0.0, min(1.0, float(relevance)))
        rec = self.assessment_for(image, source)
        decision = rec["decision"] if rec else None
        if decision is not None and not explicit:
            if decision.action == vscene.ACTION_SKIP:
                self.metrics["skipped_static"] += 1
                return False
            if decision.action == vscene.ACTION_OCR_ONLY:
                return self._schedule_ocr_only(image, source, rec)
            # O servidor mediu uma mudança que importa (cena/texto/movimento): isso vale mais que a relevância barata do navegador.
            relevance = max(relevance, 0.7)
        minimum = float(os.getenv("CELIA_VISION_MIN_RELEVANCE", "0.62"))
        if relevance < minimum:
            self.metrics["skipped_low_relevance"] += 1
            return False
        now = time.monotonic()
        if now < self._disabled_until:
            return False
        inflight_sig = self._inflight_sig.get(source)
        current_sig = self._sig_by_source.get(source)
        if inflight_sig is not None and current_sig is not None and vscene.compare(inflight_sig, current_sig).kind == vscene.KIND_STATIC:
            # já existe análise em voo deste mesmo quadro (ou de um idêntico): não duplica (medido: reconfirmação de tela parada
            # disparava 2 chamadas porque a 1ª ainda não tinha renovado a idade da cena)
            self.metrics["skipped_duplicate"] += 1
            return False
        min_interval = float(os.getenv("CELIA_VISION_MIN_INTERVAL", "1.5"))
        if now - self._last_started < max(0.25, min_interval):
            self.metrics["skipped_cooldown"] += 1
            self._remember_pending(image, relevance, source, decision)
            return False
        # Teto compartilhado: tela e câmera dividem o MESMO limite por minuto, então
        # ligar a câmera não dobra o gasto de LLM.
        max_calls = self._budget_max()
        while self._started_window and now - self._started_window[0] > 60.0:
            self._started_window.popleft()
        if not explicit and max_calls > 0 and len(self._started_window) >= max_calls:
            self.metrics["skipped_budget"] += 1
            self._remember_pending(image, relevance, source, decision)
            self._emit_throttled(
                "vision.frame",
                "skipped",
                source,
                "budget",
                calls_window=len(self._started_window),
                calls_max=max_calls,
            )
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        # Só uma análise realmente aceita substitui a anterior DA MESMA FONTE.
        # Mudança pequena ou frame barrado pelo cooldown não pode matar um resumo
        # importante em voo, e a câmera nunca mata a análise da tela (nem o inverso).
        self._generation += 1
        generation = self._generation
        self._latest_gen_by_source[source] = generation
        self._last_started = now
        self._started_window.append(now)
        self.metrics["requested"] += 1
        self._pending.pop(source, None)  # este frame (ou um mais novo) já está sendo analisado
        self._inflight_sig[source] = self._sig_by_source.get(source)
        if explicit:
            self._explicit_generations.add(generation)
        loop.create_task(self._analyze(generation, image, relevance, source))
        backend = vision_policy().backend
        logger.info(
            f"👁️ visão agendada fonte={source} backend={backend} "
            f"relevance={relevance:.3f} gen={generation}"
        )
        return True

    # ── análise ──────────────────────────────────────────────────────────────
    def _remember_pending(self, image: str, relevance: float, source: str, decision: "vscene.Decision | None") -> None:
        """Mudança importante barrada por cooldown/teto não se perde: fica pendente e roda quando a janela abrir."""
        if decision is None or decision.action not in (vscene.ACTION_VLM, vscene.ACTION_SEQUENCE):
            return
        self._pending[source] = {"image": image, "relevance": relevance, "at": time.monotonic()}
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        delay = max(0.3, float(os.getenv("CELIA_VISION_MIN_INTERVAL", "1.5")))
        loop.call_later(delay + 0.1, self._run_pending, source)

    def _run_pending(self, source: str) -> None:
        item = self._pending.get(source)
        if not item or not vision_sources.is_active(source):
            self._pending.pop(source, None)
            return
        latest = self._last_frame_by_source.get(source) or item["image"]
        if self.schedule(latest, relevance=max(item["relevance"], 0.7), source=source, explicit=True):
            self.metrics["followup_scheduled"] += 1
            logger.info(f"👁️🔁 análise pendente de {source} reagendada com o quadro mais recente")

    def _schedule_ocr_only(self, image: str, source: str, rec: dict[str, Any]) -> bool:
        """Só o texto mudou: atualiza o OCR e o contexto textual sem chamar o modelo visual nem gastar o teto de LLM."""
        now = time.monotonic()
        if now - self._last_ocr_started < float(os.getenv("CELIA_VISION_OCR_MIN_INTERVAL", "1.0")):
            self.metrics["skipped_cooldown"] += 1
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._last_ocr_started = now
        self._ocr_inflight += 1
        loop.create_task(self._ocr_only_guard(image, source, rec))
        return True

    async def _ocr_only_guard(self, image: str, source: str, rec: dict[str, Any]) -> None:
        try:
            await self._ocr_only(image, source, rec)
        finally:
            self._ocr_inflight = max(0, self._ocr_inflight - 1)

    async def _ocr_only(self, image: str, source: str, rec: dict[str, Any]) -> None:
        started = time.monotonic()
        change = rec["change"]
        run = self._new_run(source, rec["decision"], change, rec.get("frame_id"), backend="ocr")
        band = None
        if change.region and self.scene.state.ocr_lines:
            # Texto corre na horizontal: recortar só os blocos alterados corta palavras ao meio ("OFFLINE" virava "LINE"; medido).
            # Relê a FAIXA inteira das linhas afetadas, com uma linha de folga acima e abaixo.
            _x0, y0, _x1, y1 = change.region
            band = (0.0, max(0.0, y0 - 1 / vscene.BLOCK_ROWS), 1.0, min(1.0, y1 + 1 / vscene.BLOCK_ROWS))
        ocr = await run_ocr(image, band)
        merged = vscene.merge_region(self.scene.state.ocr_lines, ocr.lines, ocr.region) if ocr.status == "ok" else []
        status = ocr.status
        diff = self.scene.update_ocr(merged if status == "ok" else [], status=status, now=time.monotonic(), error=ocr.error,
                                     ms=ocr.ms, frame_id=rec.get("frame_id")) if status in ("ok", "empty", "error") else vscene.OcrDiff((), (), (), 0)
        run.update(ocr_status=status, ocr_ms=ocr.ms, ocr_conf=ocr.mean_conf, ocr_diff=diff.as_dict(),
                   total_ms=int((time.monotonic() - started) * 1000), error=ocr.error or None)
        self.metrics["ocr_only"] += 1
        if not self._source_alive(source):
            run["discard"] = "source_off"
        elif not diff.is_empty:
            self._deliver(self._ocr_context(source, diff), reason="vision_text_change", run=run)
        else:
            run["discard"] = "ocr_unchanged"
        self.last_run = run
        logger.info(f"👁️🔤 OCR-only fonte={source} status={status} {ocr.ms}ms diff={diff.as_dict() if not diff.is_empty else 'sem mudanca'}")

    def _tasks_busy(self) -> bool:
        return self._in_flight > 0 or self._ocr_inflight > 0 or (time.monotonic() - self._last_started) < 0.2

    @staticmethod
    def _source_alive(source: str) -> bool:
        return vision_sources.is_active(source)

    def _new_run(self, source: str, decision, change, frame_id, backend: str) -> dict[str, Any]:
        return {
            "source": source, "profile": decision.profile if decision else "legacy", "action": decision.action if decision else "vlm",
            "reason": decision.reason if decision else "sem avaliacao de mudanca", "frame_id": frame_id,
            "change": change.as_dict() if change else None, "frames": 0, "backend": backend, "url": None,
            "ocr_status": "skipped", "ocr_ms": None, "vlm_ms": None, "total_ms": None, "discard": None, "error": None,
            "delivered_chars": 0, "at": time.time(),
        }

    def _ocr_context(self, source: str, diff: "vscene.OcrDiff") -> str:
        parts = []
        if diff.added:
            parts.append("novo=" + " | ".join(diff.added)[:200])
        if diff.changed:
            parts.append("alterado=" + " | ".join(f"{a} -> {b}" for a, b in diff.changed)[:200])
        if diff.removed:
            parts.append("removido=" + " | ".join(diff.removed)[:120])
        scene = self.scene.state.summary if time.monotonic() - self.scene.state.analyzed_at <= 15 else ""
        return (f"source={source}; topic={self.scene.state.topic or 'screen'}; texto_ocr_mudou ({'; '.join(parts)}); "
                f"cena_conhecida={scene}")[:600]

    def _deliver(self, summary: str, *, reason: str, run: dict[str, Any]) -> None:
        try:
            from .cognitive_loop import cognitive_loop

            cognitive_loop.set_screen_context(summary)
            # OCR is a sensor update, not a new conversational turn. Repeated
            # semantic observations must not wake the brain again.
            if reason != "vision_text_change" and summary != self._last_delivered:
                cognitive_loop.request_think(reason)
            self._last_delivered = summary
            run["delivered_chars"] = len(summary)
        except Exception as exc:
            run["error"] = f"entrega: {type(exc).__name__}"
            logger.debug(f"👁️ contexto visual não entregue: {type(exc).__name__}")

    async def _analyze(
        self, generation: int, image: str, relevance: float, source: str = "screen"
    ) -> None:
        """Um ciclo completo. `_in_flight` cobre OCR + VLM + segundo passe + entrega: antes ele caía logo após a chamada ao VLM e
        quem esperava "terminou?" lia o estado no meio do ciclo (medido na bateria de 2026-10-18)."""
        self._in_flight += 1
        try:
            await self._analyze_cycle(generation, image, relevance, source)
        finally:
            self._in_flight = max(0, self._in_flight - 1)
            if self._in_flight == 0:
                self._inflight_sig.pop(source, None)
            self._drain_later(source)

    async def _analyze_cycle(
        self, generation: int, image: str, relevance: float, source: str = "screen"
    ) -> None:
        explicit = generation in self._explicit_generations
        self._explicit_generations.discard(generation)
        observability.emit_ambient(
            "vision.analysis",
            "start",
            {"source": source, "relevance": round(float(relevance), 3)},
        )
        started = time.monotonic()
        timeout = vision_policy().timeout
        rec = self.assessment_for(image, source)
        decision = rec["decision"] if rec else None
        change = rec["change"] if rec else None
        frame_id = rec["frame_id"] if rec else None
        if explicit:
            base = decision.profile if decision else vscene.PROFILE_STATIC
            decision = vscene.Decision(vscene.PROFILE_DETAIL if decision is None or base != vscene.PROFILE_MOTION else base,
                                       vscene.ACTION_VLM if not decision or decision.action != vscene.ACTION_SEQUENCE else decision.action,
                                       "analise detalhada pedida" if decision is None else decision.reason)
        backend = vision_policy().backend
        run = self._new_run(source, decision, change, frame_id, backend)
        scene_prev = self.scene.state.summary if rec else ""
        # 1) OCR como pista (só quando houve avaliação do quadro): texto fica separado da interpretação do modelo
        ocr_note, ocr_block, ocr_diff = "", "", None
        if rec and os.getenv("CELIA_VISION_OCR", "1").strip().lower() not in {"0", "false", "no"}:
            ocr = await run_ocr(image)
            run.update(ocr_status=ocr.status, ocr_ms=ocr.ms, ocr_conf=ocr.mean_conf)
            if ocr.status in ("ok", "empty"):
                ocr_diff = self.scene.update_ocr(ocr.lines, status=ocr.status, now=time.monotonic(), ms=ocr.ms, frame_id=frame_id)
                ocr_block = ocr_text(ocr.lines)
            else:
                self.scene.update_ocr([], status="error", now=time.monotonic(), error=ocr.error, ms=ocr.ms, frame_id=frame_id)
                ocr_note = f"falhou ({ocr.error or ocr.status})"
                run["error"] = f"ocr: {ocr.error or ocr.status}"
        # 2) frames: sequência só com 2+ quadros distintos; um único quadro nunca vira "movimento"
        images: list[str] | str = image
        timeline = None
        motion_note = ""
        profile = decision.profile if decision else None
        if rec and decision and decision.action == vscene.ACTION_SEQUENCE:
            limit = max(1, min(4, int(float(os.getenv("CELIA_VISION_SEQ_FRAMES", "3")))))
            frames = self.ring.window(source, time.monotonic(), limit=limit)
            if len(frames) >= 2:
                images = [f.image for f in frames]
                timeline = vscene.sequence_timeline(frames)
                motion_note = vscene.describe_motion(timeline)
                run["motion_measured"] = motion_note
                self.metrics["sequences"] += 1
            else:
                profile = vscene.PROFILE_STATIC  # sem sequência real: não afirma movimento
        run["frames"] = len(images) if isinstance(images, list) else 1
        prompt = (
            vscene.build_prompt(profile, ocr_text=ocr_block, previous_summary=scene_prev, change=change,
                                frame_count=run["frames"], timeline=timeline, ocr_note=ocr_note)
            if rec and profile else self._vision_prompt()
        )
        quality = rec.get("quality") if rec else None
        if quality is not None and quality.blurry:
            prompt += ("\nATENÇÃO: a imagem está borrada/sem detalhes nítidos. Não identifique pessoas ou personagens e não descreva "
                       "peças de roupa ou objetos específicos; descreva só manchas, cores e formas gerais e diga que não dá para ter certeza.")
            run["quality"] = quality.as_dict()
        max_tokens = int(os.getenv("CELIA_VISION_MAX_TOKENS", "180" if not rec else "420"))

        async def route_local():
            kwargs = {"json_schema": vscene.SCENE_SCHEMA} if rec else {}
            return await local_vision_client.request(prompt=prompt, image=images, max_tokens=max_tokens, **kwargs)

        vlm_started = time.monotonic()
        try:
            vision_policy().require_local()
            if not gpu_scheduler.enabled:
                raise RuntimeError(
                    "VLM local requer CELIA_GPU_SCHEDULER=1"
                )
            local_result = await asyncio.wait_for(
                gpu_scheduler.run_job(
                    "vision",
                    route_local,
                    generation=generation,
                ),
                timeout=timeout + 0.35 + (0.4 * max(0, run["frames"] - 1)),
            )
            result = self._parse(local_result.raw)
            self._last_provider = "local_vlm"
            self._last_model = local_result.model
            run["url"] = getattr(local_result, "url", None) or None
            run["model"] = local_result.model
            run["prompt_tokens"] = getattr(local_result, "prompt_tokens", None)
            if result:
                training_trace.capture(
                    "llm_call_complete",
                    {
                        "role": "screen_vision",
                        "model_used": local_result.model,
                        "provider": "local_vlm",
                        "prompt": prompt,
                        "output": result,
                        "image_payload": "[OMITTED_FROM_TRAINING_TRACE]",
                        "latency_ms": local_result.latency_ms,
                    },
                    layer="llm",
                    source="screen_vision",
                    trainable=True,
                )
        except Exception as exc:
            self.metrics["failed"] += 1
            self._disabled_until = time.monotonic() + 15.0
            detail = (f"{exc.code} status={exc.status}" if isinstance(exc, VisionUnavailable) else str(exc)[:200]) or type(exc).__name__
            run.update(error=f"{type(exc).__name__}: {detail}", discard="backend_error",
                       vlm_ms=int((time.monotonic() - vlm_started) * 1000),
                       total_ms=int((time.monotonic() - started) * 1000))
            run["failure"] = (exc.as_dict() if isinstance(exc, VisionUnavailable) else VisionUnavailable("backend_error").as_dict())
            self.last_run = run
            observability.emit_ambient(
                "vision.analysis",
                "fail",
                {"source": source, "error_type": type(exc).__name__},
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            logger.warning(
                f"👁️⚠️ visão falhou ({type(exc).__name__}: {detail}); cooldown 15s"
            )
            return
        run["vlm_ms"] = int((time.monotonic() - vlm_started) * 1000)

        superseded = not self._is_current(generation, source)
        off = self._latest_gen_by_source.get(source) == -1
        if off or (superseded and not rec):
            # fonte desligada (ou caminho sem memória de cena): resultado antigo não entra
            self.metrics["stale_dropped"] += 1
            run.update(discard="source_off" if off else "superseded", total_ms=int((time.monotonic() - started) * 1000))
            self.last_run = run
            observability.emit_ambient(
                "vision.analysis",
                "stale",
                {"source": source},
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            logger.info(f"👁️🧟 resumo visual stale descartado gen={generation} motivo={run['discard']}")
            return
        if not result:
            self.metrics["failed"] += 1
            run.update(discard="resposta_sem_json_util", total_ms=int((time.monotonic() - started) * 1000))
            run["failure"] = VisionUnavailable("invalid_response").as_dict()
            self.last_run = run
            return

        result["relevance"] = round(relevance, 3)
        result["source"] = source
        if rec and quality is not None and quality.blurry:
            result = {**vscene.cap_for_blur(result), "relevance": result["relevance"], "source": source}
            self.metrics["blurry_capped"] += 1
            run["quality"] = quality.as_dict()
        text_clues = ""
        if rec and backend == "local" and self.scene.state.ocr_lines and isinstance(images, str):
            names = vscene.flagged_by_ocr(result, self.scene.state.ocr_lines)
            if names:
                # CONFLITO texto × imagem: um nome do modelo coincide com texto escrito na tela. Passe 2 com o texto APAGADO
                # da imagem: só o que está desenhado. O resumo da cena passa a ser esse (visual) e o texto fica à parte.
                second = await self._visual_only_pass(image, generation, names, run)
                if second:
                    text_clues = " | ".join(l.text for l in self.scene.state.ocr_lines[:4])[:200]
                    result = {**second, "relevance": result["relevance"], "source": source,
                              "summary": second["summary"], "topic": second.get("topic") or result.get("topic", "screen")}
                    if rec and quality is not None and quality.blurry:
                        result = {**vscene.cap_for_blur(result), "relevance": result["relevance"], "source": source}
        if rec:
            applied = self.scene.apply_analysis(
                result, now=time.monotonic(), frame_id=frame_id, profile=profile or "", reason=decision.reason if decision else "",
                backend=self._last_provider or backend, source=source, change=change, state_version=self.scene.state.version,
            )
            if not applied:
                # o quadro analisado é mais velho que a cena atual: não sobrescreve; se ainda há mudança sem análise, reagenda
                self.metrics["stale_dropped"] += 1
                run.update(discard="frame_mais_antigo_que_a_cena", total_ms=int((time.monotonic() - started) * 1000))
                self.last_run = run
                self._drain_later(source)
                logger.info(f"👁️🧟 resultado de quadro antigo não sobrescreve a cena (frame={frame_id})")
                return
        # Stamp the final visual pass, including a possible OCR-masked retry.
        result.update(frame_id=frame_id, generation=generation, analyzed_at=time.time())
        self._last_result = result
        self._last_result_signature = vscene.signature_of(image)
        self._last_result_source = source
        self._last_result_ts = rec["at"] if rec else time.monotonic()
        vision_cache.put(
            str(result.get("topic") or "screen"),
            str(result.get("summary") or ""),
            (
                f"source={source}; salience={result.get('salience', 0.5)}; "
                f"should_comment={str(bool(result.get('should_comment'))).lower()}"
            ),
            analyzed_at=self._last_result_ts,
        )
        self.metrics["completed"] += 1
        elapsed_ms = int((time.monotonic() - started) * 1000)
        summary = self._context_text(result)
        if rec:
            presence = vscene.celia_presence(self.scene.state)
            if presence["verdict"] != vscene.AVATAR_UNCONFIRMED:
                summary = (summary + f"; avatar_celia={presence['verdict']}")[:700]
            if motion_note:
                summary = (summary + f"; movimento_medido_pelo_detector=({motion_note}); descricao_do_modelo_sobre_movimento_nao_e_confiavel")[:760]
            if text_clues:
                summary = (summary + f"; texto_na_tela(OCR, pista)={text_clues}")[:760]
            doubtful = [e.name for e in self.scene.state.entities.values() if e.uncertain][:3]
            if doubtful:
                # O texto do modelo pode afirmar ("a personagem Mario"); o estado diz que é palpite/nome escrito. A conversa recebe os dois.
                summary = (summary + f"; incerto={'/'.join(doubtful)} (nao confirmado como personagem desenhado)")[:760]
        observability.emit_ambient(
            "vision.analysis", "ok", {"source": source}, duration_ms=elapsed_ms
        )
        run.update(total_ms=elapsed_ms, ocr_diff=ocr_diff.as_dict() if ocr_diff else None)
        logger.info(
            f"👁️🧠 visão pronta em {elapsed_ms}ms topic={result.get('topic','?')} "
            f"comment={result.get('should_comment', False)} perfil={run['profile']} frames={run['frames']} "
            f"ocr={run['ocr_status']}/{run['ocr_ms']}ms vlm={run['vlm_ms']}ms"
        )
        self._deliver(summary, reason="vision_screen_change", run=run)
        self.last_run = run

    @staticmethod
    def _identity_image(key: str) -> str | None:
        """Imagem principal de uma identidade do IdentityRegistry (config/image-identities.json) como data-URL."""
        import base64

        from .raizes import RAIZ

        try:
            registry = json.loads((RAIZ / "config" / "image-identities.json").read_text(encoding="utf-8"))
            path = RAIZ / registry["identities"][key]["main"]
            data = path.read_bytes()
        except Exception:
            return None
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(data).decode()

    async def verify_celia_avatar(self, source: str = "screen") -> dict[str, Any]:
        """Conferência SOB DEMANDA: o avatar da Célia está na tela? Nunca confirma sem evidência visual.

        Um VLM de 1.6B responde "yes" a quase tudo (medido 2026-10-18: confirmou a Célia numa página sem personagem). Por isso a
        resposta só vale com CONTROLE NEGATIVO: a mesma pergunta com um retrato de OUTRA identidade no lugar do oficial. Se o modelo
        também disser "yes" para o impostor, ele não está discriminando e o veredito é `unconfirmed`.
        """
        result = await self.verify_identity("celia", source)
        return {**result, "verdict": vscene.AVATAR_VISIBLE if result.get("confirmed") else vscene.AVATAR_UNCONFIRMED}

    async def verify_identity(self, identity: str, source: str = "screen") -> dict[str, Any]:
        from .visual_identity import visual_identity
        frame = self._last_frame_by_source.get(source)
        if not frame or not vision_sources.is_active(source):
            return {"identity": identity, "confirmed": False, "confidence": "unconfirmed", "reason": "source_off"}
        result = await visual_identity.verify(frame, identity)
        current_frame = self._last_frame_by_source.get(source)
        changed = False
        if current_frame and frame != current_frame:
            previous_sig, current_sig = vscene.signature_of(frame), vscene.signature_of(current_frame)
            changed = (previous_sig is None or current_sig is None or
                       vscene.compare(previous_sig, current_sig).kind in {vscene.KIND_MAJOR, vscene.KIND_BURST})
        if not vision_sources.is_active(source) or not current_frame or changed:
            return {"identity": identity, "confirmed": False, "confidence": "unconfirmed", "reason": "frame_changed"}
        if result.get("confirmed") and identity == "celia":
            vscene.apply_avatar_check(self.scene.state, {"present": "yes", "external_reference": False,
                "confidence": .95, "evidence": result.get("evidence", [])}, time.monotonic())
        if result.get("confirmed") and self._last_result_source == source:
            records = [r for r in self._last_result.get("identities", []) if r.get("identity") != identity]
            self._last_result["identities"] = records + [result]
        return result

    async def identify_scene_candidates(self, data: dict[str, Any], deadline: float) -> dict[str, Any]:
        """Enrich an explicit description within its existing request deadline."""
        from .visual_identity import identity_candidates
        data = dict(data)
        original_ts = self._last_result_ts
        original_sig = (self._last_result_signature
                        if data.get("frame_id") == self._last_result.get("frame_id") else None)
        data["original_description"] = data.get("summary", "")
        data["identities"] = []
        for identity in identity_candidates(data):
            # Enrichment must finish while the original observation is still valid.
            remaining = min(deadline, original_ts + 14.9) - time.monotonic()
            if remaining <= 0:
                data["identity_status"] = "deadline"
                break
            try:
                result = await asyncio.wait_for(
                    self.verify_identity(identity, data["source"]), timeout=remaining)
                data["identities"].append(result)
            except asyncio.TimeoutError:
                data["identity_status"] = "deadline"
                break
            except Exception as exc:
                data["identities"].append({"identity": identity, "confirmed": False,
                                           "reason": type(exc).__name__})
        # A scene change during reference comparison invalidates the description too.
        if data.get("frame_id") != self.scene.state.last_frame_id:
            current_sig = vscene.signature_of(self._last_frame_by_source.get(data["source"], ""))
            if (original_sig is None or current_sig is None or
                    vscene.compare(original_sig, current_sig).kind in {vscene.KIND_MAJOR, vscene.KIND_BURST}):
                return {"ok": False, "error": "scene_changed_during_identity"}
        if not original_ts or time.monotonic() - original_ts > 15:
            return {"ok": False, "error": "scene_expired_during_identity"}
        return data

    async def _visual_only_pass(self, image: str, generation: int, names: list[str], run: dict[str, Any]) -> dict[str, Any] | None:
        """Segundo olhar sobre o MESMO quadro com o texto apagado. Devolve a análise visual ou None (mantém a análise 1)."""
        masked = mask_text(image, self.scene.state.ocr_lines)
        if not masked:
            return None
        if vscene.blank_content(masked):
            # Sem o texto não sobra imagem: perguntar ao VLM aqui o faria inventar uma figura (medido: "uma pessoa de jaqueta azul…" numa
            # página branca com texto). Resposta determinística e honesta, sem chamada ao modelo.
            self.metrics["second_pass"] += 1
            run["second_pass"] = {"names": names, "ms": 0, "ok": True, "blank": True}
            return {"summary": "Sem figuras ou imagens: a tela é só texto sobre fundo liso.", "topic": "texto", "salience": 0.3,
                    "should_comment": False, "entities": []}
        started = time.monotonic()
        try:
            res = await asyncio.wait_for(
                # Finish the accepted frame's OCR conflict check before newer frames.
                gpu_scheduler.run_job("vision_request", lambda: local_vision_client.request(
                    prompt=vscene.VISUAL_ONLY_PROMPT, image=masked, max_tokens=int(os.getenv("CELIA_VISION_MAX_TOKENS", "420")),
                    json_schema=vscene.SCENE_SCHEMA), generation=generation),
                timeout=vision_policy().timeout + 0.35)
        except Exception as exc:
            run["second_pass_error"] = f"{type(exc).__name__}: {str(exc)[:100]}"
            return None
        parsed = self._parse(res.raw)
        self.metrics["second_pass"] += 1
        run["second_pass"] = {"names": names, "ms": int((time.monotonic() - started) * 1000), "ok": bool(parsed)}
        return parsed

    async def analyze_now(self, source: str | None = None, timeout: float | None = None, ocr_only: bool = False) -> dict[str, Any]:
        """Análise EXPLÍCITA do quadro mais recente de uma fonte ligada, esperando o resultado (para as tools de visão da conversa).

        Antes (medido no teste 19, 2026-10-18) a tool `vision_describe_visual` usava um prompt fixo de "pessoa/estrutura/iluminação" no
        processo MCP e respondia "um cara de chapéu vermelho na frente de um prédio" para uma folha de sprites do Mario. Agora ela pede
        isto: OCR + prompt por perfil + memória da cena, e devolve cena, entidades (com incerteza) e texto lido separados.
        """
        limit = timeout if timeout is not None else float(os.getenv("CELIA_VISION_ANALYZE_NOW_TIMEOUT", "20"))
        deadline = time.monotonic() + limit
        if source is None:
            source = self._last_frame_source if vision_sources.is_active(self._last_frame_source or "") else next(
                (s for s in vision_sources.active_sources() if self._last_frame_by_source.get(s)), "")
        canonical = normalize_source(source or "")
        if not canonical or not vision_sources.is_active(canonical):
            return {"ok": False, "error": "nenhuma fonte de visao ligada"}
        frame = self._last_frame_by_source.get(canonical)
        if not frame:
            return {"ok": False, "error": "fonte ligada mas sem quadro recebido"}
        if ocr_only:
            # Só o texto (tool `vision_extract_text`): OCR estruturado do quadro atual, sem chamar o modelo visual.
            ocr = await run_ocr(frame, None, timeout=limit)
            diff = self.scene.update_ocr(ocr.lines if ocr.status == "ok" else [], status=ocr.status, now=time.monotonic(), error=ocr.error, ms=ocr.ms) \
                if ocr.status in ("ok", "empty", "error") else vscene.OcrDiff((), (), (), 0)
            if ocr.status in ("error", "unavailable"):
                return {"ok": False, "error": ocr.error or ocr.status}
            return {"ok": True, "source": canonical, "ocr_status": ocr.status, "ocr_ms": ocr.ms, "ocr_conf": ocr.mean_conf,
                    "ocr_lines": [l.text for l in ocr.lines], "ocr_boxes": [[round(v, 3) for v in l.box] for l in ocr.lines],
                    "changes": diff.as_dict()}
        def fresh_scene():
            st = self.scene.state
            now = time.monotonic()
            if (not vision_sources.is_active(canonical) or self._last_result_source != canonical
                    or not st.summary or st.summary != self._last_result.get("summary")
                    or not self._last_result_ts or now - self._last_result_ts > 15):
                return None
            current_names = {str(e.get("name", "")).casefold() for e in self._last_result.get("entities", [])}
            return {"ok": True, "source": canonical, "summary": st.summary, "topic": st.topic,
                    "profile": st.profile, "entities": [e.as_dict(now) for e in st.entities.values()
                        if e.name.casefold() in current_names and now - e.seen_at <= 15],
                    "ocr_lines": [l.text for l in st.ocr_lines], "ocr_status": st.ocr_status,
                    "frame_age_ms": int((now - self._last_result_ts) * 1000),
                    "backend": self._last_provider, "celia_presence": vscene.celia_presence(st),
                    "frame_id": st.last_frame_id, "generation": self._last_result.get("generation"),
                    "analyzed_at": self._last_result.get("analyzed_at"),
                    "identities": self._last_result.get("identities", [])}

        if fresh := fresh_scene():
            return fresh
        if self._in_flight:
            while self._in_flight and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
                if fresh := fresh_scene():
                    return fresh
            if self._in_flight:
                return {"ok": False, "error": "analysis_busy_timeout"}
        frame = self._last_frame_by_source.get(canonical)
        if not frame:
            return {"ok": False, "error": "fonte sem quadro atual"}
        before = self.scene.state.version
        self._last_started = 0.0
        self._disabled_until = 0.0                    # pedido explícito do dono da conversa: ignora o cooldown de falha
        if not self.assessment_for(frame, canonical):
            self._assess(frame, canonical)
        started = time.monotonic()
        if not self.schedule(frame, relevance=1.0, source=canonical, explicit=True):
            return {"ok": False, "error": "analise nao agendada (tela desligada ou orcamento)"}
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            if fresh := fresh_scene():
                return fresh
            # Streaming OCR runs independently and may never be idle. It must
            # not hold a completed semantic description until the HTTP timeout.
            if not self._in_flight:
                break
        if self._in_flight:
            return {"ok": False, "error": "analysis_timeout"}
        run = dict(self.last_run)
        st = self.scene.state
        if self.scene.state.version == before or run.get("discard") or run.get("error"):
            return {**run["failure"], "run": run} if run.get("failure") else {"ok": False, "error": run.get("error") or run.get("discard") or "sem resultado novo", "run": run}
        now = time.monotonic()
        return {
            "ok": True, "source": canonical, "summary": st.summary, "topic": st.topic, "profile": st.profile,
            "entities": [e.as_dict(now) for e in st.entities.values()],
            "ocr_lines": [l.text for l in st.ocr_lines], "ocr_status": st.ocr_status,
            "frame_age_ms": int((now - (self._assess_by_source.get(canonical) or {}).get("at", now)) * 1000),
            "elapsed_ms": int((now - started) * 1000), "backend": run.get("backend"), "celia_presence": vscene.celia_presence(st),
            "frame_id": st.last_frame_id, "generation": self._last_result.get("generation"),
            "analyzed_at": self._last_result.get("analyzed_at"), "identities": self._last_result.get("identities", []),
        }

    def _drain_later(self, source: str) -> None:
        """Terminou uma análise: se sobrou mudança relevante pendente desta fonte, agenda a atualização."""
        if source in self._pending:
            try:
                asyncio.get_running_loop().call_later(
                    max(0.3, float(os.getenv("CELIA_VISION_MIN_INTERVAL", "1.5"))), self._run_pending, source
                )
            except RuntimeError:
                pass

    def _is_current(self, generation: int, source: str) -> bool:
        """A análise ainda é a mais recente DA SUA FONTE?"""
        return generation == self._latest_gen_by_source.get(source, self._generation)

    def clear_source(self, source: str, reason: str = "off") -> None:
        """Fonte desligada: cancela o que estava em voo e esquece só o que veio dela."""
        canonical = normalize_source(source)
        if canonical is None:
            return
        self._latest_gen_by_source[canonical] = -1  # qualquer análise em voo vira stale
        self._last_frame_by_source.pop(canonical, None)
        self._sig_by_source.pop(canonical, None)
        self._inflight_sig.pop(canonical, None)
        self._assess_by_source.pop(canonical, None)
        self._pending.pop(canonical, None)
        self.ring.clear(canonical)
        if self.scene.state.source == canonical:
            self.scene.reset()
        if self._last_frame_source == canonical:
            self._last_frame_source = ""
        if self._last_result_source == canonical:
            self._last_result = {}
            self._last_result_ts = 0.0
            self._last_result_source = ""
            vision_cache.clear()
            try:
                from .cognitive_loop import cognitive_loop

                cognitive_loop.clear_screen_context()
            except Exception as exc:
                logger.debug(f"👁️ contexto visual não limpo: {type(exc).__name__}")
        if not vision_sources.active_sources():
            self._refresh_after_reply = False
        self.metrics["cleared_on_off"] += 1
        logger.info(f"👁️⛔ fonte {canonical} desligada ({reason}); análise e cache dela limpos")

    def _on_source_change(self, source: str, active: bool, reason: str) -> None:
        if not active:
            self.clear_source(source, reason)

    def _emit_throttled(
        self, node: str, status: str, source: str, reason: str,
        interval: float = 5.0, **meta: Any,
    ) -> None:
        """Eventos repetitivos viram um pulso por janela, com a contagem acumulada."""
        key = (node, status, source, reason)
        self._emit_count[key] = self._emit_count.get(key, 0) + 1
        now = time.monotonic()
        if now - self._emit_last.get(key, 0.0) < interval:
            return
        count = self._emit_count.pop(key, 1)
        self._emit_last[key] = now
        observability.emit_ambient(
            node, status, {"source": source, "reason": reason, "count": count, **meta}
        )

    @staticmethod
    def _vision_prompt() -> str:
        return (
            "Você é o sensor visual da Célia. Descreva APENAS o que é útil para uma co-host "
            "entender a tela agora. Leia texto visível importante, jogo/app, estado e algo saliente. "
            "Não converse com o usuário. Responda SOMENTE JSON compacto: "
            '{"summary":"...","topic":"...","salience":0.0,"should_comment":false}'
        )

    _REFUSAL = ("desculpe", "nao posso", "não posso", "sorry", "i cannot", "i can't", "cannot help")

    @staticmethod
    def _parse(raw: str) -> dict[str, Any] | None:
        if not raw:
            return None
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(raw[start : end + 1])
        except Exception:
            return None
        summary = str(obj.get("summary") or "").strip()[:320]
        if not summary:
            return None
        if any(token in summary.lower() for token in ScreenVisionAnalyzer._REFUSAL):
            return None  # recusa do modelo não é descrição da tela
        if vscene.echoes_prompt(summary):
            return None  # eco do prompt também não é observação
        try:
            salience = max(0.0, min(1.0, float(obj.get("salience", 0.5))))
        except (TypeError, ValueError):
            salience = 0.5
        should_comment = obj.get("should_comment", False)
        if isinstance(should_comment, str):
            should_comment = should_comment.strip().lower() in {"1", "true", "yes", "sim"}
        parsed = {
            "summary": summary,
            "topic": str(obj.get("topic") or "screen").strip()[:80],
            "salience": round(salience, 3),
            "should_comment": bool(should_comment),
        }
        entities = obj.get("entities")
        if isinstance(entities, list):
            clean = [e for e in entities if isinstance(e, dict) and str(e.get("name") or "").strip()][:8]
            if clean:
                parsed["entities"] = clean
        return parsed

    @staticmethod
    def _context_text(result: dict[str, Any]) -> str:
        return (
            f"source={result.get('source','screen')}; "
            f"topic={result.get('topic','screen')}; "
            f"salience={result.get('salience',0.5)}; "
            f"vision_hint_comment={str(bool(result.get('should_comment'))).lower()}; "
            f"summary={result.get('summary','')}"
        )[:600]

    def debug_snapshot(self) -> dict[str, Any]:
        age_ms = None
        if self._last_result_ts:
            age_ms = max(0, int((time.monotonic() - self._last_result_ts) * 1000))
        backend = vision_policy().backend
        return {
            "routing": (
                "local_vlm_gpu_queue"
                if backend == "local"
                else "invalid_local_configuration"
            ),
            "backend": backend,
            "vision_policy": {"url": vision_policy().url, "model": vision_policy().model, "timeout": vision_policy().timeout, "enabled": vision_policy().enabled, "cloud_fallback": False},
            "provider_last": self._last_provider or None,
            "model_last": self._last_model or None,
            "brain_router": brain_router.debug_snapshot(),
            "in_flight": self._in_flight,
            "last_result_age_ms": age_ms,
            "last_topic": self._last_result.get("topic") or None,
            "cache": vision_cache.snapshot(),
            "gpu_lane": gpu_scheduler.debug_snapshot(),
            "sources": vision_sources.snapshot(),
            "budget": {
                "calls_in_window": len(self._started_window),
                "calls_max_per_min": self._budget_max(),
            },
            "metrics": dict(self.metrics),
            "visual": self.visual_snapshot(),
        }

    def visual_snapshot(self) -> dict[str, Any]:
        """Diagnóstico da memória visual: cena, buffer, última decisão e último ciclo. Sem imagens nem texto da pessoa além do OCR resumido."""
        now = time.monotonic()
        assess = {
            src: {"profile": r["decision"].profile, "action": r["decision"].action, "reason": r["decision"].reason,
                  "profile_reason": r["profile_reason"], "change": r["change"].as_dict(), "age_s": round(now - r["at"], 2)}
            for src, r in self._assess_by_source.items()
        }
        presence = vscene.celia_presence(self.scene.state)
        return {
            "scene": self.scene.snapshot(now),
            "celia_presence": presence,
            "ring": self.ring.snapshot(now),
            "last_assessment": assess,
            "last_run": dict(self.last_run),
            "pending": {k: round(now - v["at"], 2) for k, v in self._pending.items()},
            "history": list(self.scene.history)[-6:],
        }


screen_vision = ScreenVisionAnalyzer()
vision_sources.subscribe(screen_vision._on_source_change)
