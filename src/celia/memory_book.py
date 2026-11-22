import json
import os
from .core.raizes import MEMORY_BOOK
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from loguru import logger


_STOP_WORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "for",
    "in",
    "on",
    "at",
    "is",
    "are",
    "was",
    "were",
    "that",
    "this",
    "with",
    "as",
    "be",
    "by",
    "from",
    "it",
    "i",
    "you",
    "we",
    "they",
    "he",
    "she",
    "eu",
    "tu",
    "voce",
    "voces",
    "ele",
    "ela",
    "nos",
    "de",
    "do",
    "da",
    "dos",
    "das",
    "em",
    "na",
    "no",
    "para",
    "com",
    "por",
    "um",
    "uma",
    "e",
    "ou",
    "que",
    "isso",
    "isto",
    "aquele",
    "aquela",
}

_EMOTION_BLOCK_RE = re.compile(
    r"---\s*ESTADO EMOCIONAL ATUAL.*?---\s*FIM DO ESTADO EMOCIONAL\s*---",
    re.IGNORECASE | re.DOTALL,
)

_INPUT_MARKER_RE = re.compile(r"(?:^|\n)\s*Input:\s*", re.IGNORECASE)

_LEADING_TAG_RE = re.compile(r"^\s*\[[A-Za-z0-9_-]+\]\s*")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_component(value: str) -> str:
    value = os.path.basename((value or "").strip())
    value = re.sub(r"[^\w\-\.]+", "_", value)
    return value or "default"


def normalize_text(text: str) -> str:
    if not text:
        return ""
    txt = unicodedata.normalize("NFKD", text.lower())
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def fingerprint_text(text: str, max_tokens: int = 24) -> str:
    norm = normalize_text(text)
    tokens = re.findall(r"[a-z0-9_]{3,}", norm)
    filtered = [t for t in tokens if t not in _STOP_WORDS]
    if not filtered:
        return norm[:120]
    return " ".join(filtered[:max_tokens])


class MemoryBookStore:
    """Persistent storage for global memory book."""

    # 2026-08-11 (claude): o padrao era a string relativa "memory_book", que
    # dependia de um symlink na raiz. Sem ele, a memoria dela iria pra uma
    # pasta NOVA e vazia no cwd em vez da que existe. Agora vem da raiz unica.
    def __init__(self, conf_uid: str, scope: str = "global", base_dir: str | None = None):
        self.conf_uid = _safe_component(conf_uid)
        self.scope = scope
        self.base_dir = str(base_dir) if base_dir else str(MEMORY_BOOK)
        self.conf_dir = os.path.join(self.base_dir, self.conf_uid)
        self.snapshot_dir = os.path.join(self.conf_dir, "snapshots")
        self.file_path = os.path.join(self.conf_dir, f"{scope}_memory_book.json")
        self._lock = threading.RLock()

        os.makedirs(self.conf_dir, exist_ok=True)
        os.makedirs(self.snapshot_dir, exist_ok=True)

    def _default_data(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "scope": self.scope,
            "conf_uid": self.conf_uid,
            "updated_at": _now_iso(),
            "stats": {
                "updates": 0,
                "compactions": 0,
                "manual_edits": 0,
            },
            "items": [],
        }

    def load(self) -> Dict[str, Any]:
        with self._lock:
            if not os.path.exists(self.file_path):
                data = self._default_data()
                self._atomic_save(data)
                return data

            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("Memory book file is not an object")
            except Exception as e:
                logger.error(f"Failed to read memory book ({self.file_path}): {e}")
                data = self._default_data()
                self._atomic_save(data)
                return data

            data.setdefault("version", 1)
            data.setdefault("scope", self.scope)
            data.setdefault("conf_uid", self.conf_uid)
            data.setdefault("updated_at", _now_iso())
            data.setdefault("stats", {"updates": 0, "compactions": 0, "manual_edits": 0})
            data.setdefault("items", [])
            return data

    def save(self, data: Dict[str, Any]) -> None:
        with self._lock:
            data["updated_at"] = _now_iso()
            self._atomic_save(data)

    def _atomic_save(self, data: Dict[str, Any]) -> None:
        tmp_path = f"{self.file_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.file_path)

    def snapshot(self, data: Dict[str, Any], reason: str, keep_last: int = 10) -> str:
        with self._lock:
            safe_reason = re.sub(r"[^\w\-]+", "_", reason.lower())[:40] or "snapshot"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            snapshot_path = os.path.join(
                self.snapshot_dir,
                f"{ts}_{safe_reason}.json",
            )
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # Rotate: keep only the last N snapshots
            try:
                import glob as _glob
                all_snaps = sorted(
                    _glob.glob(os.path.join(self.snapshot_dir, "*.json")),
                    key=os.path.getmtime,
                )
                for old in all_snaps[:-keep_last]:
                    try:
                        os.remove(old)
                    except Exception:
                        pass
            except Exception:
                pass

            return snapshot_path


class MemoryBookManager:
    """Handles memory book relevance, compaction, and prompt context."""

    def __init__(
        self,
        conf_uid: str,
        enabled: bool = True,
        max_lines: int = 100,
        max_chars: int = 12000,
        compact_every_n_updates: int = 20,
        prompt_top_items: int = 8,
        anti_repeat_window: int = 8,
        lang: str = "en",
        global_scope: bool = True,
        decay_half_life_days: float = 30.0,
        embeddings_enabled: bool = False,
        embeddings_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        semantic_weight: float = 0.5,
        summary_via_llm: bool = False,
    ):
        self.enabled = bool(enabled)
        self.max_lines = max(10, int(max_lines))
        self.max_chars = max(500, int(max_chars))
        self.compact_every_n_updates = max(1, int(compact_every_n_updates))
        self.prompt_top_items = max(1, int(prompt_top_items))
        self.anti_repeat_window = max(2, int(anti_repeat_window))
        self.lang = lang or "en"
        self.global_scope = bool(global_scope)
        self._decay_half_life_days = max(0.0, float(decay_half_life_days))
        self._semantic_weight = max(0.0, min(1.0, float(semantic_weight)))
        self._summary_via_llm = bool(summary_via_llm)

        scope = "global" if self.global_scope else "scoped"
        self.store = MemoryBookStore(conf_uid=conf_uid, scope=scope)

        self._recent_texts: List[str] = []
        self._recent_fingerprints: List[str] = []

        self._embedding_index = None
        if embeddings_enabled:
            try:
                from .memory_embeddings import EmbeddingIndex
                self._embedding_index = EmbeddingIndex(
                    base_dir=self.store.conf_dir,
                    model_name=embeddings_model,
                    enabled=True,
                )
                if self._embedding_index.enabled and self._embedding_index.is_empty:
                    import threading
                    def _backfill():
                        data = self.store.load()
                        self._embedding_index.backfill(data.get("items", []), self._get_summary)
                    threading.Thread(target=_backfill, daemon=True, name="mb-backfill").start()
            except Exception as e:
                logger.warning(f"EmbeddingIndex init failed, running without embeddings: {e}")
                self._embedding_index = None

    @staticmethod
    def _clean_user_text(text: str) -> str:
        if not text:
            return ""

        cleaned = _EMOTION_BLOCK_RE.sub(" ", text)
        parts = _INPUT_MARKER_RE.split(cleaned)
        if len(parts) > 1:
            cleaned = parts[-1]

        cleaned = cleaned.replace("\r", "\n")
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{2,}", "\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _clean_ai_text(text: str) -> str:
        if not text:
            return ""
        cleaned = _LEADING_TAG_RE.sub("", text.strip())
        cleaned = cleaned.replace("\r", "\n")
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{2,}", "\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _is_trivial_turn(user_text: str, ai_text: str) -> bool:
        """Returns True if the turn carries no information worth memorizing."""
        user_clean = normalize_text(user_text or "")
        ai_clean = normalize_text(ai_text or "")

        trivial_user = {
            "oi", "ola", "e ai", "e aí", "opa", "hey", "hi", "hello",
            "ok", "okay", "blz", "beleza", "valeu", "obrigado", "obrigada",
            "tchau", "ate mais", "flw", "abs", "sim", "nao", "nope", "yes",
            ".", "..", "...", "?", "!",
        }
        if user_clean in trivial_user:
            return True

        import re as _re
        if _re.match(r'^[\d\s+\-*/×x().,?=]+$', user_clean):
            return True

        tokens = _re.findall(r'[a-z0-9_]{3,}', user_clean)
        meaningful = [t for t in tokens if t not in _STOP_WORDS]
        if len(meaningful) < 3:
            return True

        if len(ai_clean.strip()) < 20:
            return True

        return False

    @staticmethod
    def _is_low_signal_summary(text: str) -> bool:
        if not text:
            return True
        low_signal_markers = (
            "estado emocional atual",
            "fim do estado emocional",
            "regras de expressao visual",
            "autoconsciencia (obrigatorio)",
            "input:",
            "error calling the chat endpoint",
            "[error from llm",
        )
        lowered = normalize_text(text)
        return any(marker in lowered for marker in low_signal_markers)

    @staticmethod
    def _is_error_ai_text(text: str) -> bool:
        lowered = normalize_text(text)
        error_markers = (
            "error calling the chat endpoint",
            "[error from llm",
            "failed to generate audio",
            "status code: 5",
            "status code: 4",
        )
        return any(marker in lowered for marker in error_markers)

    @staticmethod
    def _token_set(text: str) -> set[str]:
        norm = normalize_text(text)
        tokens = re.findall(r"[a-z0-9_]{3,}", norm)
        return {t for t in tokens if t not in _STOP_WORDS}

    def _extract_topic(self, user_text: str, ai_text: str) -> str:
        base = normalize_text(self._clean_user_text(user_text))
        if not base:
            base = normalize_text(self._clean_ai_text(ai_text))
        if not base:
            return "general"

        rules = {
            "family": ["pai", "mae", "filha", "familia", "criador"],
            "tech": ["codigo", "python", "api", "servidor", "bug", "frontend", "backend"],
            "games": ["jogo", "game", "steam", "xbox", "ps5"],
            "music": ["musica", "song", "spotify", "cant"],
            "feelings": ["triste", "feliz", "ansioso", "emocao", "emotion"],
            "daily": ["hoje", "amanha", "ontem", "rotina", "trabalho"],
        }
        for topic, keys in rules.items():
            if any(k in base for k in keys):
                return topic

        tokens = [t for t in re.findall(r"[a-z0-9_]{3,}", base) if t not in _STOP_WORDS]
        if not tokens:
            return "general"
        return "_".join(tokens[:3])[:48]

    @staticmethod
    def _get_summary(item: Dict[str, Any]) -> str:
        return (item.get("summary") or item.get("summary_en") or "")

    def _summary_en(self, user_text: str, ai_text: str, topic: str) -> str:
        user_short = self._clean_user_text(user_text).replace("\n", " ")[:180]
        ai_short = self._clean_ai_text(ai_text).replace("\n", " ")[:200]
        if not user_short:
            user_short = "No explicit user text provided."
        if not ai_short:
            ai_short = "No explicit AI response provided."
        summary = (
            f"[{topic}] O usuário assumiu que: '{user_short}'. E eu (Célia) respondi: '{ai_short}'."
        )
        return summary[:320]

    def _parse_dt(self, value: str) -> datetime:
        if not value:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return datetime.now(timezone.utc)

    def _days_since(self, iso_ts: str) -> float:
        dt = self._parse_dt(iso_ts)
        now = datetime.now(timezone.utc)
        delta = now - dt
        return max(delta.total_seconds() / 86400.0, 0.0)

    def _days_since_history_uid(self, history_uid: str) -> Optional[float]:
        if not history_uid:
            return None
        try:
            ts = (history_uid or "").split("_", 2)[:2]
            if len(ts) < 2:
                return None
            dt = datetime.strptime(f"{ts[0]}_{ts[1]}", "%Y-%m-%d_%H-%M-%S").replace(
                tzinfo=timezone.utc
            )
            now = datetime.now(timezone.utc)
            delta = now - dt
            return max(delta.total_seconds() / 86400.0, 0.0)
        except Exception:
            return None

    def _dynamic_score(self, item: Dict[str, Any]) -> float:
        base = float(item.get("relevance_score", 0.0))
        mentions = int(item.get("mentions_count", 1))
        recency_days = self._days_since(item.get("last_seen_at", ""))
        recency_bonus = max(1.8 - (recency_days * 0.05), 0.0)
        history_recency_days = self._days_since_history_uid(
            str(item.get("source_history_uid") or "")
        )
        history_recency_bonus = (
            max(1.2 - (history_recency_days * 0.03), 0.0)
            if history_recency_days is not None
            else 0.0
        )
        mention_bonus = min(mentions, 10) * 0.2
        pinned_bonus = 100.0 if item.get("pinned") else 0.0
        score = base + mention_bonus + recency_bonus + history_recency_bonus + pinned_bonus
        if not item.get("pinned") and self._decay_half_life_days > 0:
            decay = 0.5 ** (recency_days / self._decay_half_life_days)
            score *= decay
        return score

    def _total_chars(self, items: List[Dict[str, Any]]) -> int:
        return sum(len(self._get_summary(it)) for it in items)

    def _find_merge_candidate(
        self,
        items: List[Dict[str, Any]],
        topic: str,
        new_summary: str,
        new_fp: str,
    ) -> Optional[Dict[str, Any]]:
        norm_new = normalize_text(new_summary)
        for item in items:
            if item.get("topic") != topic:
                continue
            old_summary = self._get_summary(item)
            old_fp = fingerprint_text(old_summary)
            if old_fp and old_fp == new_fp:
                return item
            sim = SequenceMatcher(None, norm_new, normalize_text(old_summary)).ratio()
            if sim >= 0.88:
                return item
        return None

    def _compact_items(self, items: List[Dict[str, Any]], sync_embeddings: bool = False) -> List[Dict[str, Any]]:
        # 1) semantic-ish dedupe by fingerprint
        by_fp: Dict[str, Dict[str, Any]] = {}
        for item in items:
            fp = fingerprint_text(self._get_summary(item))
            key = f"{item.get('topic', 'general')}::{fp}"
            if key not in by_fp:
                by_fp[key] = item
                continue

            winner = by_fp[key]
            candidate = item
            if self._dynamic_score(candidate) > self._dynamic_score(winner):
                winner, candidate = candidate, winner

            winner["mentions_count"] = int(winner.get("mentions_count", 1)) + int(candidate.get("mentions_count", 1))
            winner["relevance_score"] = float(winner.get("relevance_score", 0.0)) + float(candidate.get("relevance_score", 0.0)) * 0.4
            winner["last_seen_at"] = max(
                str(winner.get("last_seen_at") or ""),
                str(candidate.get("last_seen_at") or ""),
            )
            by_fp[key] = winner

        compacted = list(by_fp.values())

        # 2) relevance expiration (non-pinned, weak, and old)
        filtered: List[Dict[str, Any]] = []
        for item in compacted:
            if item.get("pinned"):
                filtered.append(item)
                continue
            score = self._dynamic_score(item)
            old_days = self._days_since(item.get("last_seen_at", ""))
            if score < 1.2 and old_days > 10 and int(item.get("mentions_count", 1)) <= 1:
                continue
            filtered.append(item)

        # 3) enforce limits by dropping lowest score first
        filtered.sort(key=self._dynamic_score, reverse=True)

        while len(filtered) > self.max_lines:
            # remove lowest non-pinned
            idx = next((i for i in range(len(filtered) - 1, -1, -1) if not filtered[i].get("pinned")), None)
            if idx is None:
                break
            filtered.pop(idx)

        while self._total_chars(filtered) > self.max_chars:
            idx = next((i for i in range(len(filtered) - 1, -1, -1) if not filtered[i].get("pinned")), None)
            if idx is None:
                break
            filtered.pop(idx)

        if sync_embeddings and self._embedding_index:
            kept_ids = {it.get("id") for it in filtered}
            removed_ids = [it.get("id") for it in items if it.get("id") and it.get("id") not in kept_ids]
            if removed_ids:
                self._embedding_index.remove_many(removed_ids)

        return filtered

    def _save_compacted(self, data: Dict[str, Any], reason: str) -> Dict[str, Any]:
        data["items"] = self._compact_items(data.get("items", []), sync_embeddings=True)
        stats = data.setdefault("stats", {})
        stats["compactions"] = int(stats.get("compactions", 0)) + 1
        self.store.save(data)
        try:
            snap_path = self.store.snapshot(data, reason=reason)
            logger.info(f"MemoryBook snapshot created: {snap_path}")
        except Exception as e:
            logger.warning(f"Could not create MemoryBook snapshot: {e}")
        return data

    def compact_now(self, reason: str = "manual") -> Dict[str, Any]:
        data = self.store.load()
        return self._save_compacted(data, reason)

    def on_new_history(self, history_uid: str) -> None:
        self.compact_now(reason=f"new_history_{history_uid[:16]}")

    def build_memory_prompt(self, user_text: str) -> str:
        if not self.enabled:
            return ""
        selected = self.retrieve_relevant_items(
            user_text=user_text,
            max_chars=min(max(self.max_chars // 3, 1200), 2600),
            min_items=max(2, self.prompt_top_items // 2),
        )
        if not selected:
            return ""

        lines = []
        for item in selected:
            topic = item.get("topic", "general")
            summary = self._get_summary(item).strip()
            if not summary:
                continue
            if self._is_low_signal_summary(summary):
                continue
            lines.append(f"- [{topic}] {summary}")

        if not lines:
            return ""

        return (
            "\n\n<minhas_lembrancas_internas>\n"
            "Estas são minhas memórias de conversas passadas que podem ser relevantes agora:\n"
            + "\n".join(lines)
            + "\nSe eu precisar falar sobre o passado, uso isso como minhas lembranças orgânicas.\n"
            "</minhas_lembrancas_internas>"
        )

    def _query_score(
        self,
        item: Dict[str, Any],
        current_topic: str,
        query_tokens: set[str],
        semantic_scores: Optional[Dict[str, float]] = None,
    ) -> float:
        query_bonus = 0.0
        if query_tokens:
            item_tokens = self._token_set(self._get_summary(item))
            if item_tokens:
                overlap = len(query_tokens.intersection(item_tokens))
                if overlap == 0:
                    query_bonus = -0.25
                else:
                    query_bonus = (
                        min(overlap / max(len(query_tokens), 1), 1.0) * 3.2
                    )
            else:
                query_bonus = -0.15
        topic_bonus = 1.2 if item.get("topic") == current_topic else 0.0
        heuristic = self._dynamic_score(item) + topic_bonus + query_bonus
        if semantic_scores and self._semantic_weight > 0:
            sem = semantic_scores.get(item.get("id", ""), 0.0)
            heuristic += sem * self._semantic_weight * 4.0
        return heuristic

    def retrieve_relevant_items(
        self,
        user_text: str,
        max_chars: int = 900,
        min_items: int = 2,
    ) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        data = self.store.load()
        items = data.get("items", [])
        if not items:
            return []

        clean_user_text = self._clean_user_text(user_text)
        current_topic = self._extract_topic(clean_user_text, "")
        query_tokens = self._token_set(clean_user_text)

        semantic_scores: Optional[Dict[str, float]] = None
        if self._embedding_index and clean_user_text:
            results = self._embedding_index.search(clean_user_text, top_k=len(items))
            if results:
                semantic_scores = {iid: score for iid, score in results}

        scored: List[tuple[float, Dict[str, Any]]] = []
        for item in items:
            summary = self._get_summary(item).strip()
            if not summary or self._is_low_signal_summary(summary):
                continue
            score = self._query_score(item, current_topic, query_tokens, semantic_scores)
            scored.append((score, item))

        if not scored:
            return []

        scored.sort(key=lambda pair: pair[0], reverse=True)

        top_score = scored[0][0]
        dynamic_floor = max(1.0, top_score * 0.55)
        char_budget = max(280, int(max_chars))
        min_items = max(1, int(min_items))

        selected: List[Dict[str, Any]] = []
        total_chars = 0
        for score, item in scored:
            summary = self._get_summary(item).strip()
            if not summary:
                continue

            if score < dynamic_floor and len(selected) >= min_items:
                break

            projected = total_chars + len(summary)
            if projected > char_budget and len(selected) >= min_items:
                break

            selected.append(item)
            total_chars = projected

        if len(selected) < min_items:
            for _, item in scored:
                if item in selected:
                    continue
                selected.append(item)
                if len(selected) >= min_items:
                    break

        return selected

    def clear_recent_responses(self) -> None:
        self._recent_texts = []
        self._recent_fingerprints = []

    def record_recent_response(self, text: str) -> None:
        if not text:
            return
        self._recent_texts.append(text)
        self._recent_fingerprints.append(fingerprint_text(text))
        if len(self._recent_texts) > self.anti_repeat_window:
            self._recent_texts = self._recent_texts[-self.anti_repeat_window :]
            self._recent_fingerprints = self._recent_fingerprints[-self.anti_repeat_window :]

    def build_antirepeat_prompt(self) -> str:
        if not self._recent_texts:
            return ""

        unique_recent: List[str] = []
        seen = set()
        for text in reversed(self._recent_texts):
            fp = fingerprint_text(text)
            if fp in seen:
                continue
            seen.add(fp)
            unique_recent.append(text.strip().replace("\n", " ")[:160])
            if len(unique_recent) >= 4:
                break

        if not unique_recent:
            return ""

        lines = [f"- {txt}" for txt in unique_recent]
        return (
            "\n\n<anti_repeticao>\n"
            "Para não soar robótica, evite repetir a MESMA construção de frase que usei recentemente:\n"
            + "\n".join(lines)
            + "\nMantenha o sentido, mas varie o tom e as palavras!\n"
            "</anti_repeticao>"
        )

    def is_repetitive(self, text: str, threshold: float = 0.90) -> bool:
        norm = normalize_text(text)
        fp = fingerprint_text(text)
        for old_text, old_fp in zip(self._recent_texts, self._recent_fingerprints):
            if old_fp and old_fp == fp:
                return True
            sim = SequenceMatcher(None, norm, normalize_text(old_text)).ratio()
            if sim >= threshold:
                return True
        return False

    def build_pinned_prompt(self, max_chars: int = 400, max_items: int = 3) -> str:
        """Returns a prompt block with pinned lore items, always injected regardless of recall trigger."""
        if not self.enabled:
            return ""
        data = self.store.load()
        pinned = [it for it in data.get("items", []) if it.get("pinned")]
        if not pinned:
            return ""
        pinned.sort(key=self._dynamic_score, reverse=True)
        lines = []
        total = 0
        for item in pinned[:max_items]:
            summary = self._get_summary(item).strip()
            if not summary or self._is_low_signal_summary(summary):
                continue
            if total + len(summary) > max_chars:
                break
            lines.append(f"- {summary}")
            total += len(summary)
        if not lines:
            return ""
        return (
            "\n\n<lore_persistente>\n"
            "Fatos fixos sobre mim e meu mundo que sempre valem:\n"
            + "\n".join(lines)
            + "\n</lore_persistente>"
        )

    def purge_trivial_items(self) -> int:
        """Remove existing memory items that look trivial. Respects pinned=True. Returns count removed."""
        data = self.store.load()
        items = data.get("items", [])
        kept = []
        removed = 0
        for item in items:
            if item.get("pinned"):
                kept.append(item)
                continue
            summary = self._get_summary(item)
            if self._is_low_signal_summary(summary):
                removed += 1
                continue
            # Extract user/ai from the mechanical summary format to re-check triviality
            import re as _re
            m = _re.search(r"O usu[aá]rio assumiu que:\s*'(.+?)'\.\s*E eu.*?respondi:\s*'(.+?)'", summary, _re.DOTALL)
            if m:
                u, a = m.group(1), m.group(2)
                if self._is_trivial_turn(u, a):
                    removed += 1
                    continue
            elif len(summary.strip()) < 30:
                removed += 1
                continue
            kept.append(item)
        if removed:
            data["items"] = kept
            self.store.save(data)
            logger.info(f"MemoryBook purged {removed} trivial items, {len(kept)} kept.")
        return removed

    def update_from_turn(
        self,
        user_text: str,
        ai_text: str,
        source_kind: str,
        source_history_uid: str,
        pre_computed_summary: Optional[str] = None,
    ) -> None:
        if not self.enabled:
            return
        if not ai_text or not ai_text.strip():
            return

        ai_clean = self._clean_ai_text(ai_text)
        if self._is_error_ai_text(ai_clean):
            return

        if self._is_trivial_turn(user_text, ai_clean):
            logger.debug("MemoryBook: trivial turn skipped.")
            return

        topic = self._extract_topic(user_text, ai_text)
        summary = (pre_computed_summary.strip()[:320] if pre_computed_summary and pre_computed_summary.strip()
                   else self._summary_en(user_text, ai_clean, topic))
        fp = fingerprint_text(summary)

        data = self.store.load()
        items = data.get("items", [])

        now = _now_iso()
        candidate = self._find_merge_candidate(items, topic, summary, fp)

        if candidate:
            candidate["summary"] = summary
            candidate["source_kind"] = source_kind
            candidate["source_history_uid"] = source_history_uid
            candidate["updated_at"] = now
            candidate["last_seen_at"] = now
            candidate["mentions_count"] = int(candidate.get("mentions_count", 1)) + 1
            candidate["relevance_score"] = float(candidate.get("relevance_score", 1.0)) + 1.0
            if self._embedding_index:
                self._embedding_index.add(candidate["id"], summary)
        else:
            new_id = uuid.uuid4().hex
            items.append(
                {
                    "id": new_id,
                    "topic": topic,
                    "summary": summary,
                    "source_kind": source_kind,
                    "source_history_uid": source_history_uid,
                    "created_at": now,
                    "updated_at": now,
                    "mentions_count": 1,
                    "last_seen_at": now,
                    "relevance_score": 1.0,
                    "pinned": False,
                }
            )
            if self._embedding_index:
                self._embedding_index.add(new_id, summary)

        stats = data.setdefault("stats", {})
        stats["updates"] = int(stats.get("updates", 0)) + 1

        must_compact = (
            len(items) > self.max_lines
            or self._total_chars(items) > self.max_chars
            or (stats["updates"] % self.compact_every_n_updates == 0)
        )

        if must_compact:
            data = self._save_compacted(data, reason="periodic")
        else:
            self.store.save(data)

        self.record_recent_response(ai_text)

    def list_items(self, limit: int = 200) -> List[Dict[str, Any]]:
        data = self.store.load()
        items = sorted(data.get("items", []), key=self._dynamic_score, reverse=True)
        return items[: max(1, int(limit))]

    def edit_item(self, item_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        data = self.store.load()
        items = data.get("items", [])
        now = _now_iso()

        for item in items:
            if item.get("id") != item_id:
                continue

            allowed = {"topic", "summary", "summary_en", "pinned", "relevance_score"}
            applied = {}
            for key, value in (updates or {}).items():
                if key not in allowed:
                    continue
                if key == "topic":
                    value = (str(value or "general")[:48]).strip() or "general"
                elif key in ("summary", "summary_en"):
                    value = (str(value or "")[:500]).strip()
                    key = "summary"
                elif key == "pinned":
                    value = bool(value)
                elif key == "relevance_score":
                    try:
                        value = float(value)
                    except Exception:
                        continue
                item[key] = value
                applied[key] = value

            item["updated_at"] = now
            item["last_seen_at"] = now

            stats = data.setdefault("stats", {})
            stats["manual_edits"] = int(stats.get("manual_edits", 0)) + 1

            self.store.save(data)
            logger.info(f"MemoryBook manual edit item={item_id} fields={list(applied.keys())}")
            return item

        return None

    def delete_item(self, item_id: str) -> bool:
        data = self.store.load()
        items = data.get("items", [])
        new_items = [it for it in items if it.get("id") != item_id]
        if len(new_items) == len(items):
            return False

        data["items"] = new_items
        self.store.save(data)
        if self._embedding_index:
            self._embedding_index.remove(item_id)
        logger.info(f"MemoryBook manual delete item={item_id}")
        return True

    def clear(self) -> bool:
        data = self.store.load()
        data["items"] = []
        self.store.save(data)
        if self._embedding_index:
            self._embedding_index.clear()
        logger.info("MemoryBook manual clear all items")
        return True

    def delete_items_by_source(self, history_uid: str) -> int:
        if not history_uid:
            return 0
        data = self.store.load()
        items = data.get("items", [])
        to_remove = [it for it in items if it.get("source_history_uid") == history_uid]
        removed = len(to_remove)
        if removed:
            data["items"] = [it for it in items if it.get("source_history_uid") != history_uid]
            try:
                self.store.snapshot(data, reason=f"pre_purge_{history_uid[:16]}")
            except Exception as e:
                logger.warning(f"Could not create pre-purge snapshot: {e}")
            self.store.save(data)
            if self._embedding_index:
                self._embedding_index.remove_many([it.get("id") for it in to_remove if it.get("id")])
            logger.info(f"MemoryBook purged {removed} items for history_uid={history_uid}")
        return removed
