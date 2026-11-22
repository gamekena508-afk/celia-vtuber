"""Memória visual da Célia: mudança de quadro, OCR incremental, estado da cena e frames em sequência.

Tudo aqui é puro (sem rede, sem GPU, sem relógio escondido) para ser testável. O `screen_vision` orquestra:

    frame -> assinatura/mudança -> perfil -> (OCR) -> (VLM) -> estado da cena

Regras que vieram do pedido do Pai (2026-10-18):
- OCR é PISTA, nunca verdade: o texto vive separado da interpretação do modelo e a imagem segue como conferência.
- Estado atual e histórico de mudanças são coisas diferentes, e as duas têm tamanho máximo.
- Personagem reconhecido numa cena não vale para sempre: mudança grande invalida o que não foi reconfirmado.
- Nada de movimento ou ordem temporal afirmados a partir de UMA imagem.
"""
from __future__ import annotations

import base64
import binascii
import io
import os
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# ───────────────────────────── configuração (tudo por ambiente) ─────────────────────────────


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except ValueError:
        return default


GRID_W, GRID_H = 64, 36          # 4x mais colunas e linhas que os 16x9 do navegador: enxerga linha de texto/caixa de diálogo
BLOCK_COLS, BLOCK_ROWS = 8, 6    # a grade é agrupada em 8x6 blocos para localizar ONDE mudou


# ───────────────────────────── 1. mudança entre quadros ─────────────────────────────

KIND_STATIC = "static"           # praticamente igual: não gasta VLM nem OCR
KIND_LOCAL = "localized"         # região pequena mudou (caixa de diálogo, cursor, HUD)
KIND_TEXT = "text"               # faixa que parece texto mudou: atualiza OCR, não reanalisa a cena
KIND_MAJOR = "major"             # cena/layout mudou: reanalisa
KIND_BURST = "burst"             # mudança grande e repetida em seguida: movimento/vídeo


@dataclass(frozen=True)
class FrameSignature:
    gray: bytes                  # GRID_W*GRID_H tons de cinza
    edge: bytes                  # mesma grade, só gradiente horizontal (assinatura de texto)
    width: int
    height: int

    def blocks(self) -> list[float]:
        """Média de cinza por bloco (BLOCK_COLS x BLOCK_ROWS)."""
        out = []
        bw, bh = GRID_W // BLOCK_COLS, GRID_H // BLOCK_ROWS
        for by in range(BLOCK_ROWS):
            for bx in range(BLOCK_COLS):
                total = 0
                for y in range(by * bh, (by + 1) * bh):
                    row = y * GRID_W
                    total += sum(self.gray[row + bx * bw: row + (bx + 1) * bw])
                out.append(total / (bw * bh))
        return out


@dataclass(frozen=True)
class FrameChange:
    kind: str
    global_diff: float           # 0..1, média |a-b|/255 em toda a grade
    local_max: float             # 0..1, maior diferença média em UM bloco
    changed_blocks: tuple[int, ...]
    text_ratio: float            # fração da mudança que está no gradiente (bordas/texto) e não na luminância
    region: tuple[float, float, float, float] | None  # x0,y0,x1,y1 normalizados da área alterada

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "global_diff": round(self.global_diff, 4), "local_max": round(self.local_max, 4),
            "changed_blocks": list(self.changed_blocks), "text_ratio": round(self.text_ratio, 3),
            "region": [round(v, 3) for v in self.region] if self.region else None,
        }


def decode_data_url(image: str) -> bytes | None:
    if not isinstance(image, str) or "," not in image:
        return None
    try:
        return base64.b64decode(image.split(",", 1)[1], validate=True)
    except (binascii.Error, ValueError):
        return None


def signature_of(image: str | bytes) -> FrameSignature | None:
    """Assinatura 64x36 (cinza + gradiente) de um data-URL/bytes JPEG/PNG. None se não for imagem decodificável."""
    raw = decode_data_url(image) if isinstance(image, str) else image
    if not raw:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            width, height = im.size
            small = im.convert("L").resize((GRID_W, GRID_H), Image.BILINEAR)
            gray = small.tobytes()
    except Exception:
        return None
    edge = bytearray(GRID_W * GRID_H)
    for y in range(GRID_H):
        row = y * GRID_W
        for x in range(1, GRID_W):
            edge[row + x] = min(255, abs(gray[row + x] - gray[row + x - 1]) * 4)
    return FrameSignature(gray=bytes(gray), edge=bytes(edge), width=width, height=height)


def compare(prev: FrameSignature | None, cur: FrameSignature) -> FrameChange:
    """Classifica a mudança. Limiares por ambiente; padrões medidos em tests/test_visual_scene.py."""
    if prev is None:
        return FrameChange(KIND_MAJOR, 1.0, 1.0, tuple(range(BLOCK_COLS * BLOCK_ROWS)), 0.0, (0.0, 0.0, 1.0, 1.0))
    n = GRID_W * GRID_H
    gdiff = sum(abs(a - b) for a, b in zip(prev.gray, cur.gray)) / (n * 255)
    ediff = sum(abs(a - b) for a, b in zip(prev.edge, cur.edge)) / (n * 255)
    pb, cb = prev.blocks(), cur.blocks()
    per_block = [abs(a - b) / 255 for a, b in zip(pb, cb)]
    # bloco por "diferença de gradiente" também: texto muda o gradiente sem mexer muito na média de cinza
    bw, bh = GRID_W // BLOCK_COLS, GRID_H // BLOCK_ROWS
    for by in range(BLOCK_ROWS):
        for bx in range(BLOCK_COLS):
            tot = 0
            for y in range(by * bh, (by + 1) * bh):
                row = y * GRID_W
                tot += sum(abs(prev.edge[row + x] - cur.edge[row + x]) for x in range(bx * bw, (bx + 1) * bw))
            per_block[by * BLOCK_COLS + bx] = max(per_block[by * BLOCK_COLS + bx], tot / (bw * bh * 255) * 2.0)
    block_thr = _f("CELIA_VISION_BLOCK_THRESHOLD", 0.045)
    changed = tuple(i for i, v in enumerate(per_block) if v >= block_thr)
    local_max = max(per_block) if per_block else 0.0
    static_thr = _f("CELIA_VISION_STATIC_THRESHOLD", 0.006)
    major_thr = _f("CELIA_VISION_MAJOR_THRESHOLD", 0.12)
    burst_thr = _f("CELIA_VISION_BURST_THRESHOLD", 0.30)
    text_ratio = ediff / (gdiff + ediff) if (gdiff + ediff) > 0 else 0.0
    region = None
    if changed:
        xs = [i % BLOCK_COLS for i in changed]
        ys = [i // BLOCK_COLS for i in changed]
        region = (min(xs) / BLOCK_COLS, min(ys) / BLOCK_ROWS, (max(xs) + 1) / BLOCK_COLS, (max(ys) + 1) / BLOCK_ROWS)
    coverage = len(changed) / (BLOCK_COLS * BLOCK_ROWS)
    if gdiff < static_thr and local_max < block_thr:
        kind = KIND_STATIC
    elif gdiff >= burst_thr or (gdiff >= major_thr and coverage >= 0.6 and text_ratio < 0.4):
        kind = KIND_BURST if gdiff >= burst_thr else KIND_MAJOR
    elif gdiff >= major_thr or coverage >= 0.5:
        kind = KIND_MAJOR
    elif text_ratio >= _f("CELIA_VISION_TEXT_RATIO", 0.55) and coverage <= 0.34:
        kind = KIND_TEXT
    else:
        kind = KIND_LOCAL
    return FrameChange(kind, gdiff, local_max, changed, text_ratio, region)


# ───────────────────────────── 2. buffer circular de frames ─────────────────────────────


@dataclass
class BufferedFrame:
    frame_id: int
    source: str
    image: str
    signature: FrameSignature
    at: float                      # monotonic
    kind: str = KIND_MAJOR
    gdiff: float = 0.0


class FrameRing:
    """Buffer circular com teto de quantidade, de bytes e de idade; descarta quase-duplicatas na entrada."""

    def __init__(self, max_frames: int | None = None, max_bytes: int | None = None, max_age_s: float | None = None) -> None:
        self.max_frames = max_frames if max_frames is not None else _i("CELIA_VISION_RING_FRAMES", 12)
        self.max_bytes = max_bytes if max_bytes is not None else _i("CELIA_VISION_RING_BYTES", 12 * 1024 * 1024)
        self.max_age_s = max_age_s if max_age_s is not None else _f("CELIA_VISION_RING_MAX_AGE", 20.0)
        self._frames: deque[BufferedFrame] = deque()
        self._seq = 0
        self.dropped_redundant = 0
        self.evicted = 0

    def __len__(self) -> int:
        return len(self._frames)

    def bytes_used(self) -> int:
        return sum(len(f.image) for f in self._frames)

    def _expire(self, now: float) -> None:
        while self._frames and now - self._frames[0].at > self.max_age_s:
            self._frames.popleft()
            self.evicted += 1

    def push(self, source: str, image: str, signature: FrameSignature, now: float, kind: str = KIND_MAJOR,
             gdiff: float = 0.0) -> BufferedFrame | None:
        """Guarda o frame. Devolve None quando ele é redundante (igual ao último desta fonte)."""
        self._expire(now)
        last = next((f for f in reversed(self._frames) if f.source == source), None)
        if last is not None and kind == KIND_STATIC:
            self.dropped_redundant += 1
            return None
        self._seq += 1
        frame = BufferedFrame(self._seq, source, image, signature, now, kind, gdiff)
        self._frames.append(frame)
        while len(self._frames) > max(1, self.max_frames) or (len(self._frames) > 1 and self.bytes_used() > self.max_bytes):
            self._frames.popleft()
            self.evicted += 1
        return frame

    def window(self, source: str, now: float, *, before_s: float = 6.0, limit: int = 3,
               min_gap_diff: float = 0.02) -> list[BufferedFrame]:
        """Até `limit` frames recentes desta fonte, em ordem cronológica, sem quase-duplicatas e SEMPRE com o mais novo."""
        self._expire(now)
        pool = [f for f in self._frames if f.source == source and now - f.at <= before_s]
        if not pool:
            return []
        chosen: list[BufferedFrame] = [pool[0]]
        for frame in pool[1:]:
            if compare(chosen[-1].signature, frame.signature).global_diff >= min_gap_diff:
                chosen.append(frame)
        if chosen[-1].frame_id != pool[-1].frame_id:
            chosen.append(pool[-1])
        limit = max(1, limit)
        if len(chosen) > limit:  # mantém primeiro, último e distribui o resto
            step = (len(chosen) - 1) / (limit - 1) if limit > 1 else 0
            chosen = [chosen[round(i * step)] for i in range(limit)] if limit > 1 else [chosen[-1]]
        return chosen

    def clear(self, source: str | None = None) -> None:
        if source is None:
            self._frames.clear()
        else:
            self._frames = deque(f for f in self._frames if f.source != source)

    def snapshot(self, now: float) -> dict[str, Any]:
        oldest = self._frames[0].at if self._frames else None
        return {"frames": len(self._frames), "bytes": self.bytes_used(), "max_frames": self.max_frames,
                "oldest_age_s": round(now - oldest, 2) if oldest is not None else None,
                "dropped_redundant": self.dropped_redundant, "evicted": self.evicted}


def _centroid(change: FrameChange) -> tuple[float, float] | None:
    if not change.changed_blocks:
        return None
    xs = [(i % BLOCK_COLS + 0.5) / BLOCK_COLS for i in change.changed_blocks]
    ys = [(i // BLOCK_COLS + 0.5) / BLOCK_ROWS for i in change.changed_blocks]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def sequence_timeline(frames: list[BufferedFrame]) -> list[dict[str, Any]]:
    """Linha do tempo DETERMINÍSTICA da sequência (observação direta, não inferência): idade, mudança e centro da região alterada."""
    if not frames:
        return []
    newest = frames[-1].at
    out = []
    prev = None
    for frame in frames:
        item = {"frame_id": frame.frame_id, "t_rel_s": round(frame.at - newest, 2)}
        if prev is not None:
            change = compare(prev.signature, frame.signature)
            item["change_from_previous"] = change.as_dict()
            c = _centroid(change)
            if c:
                item["changed_centroid"] = [round(c[0], 3), round(c[1], 3)]
        out.append(item)
        prev = frame
    return out


def describe_motion(timeline: list[dict[str, Any]], min_shift: float = 0.08) -> str:
    """Movimento MEDIDO pelo detector (não pelo modelo). A direção só sai com 3+ quadros (2+ deslocamentos do centro da região
    alterada); com 2 quadros a região alterada contém posição antiga e nova juntas e a direção é indeterminada."""
    pairs = [t for t in timeline if "changed_centroid" in t]
    if not timeline or len(timeline) < 2:
        return ""
    if not pairs:
        return "nenhuma mudança medida entre os quadros"
    if len(pairs) < 2:
        return "houve mudança entre os 2 quadros; com 2 quadros a direção do movimento é indeterminada"
    dx = pairs[-1]["changed_centroid"][0] - pairs[0]["changed_centroid"][0]
    dy = pairs[-1]["changed_centroid"][1] - pairs[0]["changed_centroid"][1]
    parts = []
    if abs(dx) >= min_shift:
        parts.append("da esquerda para a direita" if dx > 0 else "da direita para a esquerda")
    if abs(dy) >= min_shift:
        parts.append("de cima para baixo" if dy > 0 else "de baixo para cima")
    return ("região alterada se desloca " + " e ".join(parts)) if parts else "região alterada mudou mas sem deslocamento líquido claro (pode ser mudança de conteúdo no lugar)"


# ───────────────────────────── 3. OCR incremental ─────────────────────────────


@dataclass(frozen=True)
class OcrLine:
    text: str
    box: tuple[float, float, float, float]   # x0,y0,x1,y1 normalizados 0..1
    conf: float | None = None                  # 0..100 quando o motor informa


_PLACEHOLDER_EV = ("pista visual", "pista 1", "evidence", "o que voce ve", "o que você vê", "<")


def _is_placeholder_evidence(text: str) -> bool:
    t = _norm_text(text)
    return not t or any(_norm_text(p) in t for p in _PLACEHOLDER_EV if _norm_text(p))


def _norm_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


@dataclass(frozen=True)
class OcrDiff:
    added: tuple[str, ...]
    changed: tuple[tuple[str, str], ...]     # (antes, depois) na mesma posição aproximada
    removed: tuple[str, ...]
    unchanged: int

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.changed or self.removed)

    def as_dict(self) -> dict[str, Any]:
        return {"added": list(self.added), "changed": [list(c) for c in self.changed], "removed": list(self.removed),
                "unchanged": self.unchanged}


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, _norm_text(a), _norm_text(b)).ratio()


def diff_ocr(prev: list[OcrLine], cur: list[OcrLine]) -> OcrDiff:
    """Compara dois OCRs por linha: igual (normalizada), alterada (mesma região, texto parecido) ou nova/removida."""
    prev_left = list(prev)
    added: list[str] = []
    changed: list[tuple[str, str]] = []
    unchanged = 0
    remaining: list[OcrLine] = []
    for line in cur:
        key = _norm_text(line.text)
        match = next((p for p in prev_left if _norm_text(p.text) == key), None)
        if match is not None:
            prev_left.remove(match)
            unchanged += 1
        else:
            remaining.append(line)
    for line in remaining:
        near = [p for p in prev_left if abs(p.box[1] - line.box[1]) < 0.06 and _similar(p.text, line.text) >= 0.45]
        if near:
            best = max(near, key=lambda p: _similar(p.text, line.text))
            prev_left.remove(best)
            changed.append((best.text, line.text))
        else:
            added.append(line.text)
    return OcrDiff(tuple(added), tuple(changed), tuple(p.text for p in prev_left), unchanged)


def merge_region(prev: list[OcrLine], new: list[OcrLine], region: tuple[float, float, float, float] | None) -> list[OcrLine]:
    """OCR recortado: troca só as linhas de `prev` cujo centro está na região relida e mantém o resto."""
    if not region:
        return list(new)
    x0, y0, x1, y1 = region
    keep = [l for l in prev if not (x0 - 0.02 <= (l.box[0] + l.box[2]) / 2 <= x1 + 0.02 and y0 - 0.02 <= (l.box[1] + l.box[3]) / 2 <= y1 + 0.02)]
    merged = keep + list(new)
    return sorted(merged, key=lambda l: (round(l.box[1], 2), l.box[0]))


# ───────────────────────────── 4. perfis ─────────────────────────────

PROFILE_STATIC = "static_interface"
PROFILE_GAME = "game_dialog"
PROFILE_MOTION = "motion_video"
PROFILE_DETAIL = "detail_on_demand"
PROFILES = (PROFILE_STATIC, PROFILE_GAME, PROFILE_MOTION, PROFILE_DETAIL)

ACTION_SKIP = "skip"             # nada a fazer
ACTION_OCR_ONLY = "ocr_only"     # só atualiza texto
ACTION_VLM = "vlm"               # OCR (se útil) + VLM de um frame
ACTION_SEQUENCE = "sequence"     # OCR + VLM sobre frames em sequência


@dataclass(frozen=True)
class Decision:
    profile: str
    action: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"profile": self.profile, "action": self.action, "reason": self.reason}


def classify_profile(*, burst_recent: int, text_changes_recent: int, major_recent: int, has_scene: bool,
                     explicit: bool, override: str | None = None) -> tuple[str, str]:
    """Escolhe o perfil por sinais baratos; na dúvida, o mais conservador (interface estática)."""
    override = (override or os.getenv("CELIA_VISION_PROFILE", "auto")).strip().lower()
    if override in PROFILES:
        return override, "configurado"
    if explicit:
        return PROFILE_DETAIL, "pedido explicito"
    if burst_recent >= 2:
        return PROFILE_MOTION, f"{burst_recent} mudancas bruscas seguidas"
    if has_scene and text_changes_recent >= 2 and major_recent == 0:
        return PROFILE_GAME, "texto muda sobre cena estavel"
    return PROFILE_STATIC, "padrao conservador"


def decide(profile: str, change: FrameChange, *, has_scene: bool, scene_age_s: float | None, explicit: bool) -> Decision:
    """O que fazer com ESTE quadro dado o perfil. Mudança importante nunca é ignorada por já ter havido análise antes."""
    kind = change.kind
    if explicit or profile == PROFILE_DETAIL:
        return Decision(profile, ACTION_VLM, "analise detalhada pedida")
    if kind == KIND_STATIC:
        stale_after = _f("CELIA_VISION_SCENE_REFRESH_S", 90.0)
        if has_scene and scene_age_s is not None and scene_age_s > stale_after:
            return Decision(profile, ACTION_VLM, f"cena parada ha {scene_age_s:.0f}s: reconfirmar")
        return Decision(profile, ACTION_SKIP, "tela estatica")
    if profile == PROFILE_MOTION:
        return Decision(PROFILE_MOTION, ACTION_SEQUENCE, "movimento: mudancas bruscas repetidas")
    if not has_scene:
        return Decision(profile, ACTION_VLM, "sem cena em memoria")
    if kind in (KIND_MAJOR, KIND_BURST):
        return Decision(profile, ACTION_VLM, "mudanca visual importante")
    if kind == KIND_TEXT:
        return Decision(profile, ACTION_OCR_ONLY, "so o texto mudou")
    # localizada, não parece texto
    if profile == PROFILE_GAME:
        return Decision(profile, ACTION_OCR_ONLY, "jogo: mudanca localizada (dialogo/HUD) atualiza so o texto")
    return Decision(profile, ACTION_VLM, "mudanca localizada em interface")


# ───────────────────────────── 5. estado da cena ─────────────────────────────

SOURCE_VISUAL = "visual"        # visto na imagem
SOURCE_OCR = "ocr"              # lido como texto
SOURCE_INFERRED = "inferred"    # deduzido

ENTITY_TTL_S = 120.0


@dataclass
class Entity:
    name: str
    kind: str = "unknown"             # personagem, objeto, interface, jogo...
    confidence: float = 0.5           # 0..1
    evidence: list[str] = field(default_factory=list)
    source: str = SOURCE_VISUAL
    seen_at: float = 0.0
    uncertain: bool = False

    def as_dict(self, now: float) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "confidence": round(self.confidence, 2),
                "evidence": list(self.evidence)[:4], "source": self.source, "uncertain": self.uncertain,
                "age_s": round(max(0.0, now - self.seen_at), 1)}


@dataclass
class SceneState:
    version: int = 0
    summary: str = ""
    topic: str = ""
    entities: dict[str, Entity] = field(default_factory=dict)
    ocr_lines: list[OcrLine] = field(default_factory=list)
    ocr_status: str = "none"          # none | ok | empty | error
    ocr_error: str = ""
    ocr_ms: int | None = None
    last_frame_id: int | None = None
    analyzed_at: float = 0.0
    profile: str = ""
    reason: str = ""
    backend: str = ""
    source: str = "screen"
    last_change: dict[str, Any] | None = None
    needs_reevaluation: list[str] = field(default_factory=list)


class SceneMemory:
    """Estado ATUAL + histórico curto de mudanças. Nunca guarda imagem; descrições e textos têm teto."""

    def __init__(self, history_max: int | None = None, ocr_lines_max: int | None = None) -> None:
        self.state = SceneState()
        self.history: deque[dict[str, Any]] = deque(maxlen=history_max or _i("CELIA_VISION_HISTORY", 12))
        self.ocr_lines_max = ocr_lines_max or _i("CELIA_VISION_OCR_LINES_MAX", 40)

    # -- OCR (texto separado da interpretação) --
    def update_ocr(self, lines: list[OcrLine], *, status: str, now: float, error: str = "", ms: int | None = None,
                   frame_id: int | None = None) -> OcrDiff:
        diff = diff_ocr(self.state.ocr_lines, lines[: self.ocr_lines_max]) if status == "ok" else OcrDiff((), (), (), 0)
        if status == "ok":
            self.state.ocr_lines = lines[: self.ocr_lines_max]
        elif status == "empty":
            diff = OcrDiff((), (), tuple(l.text for l in self.state.ocr_lines), 0)
            self.state.ocr_lines = []
        self.state.ocr_status, self.state.ocr_error, self.state.ocr_ms = status, error[:160], ms
        if not diff.is_empty:
            self._log("ocr", now, {"ocr": diff.as_dict()}, frame_id)
        return diff

    # -- interpretação do modelo --
    def apply_analysis(self, analysis: dict[str, Any], *, now: float, frame_id: int | None, profile: str, reason: str,
                       backend: str, source: str, change: FrameChange | None, state_version: int) -> bool:
        """Aplica um resultado SE ele não é mais velho que o estado atual.

        A ordem é a do QUADRO analisado (`frame_id`), não a de chegada: uma análise lenta de um quadro antigo não pode sobrescrever
        a cena que um quadro mais novo já estabeleceu; uma análise válida cujo quadro ainda é o mais novo é aplicada mesmo que
        outra tenha sido agendada depois (ela será sobrescrita quando terminar). Devolve se foi aplicada.
        """
        st = self.state
        if frame_id is not None and st.last_frame_id is not None and frame_id < st.last_frame_id:
            self._log("stale_result", now, {"result_frame": frame_id, "current_frame": st.last_frame_id,
                                            "state_version": state_version}, frame_id)
            return False
        st.version += 1
        st.summary = str(analysis.get("summary") or "")[:320]
        st.topic = str(analysis.get("topic") or "")[:80]
        st.profile, st.reason, st.backend, st.source = profile, reason, backend, source
        st.last_frame_id, st.analyzed_at = frame_id, now
        st.last_change = change.as_dict() if change else None
        for raw in analysis.get("entities") or []:
            name = str(raw.get("name") or "").strip()[:60]
            if not name:
                continue
            key = _norm_text(name)
            conf = raw.get("confidence", 0.5)
            try:
                conf = max(0.0, min(1.0, float(conf)))
            except (TypeError, ValueError):
                conf = 0.5
            ev = [str(e)[:80] for e in (raw.get("evidence") or []) if not _is_placeholder_evidence(str(e))][:4]
            uncertain = bool(raw.get("uncertain", conf < 0.6))
            ocr_words = {w for l in st.ocr_lines for w in _norm_text(l.text).split()}
            name_words = set(key.split())
            if name_words and name_words <= ocr_words:
                # O nome ESTÁ ESCRITO na tela (OCR). Isso é pista de contexto, não prova de que o personagem desenhado seja esse:
                # medido 2026-10-18, o VLM de 1.6B respondeu "a personagem Mario" só porque a caixa dizia "Pesquisa: Mario".
                conf = min(conf, 0.4)
                uncertain = True
                ev = ["nome aparece escrito na tela (OCR)"] + ev
            if not ev:
                conf, uncertain = min(conf, 0.5), True   # sem pista visual declarada não passa de palpite
            st.entities[key] = Entity(name, str(raw.get("kind") or "unknown")[:24], conf, ev,
                                      str(raw.get("source") or SOURCE_VISUAL), now, uncertain=uncertain)
        st.needs_reevaluation = []
        self._log("analysis", now, {"summary": st.summary[:80], "profile": profile, "backend": backend}, frame_id)
        return True

    def invalidate(self, reason: str, now: float, *, hard: bool) -> None:
        """Mudança importante: `hard` esquece entidades (cena nova); senão marca para reavaliar e envelhece."""
        if hard:
            gone = [e.name for e in self.state.entities.values()]
            self.state.entities.clear()
            self.state.summary = ""
            self.state.needs_reevaluation = ["scene"]
            self._log("scene_reset", now, {"reason": reason, "dropped_entities": gone[:8]}, None)
        else:
            self.state.needs_reevaluation = ["entities"] if self.state.entities else []

    def expire(self, now: float) -> None:
        for key in [k for k, e in self.state.entities.items() if now - e.seen_at > ENTITY_TTL_S]:
            del self.state.entities[key]

    def _log(self, kind: str, now: float, data: dict[str, Any], frame_id: int | None) -> None:
        self.history.append({"kind": kind, "at": round(now, 2), "frame_id": frame_id, **data})

    def snapshot(self, now: float) -> dict[str, Any]:
        st = self.state
        return {
            "version": st.version, "summary": st.summary, "topic": st.topic,
            "entities": [e.as_dict(now) for e in st.entities.values()],
            "ocr": {"status": st.ocr_status, "lines": len(st.ocr_lines), "ms": st.ocr_ms, "error": st.ocr_error},
            "last_frame_id": st.last_frame_id,
            "age_s": round(now - st.analyzed_at, 1) if st.analyzed_at else None,
            "profile": st.profile, "reason": st.reason, "backend": st.backend, "source": st.source,
            "last_change": st.last_change, "needs_reevaluation": list(st.needs_reevaluation),
            "history_len": len(self.history),
        }

    def reset(self) -> None:
        self.state = SceneState()
        self.history.clear()


# ───────────────────────────── 6. presença do avatar da Célia ─────────────────────────────

AVATAR_VISIBLE = "avatar_visible"          # o modelo apontou a Célia NA imagem, com pista visual
AVATAR_MENTION = "name_in_text"            # o nome aparece escrito (OCR/pagina), sem avatar confirmado
AVATAR_EXTERNAL = "external_reference"     # imagem/video/arte de fora que a representa
AVATAR_UNCONFIRMED = "unconfirmed"         # nao da para confirmar


def celia_presence(scene: SceneState) -> dict[str, Any]:
    """Veredito CAUTELOSO. Nome escrito não é avatar; referência externa não é o avatar; sem evidência visual => `unconfirmed`."""
    celia_keys = [k for k, e in scene.entities.items() if k in {"celia", "celia avatar", "avatar da celia"} or "celia" in k.split()]
    best = max((scene.entities[k] for k in celia_keys), key=lambda e: e.confidence, default=None)
    text_mention = any("celia" in _norm_text(l.text).split() for l in scene.ocr_lines)
    if best is not None and best.source == SOURCE_VISUAL:
        ext_words = ("pagina", "page", "site", "video", "foto", "imagem de referencia", "referencia", "pesquisa", "arte", "wallpaper")
        evidence_text = _norm_text(" ".join(best.evidence + [best.kind]))
        if any(w in evidence_text for w in ext_words):
            return {"verdict": AVATAR_EXTERNAL, "confidence": round(best.confidence, 2), "evidence": best.evidence[:3]}
        if best.kind == "verified_avatar" and best.confidence >= 0.85 and not best.uncertain and best.evidence:
            return {"verdict": AVATAR_VISIBLE, "confidence": round(best.confidence, 2), "evidence": best.evidence[:3]}
        return {"verdict": AVATAR_UNCONFIRMED, "confidence": round(best.confidence, 2), "evidence": best.evidence[:3],
                "note": "pista visual fraca ou sem evidencia"}
    if text_mention or (best is not None):
        return {"verdict": AVATAR_MENTION, "confidence": 0.0, "evidence": [],
                "note": "o nome aparece, mas nao ha avatar confirmado na imagem"}
    return {"verdict": AVATAR_UNCONFIRMED, "confidence": 0.0, "evidence": []}


# ───────────────────────────── 7. prompts por perfil ─────────────────────────────

_JSON_SHAPE = (
    '{"summary":"...","topic":"...","salience":0.0,"should_comment":false,'
    '"entities":[{"name":"...","kind":"personagem","confidence":0.0,'
    '"evidence":["<o que você VÊ que sustenta, ex.: cor do cabelo>"],"uncertain":false}]}'
)

_GROUND = (
    "Regras: descreva só o que está VISÍVEL na imagem; o texto do OCR abaixo é uma pista que pode estar errada — confira na "
    "imagem e, se contradisser, diga. Um nome ESCRITO na tela não prova que o personagem desenhado é aquele: separe "
    "'o que está escrito' de 'o que está desenhado'. Se não tiver certeza de quem é um personagem, diga 'provável' e liste as pistas. "
    "Reconstrua a cena ATUAL a partir da imagem; a cena anterior serve apenas para comparar mudanças. "
    "Para avatares de Célia/Criador, descreva aparência e acessórios; a comparação com referências decide identidade e parentesco. "
    "Texto NVIDIA pode ser documentação ou log exibido: um erro real de serviço exige telemetria, não OCR. "
    "Um screenshot mostra pixels de janelas e avatares sobrepostos; não invente mesa, corpo sentado ou ambiente fora da captura."
)


def build_prompt(profile: str, *, ocr_text: str = "", previous_summary: str = "", change: FrameChange | None = None,
                 frame_count: int = 1, timeline: list[dict[str, Any]] | None = None, ocr_note: str = "") -> str:
    if ocr_text.strip():
        ocr_block = f"\nTEXTO LIDO POR OCR (pista, pode conter erros):\n{ocr_text[:900]}\n"
    elif ocr_note:
        ocr_block = f"\nOCR: {ocr_note}. Confira o texto diretamente na imagem.\n"
    else:
        ocr_block = "\nOCR: sem texto lido.\n"
    prev_block = f"\nCENA ANTERIOR (memória, não evidência atual): {previous_summary[:240]}\n" if previous_summary else ""
    change_block = ""
    if change is not None and change.kind in (KIND_LOCAL, KIND_TEXT) and change.region:
        # só para mudança pequena e localizada; para cena nova/rajada o bloco fazia o VLM de 1.6B ecoar o prompt (medido: o resumo
        # virou "mudanças detectadas na região (0.0, 0.0, 1.0, 1.0)...")
        x0, y0, x1, y1 = change.region
        change_block = f"\nA parte da imagem que mudou fica entre {x0:.0%}-{x1:.0%} da largura e {y0:.0%}-{y1:.0%} da altura.\n"
    if profile == PROFILE_GAME:
        task = ("Perfil JOGO/DIÁLOGO: identifique o jogo, os personagens (nome provável + pistas: cor, roupa, traços) e o que está "
                "acontecendo. Não repita a descrição completa a cada fala.")
    elif profile == PROFILE_MOTION:
        task = (f"Perfil MOVIMENTO: são {frame_count} capturas em ORDEM CRONOLÓGICA (1 = mais antiga). Descreva QUEM/O QUE aparece (personagens, "
                "cenário). Não afirme direção nem ordem de movimento: isso é medido por outro componente e anexado depois.")
    elif profile == PROFILE_DETAIL:
        task = ("Perfil DETALHE: análise completa e cuidadosa. Liste personagens/objetos com confiança e pistas, o texto que precisa ser "
                "interpretado visualmente e qualquer dúvida que o OCR sozinho não resolve.")
    else:
        task = ("Perfil INTERFACE: diga qual app/página/documento é e o estado dele. Leia visualmente só o texto que o OCR não cobriu "
                "ou que parece errado.")
    return f"Você é o sensor visual da Célia. Responda em português. {task}\n{_GROUND}{prev_block}{change_block}{ocr_block}\nResponda SOMENTE JSON compacto: {_JSON_SHAPE}"


# ───────────────────────────── 8. conferência do avatar da Célia ─────────────────────────────

AVATAR_CHECK_PROMPT = (
    "Imagem 1 é o retrato OFICIAL da personagem Célia (cabelo azul escuro, orelhas de gato, casaco azul, presilha amarela). "
    "Imagem 2 é uma captura de tela. Diga se a personagem da Imagem 1 aparece DESENHADA/RENDERIZADA na Imagem 2 como avatar na tela. "
    "Se a Imagem 2 só tem texto com o nome 'Célia', uma foto/vídeo/arte de referência dentro de uma página, ou nenhuma personagem parecida, NÃO é o avatar. "
    "Se não der para ter certeza, responda unsure. Responda SOMENTE JSON: "
    '{"present":"yes|no|unsure","external_reference":false,"confidence":0.0,"evidence":["pista visual"]}'
)


def parse_avatar_check(raw: str) -> dict[str, Any] | None:
    """Interpreta a resposta da conferência. Só `yes` com pista e confiança vira entidade visual; o resto nunca confirma."""
    import json

    start, end = (raw or "").find("{"), (raw or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except Exception:
        return None
    present = str(obj.get("present") or "").strip().lower()
    if present not in {"yes", "no", "unsure"}:
        return None
    try:
        conf = max(0.0, min(1.0, float(obj.get("confidence", 0.0))))
    except (TypeError, ValueError):
        conf = 0.0
    evidence = [str(e)[:80] for e in (obj.get("evidence") or []) if str(e).strip()][:4]
    return {"present": present, "external_reference": bool(obj.get("external_reference")), "confidence": conf, "evidence": evidence}


def apply_avatar_check(scene: "SceneState", check: dict[str, Any], now: float) -> dict[str, Any]:
    """Grava a conferência no estado e devolve o veredito cauteloso. `yes` sem evidência ou com confiança baixa NÃO confirma."""
    key = "celia"
    if check["present"] == "yes" and check["evidence"] and check["confidence"] >= 0.6:
        kind = "referencia externa" if check["external_reference"] else "verified_avatar"
        scene.entities[key] = Entity("Célia", kind, check["confidence"], check["evidence"], SOURCE_VISUAL, now,
                                     uncertain=False)
    elif check["present"] == "no":
        scene.entities.pop(key, None)
    else:
        scene.entities[key] = Entity("Célia", "indeterminado", check["confidence"], check["evidence"], SOURCE_VISUAL, now,
                                     uncertain=True)
    return celia_presence(scene)


SCENE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "maxLength": 180},
        "topic": {"type": "string", "maxLength": 40},
        "salience": {"type": "number"},
        "should_comment": {"type": "boolean"},
        "entities": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
            "name": {"type": "string", "maxLength": 40}, "kind": {"type": "string", "enum": ["personagem", "objeto", "interface", "jogo", "texto", "outro"]},
            "confidence": {"type": "number"}, "evidence": {"type": "array", "maxItems": 1, "items": {"type": "string", "maxLength": 48}},
            "uncertain": {"type": "boolean"}}, "required": ["name", "kind", "confidence", "evidence", "uncertain"]}},
    },
    "required": ["summary", "topic", "salience", "should_comment", "entities"],
}

AVATAR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"present": {"type": "string", "enum": ["yes", "no", "unsure"]}, "external_reference": {"type": "boolean"},
                   "confidence": {"type": "number"}, "evidence": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 60}}},
    "required": ["present", "external_reference", "confidence", "evidence"],
}


def gate_avatar_check(real: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    """Aplica o controle negativo: se o impostor também recebe `yes`, a resposta do modelo não discrimina e vira `unsure`."""
    if real["present"] == "yes" and control["present"] == "yes":
        return {"present": "unsure", "external_reference": False, "confidence": 0.0,
                "evidence": ["o modelo disse sim ate para o retrato de outra pessoa: nao discrimina"]}
    return real


# ───────────────────────────── 9. qualidade da imagem (nitidez) e guardas do modelo pequeno ─────────────────────────────

@dataclass(frozen=True)
class ImageQuality:
    sharpness: float        # variância do Laplaciano (640 px de largura)
    edge_density: float     # fração de pixels de borda
    blurry: bool

    def as_dict(self) -> dict[str, Any]:
        return {"sharpness": round(self.sharpness, 1), "edge_density": round(self.edge_density, 4), "blurry": self.blurry}


def image_quality(image: str | bytes) -> ImageQuality | None:
    """Nitidez barata (~3 ms). Medido 2026-10-18: borrão = lapvar 1,2 / bordas 0,0002; todas as demais telas de teste ≥ 89 / ≥ 0,007.

    Existe porque o VLM de 1.6B descreve "uma figura de chapéu e casaco" num borrão com confiança 0,9: a auto-confiança dele
    não é calibrada, então a nitidez medida limita o que ele pode afirmar.
    """
    raw = decode_data_url(image) if isinstance(image, str) else image
    if not raw:
        return None
    try:
        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("L")
            im = im.resize((640, max(1, round(im.height * 640 / im.width))))
            a = np.asarray(im, dtype=np.float32)
    except Exception:
        return None
    lap = (-4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:])
    sharp = float(lap.var())
    gx, gy = np.abs(np.diff(a, axis=1))[:-1, :], np.abs(np.diff(a, axis=0))[:, :-1]
    edges = float(((gx + gy) > 60).mean())
    return ImageQuality(sharp, edges, sharp < _f("CELIA_VISION_BLUR_LAPVAR", 8.0) and edges < _f("CELIA_VISION_BLUR_EDGES", 0.002))


def cap_for_blur(analysis: dict[str, Any]) -> dict[str, Any]:
    """Imagem borrada: nenhuma entidade passa de 0,4 e todas viram incertas; o resumo diz que a imagem é pouco nítida."""
    out = dict(analysis)
    out["summary"] = ("[imagem pouco nítida: detalhes não confiáveis] " + str(out.get("summary") or ""))[:320]
    ents = []
    for e in out.get("entities") or []:
        e = dict(e)
        try:
            e["confidence"] = min(0.4, float(e.get("confidence", 0.4)))
        except (TypeError, ValueError):
            e["confidence"] = 0.4
        e["uncertain"] = True
        ents.append(e)
    out["entities"] = ents
    return out


def flagged_by_ocr(analysis: dict[str, Any], ocr_lines: list["OcrLine"]) -> list[str]:
    """Nomes de entidades que também aparecem ESCRITOS no OCR (candidatas a confusão texto × imagem)."""
    words = {w for l in ocr_lines for w in _norm_text(l.text).split()}
    out = []
    for e in analysis.get("entities") or []:
        nm = set(_norm_text(str(e.get("name") or "")).split())
        if nm and nm <= words:
            out.append(str(e.get("name")))
    return out


VISUAL_ONLY_PROMPT = (
    "Você é o sensor visual da Célia. Responda em português. TODO O TEXTO desta imagem foi apagado de propósito. "
    "Descreva SOMENTE o que está desenhado/fotografado: personagens (pela aparência: cabelo, roupa, cores, objetos que carregam), "
    "objetos, cenário e cores. Só dê um nome se der para deduzir pela aparência e liste as pistas visuais; senão diga 'personagem não identificado'. "
    "Se não houver nenhuma figura, diga que a imagem não mostra personagem. Responda SOMENTE JSON compacto: " + _JSON_SHAPE
)

_PROMPT_ECHO = ("mudancas detectadas", "mudanca detectada", "texto lido por ocr", "cena anterior", "responda somente json", "sensor visual da celia")


def echoes_prompt(summary: str) -> bool:
    """O modelo pequeno às vezes devolve pedaços do próprio prompt como 'descrição'. Isso não é observação."""
    n = _norm_text(summary)
    return any(marker in n for marker in _PROMPT_ECHO)


def blank_content(image: str | bytes) -> bool:
    """Imagem praticamente sem conteúdo (ex.: página branca depois de apagar o texto). VLMs inventam figuras em imagens vazias."""
    raw = decode_data_url(image) if isinstance(image, str) else image
    if not raw:
        return False
    try:
        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            a = np.asarray(im.convert("L").resize((320, 180)), dtype=np.float32)
    except Exception:
        return False
    return float(a.std()) < _f("CELIA_VISION_BLANK_STD", 6.0)
