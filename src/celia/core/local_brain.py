"""Low-latency local decision brain for Célia.

The local brain does NOT write Célia's final reply. It only chooses a typed
route/category/tool from a small mode-specific menu. It is off by default and
can run in disposable shadow mode before gaining authority.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import httpx
import yaml

from .gpu_scheduler import gpu_scheduler
from .latency_trace import latency_trace


def _truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DecisionState:
    turn_id: str
    text: str
    mode: str
    celia_speaking: bool
    tool_running: bool
    screen_changed: bool
    vision_age_ms: int | None
    app: str | None
    legacy_decision: str | None = None

    def with_legacy(self, value: str | None) -> "DecisionState":
        return replace(self, legacy_decision=value)

    def block(self) -> str:
        # Stable fields first; user text last so llama.cpp can reuse more prefix.
        return (
            f"modo: {self.mode}\n"
            f"celia_falando: {'sim' if self.celia_speaking else 'nao'}\n"
            f"tool_rodando: {'sim' if self.tool_running else 'nao'}\n"
            f"tela_mudou: {'sim' if self.screen_changed else 'nao'}\n"
            f"visao_idade_ms: {self.vision_age_ms if self.vision_age_ms is not None else '-'}\n"
            f"app: {self.app or '-'}\n"
            f"fala: {(self.text or '').strip()[:240]}"
        )


@dataclass(frozen=True)
class Decision:
    route: str
    category: str | None = None
    tool: str | None = None
    q1: str | None = None
    q2: str | None = None
    q3: str | None = None
    latency_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class BrainConfigError(RuntimeError):
    pass


class LocalBrain:
    PREFIX = (
        "Voce e um roteador interno de software. "
        "Responda com UMA letra valida, sem explicacao. "
        "Escolha sempre a opcao mais provavel.\n\n"
    )

    def __init__(
        self,
        config_path: str | Path | None = None,
        base_url: str | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[3]
        self.config_path = Path(
            config_path or project_root / "config" / "brain_questions.yaml"
        )
        self.base_url = (
            base_url
            or os.getenv("CELIA_LOCAL_BRAIN_URL", "http://127.0.0.1:8091")
        ).rstrip("/")
        self.timeout = max(
            0.15, float(os.getenv("CELIA_BRAIN_TIMEOUT", "0.8"))
        )
        self._config_mtime_ns: int | None = None
        self._config: dict[str, Any] = {}
        self._http: httpx.AsyncClient | None = None
        # Lazy load: a malformed optional YAML must never prevent Célia from
        # booting while CELIA_LOCAL_BRAIN is disabled.

    @property
    def enabled(self) -> bool:
        return _truthy("CELIA_LOCAL_BRAIN", "0")

    @property
    def shadow_enabled(self) -> bool:
        # Safe default is OFF until the local server has been explicitly started.
        return _truthy("CELIA_BRAIN_SHADOW", "0")

    def runtime_config_snapshot(self) -> dict[str, Any]:
        """Return exactly the LocalBrain config loaded by this live process."""
        self._load_config()
        effective = {
            "prefix": self.PREFIX,
            "config": self._config,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "request": {
                "endpoint": "/completion",
                "n_predict": 1,
                "temperature": 0,
                "cache_prompt": True,
            },
        }
        canonical = json.dumps(
            effective, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return {
            "effective_config": effective,
            "effective_config_hash": hashlib.sha256(canonical).hexdigest(),
            "prompt_hash": hashlib.sha256(self.PREFIX.encode("utf-8")).hexdigest(),
            "brain_questions_hash": hashlib.sha256(
                self.config_path.read_bytes()
            ).hexdigest(),
            "config_path": str(self.config_path),
        }

    def _load_config(self, *, force: bool = False) -> None:
        try:
            stat = self.config_path.stat()
        except OSError as exc:
            raise BrainConfigError(f"config ausente: {self.config_path}") from exc
        if not force and self._config_mtime_ns == stat.st_mtime_ns:
            return
        try:
            loaded = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            raise BrainConfigError("brain_questions.yaml invalido") from exc
        self._validate_config(loaded)
        self._config = loaded
        self._config_mtime_ns = stat.st_mtime_ns

    @staticmethod
    def _validate_options(options: Any, label: str) -> None:
        if not isinstance(options, dict) or not options:
            raise BrainConfigError(f"{label}: options vazio")
        for key, value in options.items():
            if not isinstance(key, str) or len(key.strip()) != 1:
                raise BrainConfigError(f"{label}: chave deve ser uma letra")
            if not isinstance(value, str) or not value.strip():
                raise BrainConfigError(f"{label}: opcao vazia")

    @classmethod
    def _validate_config(cls, data: dict[str, Any]) -> None:
        modes = data.get("modes")
        if not isinstance(modes, dict) or "chat" not in modes:
            raise BrainConfigError("modes.chat ausente")
        for mode_name, mode in modes.items():
            if not isinstance(mode, dict):
                raise BrainConfigError(f"modo {mode_name} invalido")
            q1 = mode.get("q1")
            if not isinstance(q1, dict) or not str(q1.get("question") or "").strip():
                raise BrainConfigError(f"modo {mode_name}: q1 ausente")
            cls._validate_options(q1.get("options"), f"{mode_name}.q1")
            categories = mode.get("categories") or {}
            if categories:
                cls._validate_options(categories, f"{mode_name}.categories")
            tools = mode.get("tools") or {}
            if not isinstance(tools, dict):
                raise BrainConfigError(f"{mode_name}.tools invalido")
            for category, options in tools.items():
                cls._validate_options(options, f"{mode_name}.tools.{category}")

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._http

    @staticmethod
    def _probabilities(payload: dict[str, Any]) -> dict[str, float]:
        cp = payload.get("completion_probabilities") or []
        if not cp:
            return {}
        first = cp[0] if isinstance(cp[0], dict) else {}
        top = first.get("top_logprobs") or first.get("probs") or []
        out: dict[str, float] = {}
        for item in top:
            if not isinstance(item, dict):
                continue
            token = str(item.get("token") or item.get("tok_str") or "")
            letter = token.strip().upper()[:1]
            if not letter:
                continue
            if "logprob" in item:
                score = float(item["logprob"])
            else:
                prob = max(float(item.get("prob", 0.0)), 1e-12)
                score = math.log(prob)
            out[letter] = max(out.get(letter, float("-inf")), score)
        return out

    @classmethod
    def _pick_letter(
        cls,
        payload: dict[str, Any],
        options: dict[str, str],
    ) -> str:
        valid_letters = {str(key).upper() for key in options}
        probs = {
            key: value
            for key, value in cls._probabilities(payload).items()
            if key in valid_letters
        }
        if probs:
            return max(probs, key=probs.get)
        content = str(payload.get("content") or "").strip().upper()
        if content[:1] in valid_letters:
            return content[:1]
        raise ValueError("sem letra valida")

    async def _raw_ask(
        self,
        state: DecisionState,
        question: str,
        options: dict[str, str],
        *,
        stage: str,
    ) -> str:
        labels = "\n".join(f"{key}) {value}" for key, value in options.items())
        prompt = (
            f"{self.PREFIX}"
            f"{state.block()}\n\n"
            f"{question}\n{labels}\nResposta:"
        )
        started = time.perf_counter()
        latency_trace.mark(state.turn_id, f"{stage}_start")
        try:
            response = await self._client().post(
                "/completion",
                json={
                    "prompt": prompt,
                    "n_predict": 1,
                    "temperature": 0,
                    "cache_prompt": True,
                    "n_probs": max(10, len(options) * 3),
                },
            )
            response.raise_for_status()
            return self._pick_letter(response.json(), options)
        finally:
            latency_trace.mark(
                state.turn_id,
                stage,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

    async def _raw_boolean_score(
        self,
        state: DecisionState,
        question: str,
        candidate: str,
        *,
        stage: str,
    ) -> float:
        prompt = (
            "Voce e um roteador interno de software. "
            "Responda somente V ou F, sem explicacao.\n\n"
            f"{state.block()}\n\n"
            f"{question}\n"
            f"Opcao candidata: {candidate}\n"
            "Esta opcao e a melhor escolha? V ou F\nResposta:"
        )
        started = time.perf_counter()
        latency_trace.mark(state.turn_id, f"{stage}_start")
        try:
            response = await self._client().post(
                "/completion",
                json={
                    "prompt": prompt,
                    "n_predict": 1,
                    "temperature": 0,
                    "cache_prompt": True,
                    "n_probs": 10,
                },
            )
            response.raise_for_status()
            payload = response.json()
            probs = self._probabilities(payload)
            v_score = probs.get("V")
            f_score = probs.get("F")
            if v_score is not None:
                return float(v_score - (f_score if f_score is not None else -20.0))
            content = str(payload.get("content") or "").strip().upper()
            if content.startswith("V"):
                return 1.0
            if content.startswith("F"):
                return -1.0
            raise ValueError("sem V/F valido")
        finally:
            latency_trace.mark(
                state.turn_id,
                stage,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

    async def _ask_boolean_argmax(
        self,
        state: DecisionState,
        question: str,
        options: dict[str, str],
        *,
        stage: str,
        use_scheduler: bool = True,
    ) -> str:
        scores: dict[str, float] = {}
        for key, value in options.items():
            async def work(candidate=value, candidate_key=key) -> float:
                return await self._raw_boolean_score(
                    state,
                    question,
                    candidate,
                    stage=f"{stage}_{candidate_key}",
                )

            if use_scheduler:
                scores[key] = await gpu_scheduler.run_job(
                    "brain",
                    work,
                    turn_id=state.turn_id,
                )
            else:
                scores[key] = await work()
        if not scores:
            raise ValueError("sem opcoes")
        return max(scores, key=scores.get)

    async def ask(
        self,
        state: DecisionState,
        question: str,
        options: dict[str, str],
        *,
        stage: str,
        use_scheduler: bool = True,
    ) -> str:
        strategy = os.getenv("CELIA_BRAIN_STRATEGY", "choice").strip().lower()
        if strategy == "boolean":
            return await self._ask_boolean_argmax(
                state,
                question,
                options,
                stage=stage,
                use_scheduler=use_scheduler,
            )

        async def work() -> str:
            return await self._raw_ask(
                state, question, options, stage=stage
            )

        if use_scheduler:
            return await gpu_scheduler.run_job(
                "brain",
                work,
                turn_id=state.turn_id,
            )
        return await work()

    async def decide_observed(
        self,
        state: DecisionState,
        *,
        use_scheduler: bool = True,
    ) -> tuple[Decision, dict[str, float]]:
        """Shadow-only decision with normalized T/V/C/S/N probabilities for Q1."""
        started = time.perf_counter()
        mode = self._mode(state.mode)
        q1 = mode["q1"]
        options = dict(q1["options"])
        labels = "\n".join(f"{key}) {value}" for key, value in options.items())
        prompt = (
            f"{self.PREFIX}"
            f"{state.block()}\n\n"
            f"{q1['question']}\n{labels}\nResposta:"
        )

        async def q1_work():
            response = await self._client().post(
                "/completion",
                json={
                    "prompt": prompt,
                    "n_predict": 1,
                    "temperature": 0,
                    "cache_prompt": True,
                    "n_probs": max(10, len(options) * 3),
                },
            )
            response.raise_for_status()
            payload = response.json()
            return self._pick_letter(payload, options), self._probabilities(payload)

        if use_scheduler:
            first, raw_probs = await gpu_scheduler.run_job(
                "brain", q1_work, turn_id=state.turn_id
            )
        else:
            first, raw_probs = await q1_work()

        route = str(options[first])
        semantic = {
            "ferramenta": "T",
            "visao": "V",
            "conversa": "C",
            "estado": "S",
            "silencio": "N",
        }
        class_logprobs: dict[str, float] = {}
        for letter, value in options.items():
            cls = semantic.get(str(value))
            if cls and letter in raw_probs:
                class_logprobs[cls] = float(raw_probs[letter])
        if class_logprobs:
            peak = max(class_logprobs.values())
            weights = {k: math.exp(v - peak) for k, v in class_logprobs.items()}
            denom = sum(weights.values()) or 1.0
            probs = {c: weights.get(c, 0.0) / denom for c in ("T", "V", "C", "S", "N")}
        else:
            chosen_cls = semantic.get(route, "C")
            probs = {c: (1.0 if c == chosen_cls else 0.0) for c in ("T", "V", "C", "S", "N")}

        if route != "ferramenta":
            return (
                Decision(
                    route=route,
                    q1=first,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                ),
                probs,
            )

        categories = dict(mode.get("categories") or {})
        if not categories:
            raise BrainConfigError("rota ferramenta sem categories")
        second = await self.ask(
            state,
            "Qual categoria de ferramenta?",
            categories,
            stage="brain_q2",
            use_scheduler=use_scheduler,
        )
        category = str(categories[second])
        tools_by_category = mode.get("tools") or {}
        tool_options = dict(tools_by_category.get(category) or {})
        if not tool_options:
            raise BrainConfigError(f"categoria sem tools: {category}")
        third = await self.ask(
            state,
            "Qual ferramenta especifica?",
            tool_options,
            stage="brain_q3",
            use_scheduler=use_scheduler,
        )
        return (
            Decision(
                route="ferramenta",
                category=category,
                tool=str(tool_options[third]),
                q1=first,
                q2=second,
                q3=third,
                latency_ms=int((time.perf_counter() - started) * 1000),
            ),
            probs,
        )

    def _mode(self, requested: str) -> dict[str, Any]:
        self._load_config()
        aliases = {
            "recording": "roteiro",
            "gravação": "roteiro",
            "gravacao": "roteiro",
        }
        name = aliases.get(str(requested or "").lower(), str(requested or "chat").lower())
        return self._config["modes"].get(name) or self._config["modes"]["chat"]

    async def decide(
        self,
        state: DecisionState,
        *,
        use_scheduler: bool = True,
    ) -> Decision:
        started = time.perf_counter()
        mode = self._mode(state.mode)
        q1 = mode["q1"]
        first = await self.ask(
            state,
            str(q1["question"]),
            dict(q1["options"]),
            stage="brain_q1",
            use_scheduler=use_scheduler,
        )
        route = str(q1["options"][first])
        if route != "ferramenta":
            return Decision(
                route=route,
                q1=first,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        categories = dict(mode.get("categories") or {})
        if not categories:
            raise BrainConfigError("rota ferramenta sem categories")
        second = await self.ask(
            state,
            "Qual categoria de ferramenta?",
            categories,
            stage="brain_q2",
            use_scheduler=use_scheduler,
        )
        category = str(categories[second])
        tools_by_category = mode.get("tools") or {}
        tool_options = dict(tools_by_category.get(category) or {})
        if not tool_options:
            raise BrainConfigError(f"categoria sem tools: {category}")
        third = await self.ask(
            state,
            "Qual ferramenta especifica?",
            tool_options,
            stage="brain_q3",
            use_scheduler=use_scheduler,
        )
        return Decision(
            route="ferramenta",
            category=category,
            tool=str(tool_options[third]),
            q1=first,
            q2=second,
            q3=third,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


class LocalBrainRuntime:
    def __init__(self, brain: LocalBrain | None = None) -> None:
        self.brain = brain or LocalBrain()

    @property
    def enabled(self) -> bool:
        return self.brain.enabled

    @property
    def shadow_enabled(self) -> bool:
        return self.brain.shadow_enabled

    async def decide(self, state: DecisionState) -> Decision:
        if self.enabled and not gpu_scheduler.enabled:
            raise RuntimeError(
                "local brain requer CELIA_GPU_SCHEDULER=1 para nao disputar GPU fora da fila"
            )
        return await asyncio.wait_for(
            self.brain.decide(state),
            timeout=self.brain.timeout,
        )

    def maybe_shadow(
        self,
        state: DecisionState,
        *,
        legacy_decision: str | None,
    ) -> bool:
        if not self.shadow_enabled or self.enabled:
            return False
        shadow_state = state.with_legacy(legacy_decision)

        async def work() -> Decision:
            started = time.perf_counter()
            try:
                decision = await asyncio.wait_for(
                    self.brain.decide(shadow_state, use_scheduler=False),
                    timeout=self.brain.timeout,
                )
                latency_trace.mark(
                    shadow_state.turn_id,
                    "brain_shadow",
                    new=decision.as_dict(),
                    old=legacy_decision or "none",
                    match=(decision.route == (legacy_decision or "conversa")),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                return decision
            except Exception as exc:
                latency_trace.mark(
                    shadow_state.turn_id,
                    "brain_shadow_error",
                    error_type=type(exc).__name__,
                )
                raise

        # Shadow is allowed only behind the shared GPU scheduler. Without
        # that lane it could silently compete with TTS/vision and poison the
        # production latency baseline.
        if not gpu_scheduler.enabled:
            return False
        future = gpu_scheduler.submit_shadow_if_idle(
            work,
            turn_id=state.turn_id,
        )
        if future is None:
            return False

        def _consume(done: asyncio.Future[Any]) -> None:
            try:
                done.exception()
            except (asyncio.CancelledError, Exception):
                pass

        future.add_done_callback(_consume)
        return True


local_brain_runtime = LocalBrainRuntime()
