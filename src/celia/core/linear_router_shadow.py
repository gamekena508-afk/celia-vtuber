"""CPU-only linear router shadow for Célia.

This module never authorizes or executes actions. It only predicts T/V/C/S/N and
appends auditable JSONL shadow records when explicitly enabled.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from .local_brain import DecisionState


_CLASS_TO_ROUTE = {
    "T": "ferramenta",
    "V": "visao",
    "C": "conversa",
    "S": "estado",
    "N": "silencio",
}
_DEICTIC_RE = re.compile(
    r"\b(isso|isso aí|isso ai|aqui|aquilo|esse negócio|esse negocio|essa tela)\b",
    re.IGNORECASE,
)
_CORRECTION_RE = re.compile(
    r"^\s*(?:não[, ]+não|nao[, ]+nao|não[, ]|nao[, ]|pera(?: aí)?|"
    r"quer dizer|não foi isso|nao foi isso|não era|nao era|cancela|deixa)",
    re.IGNORECASE,
)


def _truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class LinearShadowDecision:
    cls: str
    route: str
    probs: dict[str, float]
    top1_probability: float
    top2_class: str | None
    top2_probability: float | None
    margin: float
    novelty_similarity: float | None
    latency_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "class": self.cls,
            "route": self.route,
            "probs": {k: round(float(v), 6) for k, v in self.probs.items()},
            "top1_probability": round(self.top1_probability, 6),
            "top2_class": self.top2_class,
            "top2_probability": (
                round(self.top2_probability, 6)
                if self.top2_probability is not None
                else None
            ),
            "margin": round(self.margin, 6),
            "novelty_similarity": (
                round(self.novelty_similarity, 6)
                if self.novelty_similarity is not None
                else None
            ),
            "latency_ms": round(self.latency_ms, 4),
        }


class LinearRouterShadow:
    def __init__(self) -> None:
        project_root = Path(__file__).resolve().parents[3]
        self.project_root = project_root
        self.artifact_path = Path(
            os.getenv(
                "CELIA_LINEAR_ROUTER_MODEL",
                str(project_root / "models" / "router_linear_v2.joblib"),
            )
        )
        self.meta_path = Path(
            os.getenv(
                "CELIA_LINEAR_ROUTER_META",
                str(project_root / "models" / "router_linear_v2.meta.json"),
            )
        )
        self.log_path = Path(
            os.getenv(
                "CELIA_LINEAR_ROUTER_SHADOW_LOG",
                str(project_root / "logs" / "router_linear_shadow.jsonl"),
            )
        )
        self.active_card_path = Path(
            os.getenv(
                "CELIA_ROUTER_OOD_ACTIVE_CARD_FILE",
                str(project_root / "logs" / "router_ood_active_card.json"),
            )
        )
        self._lock = threading.Lock()
        self._payload: dict[str, Any] | None = None
        self._meta: dict[str, Any] = {}
        self._artifact_sha256: str | None = None
        self._last_turn_id: str | None = None
        self._last_text: str | None = None
        self._last_route: str | None = None
        self._last_at: float | None = None

    @property
    def enabled(self) -> bool:
        return _truthy("CELIA_LINEAR_ROUTER_SHADOW", "0")

    def _ensure_loaded(self) -> None:
        if self._payload is not None:
            return
        if not self.artifact_path.exists():
            raise FileNotFoundError(self.artifact_path)
        payload = joblib.load(self.artifact_path)
        required = {"vectorizer", "classifier", "train_matrix", "version"}
        missing = required - set(payload)
        if missing:
            raise RuntimeError(f"linear router artifact missing fields: {sorted(missing)}")
        self._payload = payload
        self._artifact_sha256 = _sha256(self.artifact_path)
        if self.meta_path.exists():
            try:
                self._meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except Exception:
                self._meta = {}

    def _active_card(self) -> dict[str, Any] | None:
        if not self.active_card_path.exists():
            return None
        try:
            obj = json.loads(self.active_card_path.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    def predict(self, text: str) -> LinearShadowDecision:
        self._ensure_loaded()
        assert self._payload is not None
        vec = self._payload["vectorizer"]
        clf = self._payload["classifier"]
        train_matrix = self._payload["train_matrix"]

        started = time.perf_counter()
        q = vec.transform([text])
        probs = clf.predict_proba(q)[0]
        classes = list(clf.classes_)
        order = np.argsort(-probs)
        top1_idx = int(order[0])
        top2_idx = int(order[1]) if len(order) > 1 else None
        cls = str(classes[top1_idx])
        probs_map = {c: 0.0 for c in ("T", "V", "C", "S", "N")}
        for class_name, prob in zip(classes, probs):
            if str(class_name) in probs_map:
                probs_map[str(class_name)] = float(prob)
        p1 = float(probs[top1_idx])
        p2 = float(probs[top2_idx]) if top2_idx is not None else None
        top2_class = str(classes[top2_idx]) if top2_idx is not None else None
        margin = p1 - (p2 or 0.0)

        novelty_similarity: float | None = None
        try:
            # FeatureUnion joins two individually L2-normalized TF-IDF blocks.
            # Cosine is dot / (||q|| * ||train||), computed sparsely.
            q_norm = float(np.sqrt(q.multiply(q).sum()))
            train_norms = np.sqrt(train_matrix.multiply(train_matrix).sum(axis=1)).A1
            dots = (q @ train_matrix.T).toarray().ravel()
            denom = train_norms * q_norm
            valid = denom > 0
            if np.any(valid):
                sims = np.zeros_like(dots, dtype=float)
                sims[valid] = dots[valid] / denom[valid]
                novelty_similarity = float(np.max(sims))
        except Exception:
            novelty_similarity = None

        elapsed_ms = (time.perf_counter() - started) * 1000
        return LinearShadowDecision(
            cls=cls,
            route=_CLASS_TO_ROUTE.get(cls, "conversa"),
            probs=probs_map,
            top1_probability=p1,
            top2_class=top2_class,
            top2_probability=p2,
            margin=margin,
            novelty_similarity=novelty_similarity,
            latency_ms=elapsed_ms,
        )

    @staticmethod
    def _asr_fields(asr_meta: dict[str, Any] | None) -> dict[str, Any]:
        meta = asr_meta if isinstance(asr_meta, dict) else {}
        explicit_confidence = None
        for key in ("confidence", "asr_confidence", "transcription_confidence"):
            value = meta.get(key)
            if isinstance(value, (int, float)):
                explicit_confidence = float(value)
                break
        uncertain = bool(
            meta.get("measured_suspicious")
            or meta.get("language_shape_mismatch")
            or str(meta.get("status") or "").upper() == "UNCERTAIN"
        )
        acoustic = meta.get("acoustic") if isinstance(meta.get("acoustic"), dict) else None
        return {
            "asr_confidence": explicit_confidence,
            "asr_confidence_source": "engine" if explicit_confidence is not None else "unavailable",
            "asr_uncertain": uncertain,
            "asr_raw_text": str(meta.get("raw_text") or "")[:500] or None,
            "asr_normalization_rules": list(meta.get("normalization_rules") or []),
            "asr_latency_ms": (
                float(meta["asr_latency_ms"])
                if isinstance(meta.get("asr_latency_ms"), (int, float))
                else None
            ),
            "asr_refinement_latency_ms": (
                float(meta["latency_ms"])
                if isinstance(meta.get("latency_ms"), (int, float))
                else None
            ),
            "asr_acoustic": acoustic,
        }

    def log_shadow(
        self,
        state: DecisionState,
        *,
        current_route: str | None,
        asr_meta: dict[str, Any] | None = None,
        origin: str | None = None,
    ) -> LinearShadowDecision | None:
        if not self.enabled:
            return None

        decision = self.predict(state.text)
        now = time.time()
        correction_signal = bool(_CORRECTION_RE.search(state.text or ""))
        normalized_origin = str(origin or "").strip().lower()
        # OOD/pilot cards must attach only to real microphone turns. Internal
        # system/NPC/tool prompts are still shadow-logged, but never consume a
        # human test card.
        card = self._active_card() if normalized_origin == "microphone" else None

        record: dict[str, Any] = {
            "schema_version": 1,
            "timestamp_unix": round(now, 3),
            "turn_id": state.turn_id or None,
            "origin": origin or None,
            "text": state.text,
            "directed_to_celia": (
                card.get("directed_to_celia") if isinstance(card, dict) else "unreviewed"
            ),
            "ood_card_id": card.get("id") if isinstance(card, dict) else None,
            "ood_session": card.get("session") if isinstance(card, dict) else None,
            "expected_labels_predeclared": (
                card.get("labels_accepted") if isinstance(card, dict) else None
            ),
            "route_scored": card.get("route_scored") if isinstance(card, dict) else None,
            "current_route": current_route or state.legacy_decision,
            "legacy_route": state.legacy_decision,
            "linear": decision.as_dict(),
            "low_confidence_dev_threshold": 0.113483,
            "low_confidence": decision.margin < 0.113483,
            "deictic": bool(_DEICTIC_RE.search(state.text or "")),
            "pipeline_version": self._payload.get("version") if self._payload else None,
            "pipeline_hash": self._meta.get("pipeline_hash"),
            "artifact_sha256": self._artifact_sha256,
            "semantic_dedup_done": bool(self._meta.get("semantic_dedup_done", False)),
            "correction_signal": correction_signal,
            "correction_of_turn_id": self._last_turn_id if correction_signal else None,
            "previous_linear_route": self._last_route if correction_signal else None,
            "router_latency_ms": round(decision.latency_ms, 4),
            **self._asr_fields(asr_meta),
        }

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._last_turn_id = state.turn_id or None
            self._last_text = state.text
            self._last_route = decision.route
            self._last_at = now
        return decision


linear_router_shadow = LinearRouterShadow()
