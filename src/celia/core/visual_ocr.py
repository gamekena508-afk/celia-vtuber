"""OCR da tela para o contexto visual: tesseract (já instalado) via pytesseract, com linhas, posição e confiança.

O MCP `vision_extract_text` continua existindo e usa o mesmo motor. Aqui o resultado é ESTRUTURADO (linha + caixa
normalizada + confiança) para o OCR incremental; o texto nunca é misturado com a interpretação do modelo visual.
Rodar em thread: tesseract é CPU e bloquearia o laço de eventos.
"""
from __future__ import annotations

import asyncio
import io
import os
import time
from dataclasses import dataclass

from .visual_scene import OcrLine, decode_data_url


@dataclass(frozen=True)
class OcrResult:
    lines: list[OcrLine]
    status: str            # ok | empty | error | unavailable
    ms: int
    error: str = ""
    region: tuple[float, float, float, float] | None = None
    mean_conf: float | None = None


def _available() -> bool:
    try:
        import pytesseract  # noqa: F401

        return True
    except ImportError:
        return False


def _lang() -> str:
    return os.getenv("CELIA_VISION_OCR_LANG", "por+eng").strip() or "eng"


def _plausible(text: str, conf: float) -> bool:
    """Descarta lixo típico de OCR sobre arte (símbolos soltos): precisa de letras/dígitos suficientes e confiança mínima."""
    alnum = sum(ch.isalnum() for ch in text)
    letters = sum(ch.isalpha() for ch in text)
    if alnum < 3 or conf < float(os.getenv("CELIA_VISION_OCR_LINE_MIN_CONF", "45")):
        return False
    return letters >= 2 or alnum >= 4


def mask_text(image: str | bytes, lines: list[OcrLine], pad: float = 0.012) -> str | None:
    """Apaga as caixas do OCR (cor de fundo local) e devolve data-URL JPEG: serve ao passe 'só o que está desenhado'."""
    raw = decode_data_url(image) if isinstance(image, str) else image
    if not raw:
        return None
    try:
        import base64

        from PIL import Image, ImageDraw

        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            w, h = im.size
            d = ImageDraw.Draw(im)
            for line in lines:
                x0, y0, x1, y1 = line.box
                box = (max(0, int((x0 - pad) * w)), max(0, int((y0 - pad) * h)), min(w, int((x1 + pad) * w)), min(h, int((y1 + pad) * h)))
                edge = im.crop((max(0, box[0] - 3), box[1], max(1, box[0]), box[3])).resize((1, 1)) if box[0] > 3 else im.crop((box[0], box[1], box[0] + 1, box[1] + 1))
                d.rectangle(box, fill=edge.getpixel((0, 0)))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def run_ocr_sync(image: str | bytes, region: tuple[float, float, float, float] | None = None,
                 min_conf: float | None = None) -> OcrResult:
    """OCR de um data-URL/bytes. `region` (x0,y0,x1,y1 normalizados) recorta antes; as caixas voltam em coordenadas da tela inteira."""
    started = time.monotonic()
    if not _available():
        return OcrResult([], "unavailable", 0, "pytesseract nao instalado")
    raw = decode_data_url(image) if isinstance(image, str) else image
    if not raw:
        return OcrResult([], "error", 0, "imagem nao decodificavel")
    try:
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            full_w, full_h = im.size
            ox = oy = 0
            if region:
                pad = 0.02
                x0, y0 = max(0.0, region[0] - pad), max(0.0, region[1] - pad)
                x1, y1 = min(1.0, region[2] + pad), min(1.0, region[3] + pad)
                ox, oy = int(x0 * full_w), int(y0 * full_h)
                im = im.crop((ox, oy, max(ox + 8, int(x1 * full_w)), max(oy + 8, int(y1 * full_h))))
            data = pytesseract.image_to_data(im, lang=_lang(), output_type=pytesseract.Output.DICT,
                                             config="--psm 6" if region else "--psm 3")
    except Exception as exc:  # tesseract ausente do PATH, idioma faltando, imagem corrompida...
        return OcrResult([], "error", int((time.monotonic() - started) * 1000), f"{type(exc).__name__}: {exc}"[:160], region)
    threshold = min_conf if min_conf is not None else float(os.getenv("CELIA_VISION_OCR_MIN_CONF", "30"))
    groups: dict[tuple[int, int, int], list[int]] = {}
    for i, word in enumerate(data.get("text", [])):
        word = (word or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if not word or conf < threshold:
            continue
        groups.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(i)
    lines: list[OcrLine] = []
    confs: list[float] = []
    for key in sorted(groups, key=lambda k: (min(data["top"][i] for i in groups[k]), min(data["left"][i] for i in groups[k]))):
        idx = groups[key]
        text = " ".join(data["text"][i].strip() for i in idx)
        if not _plausible(text, sum(float(data["conf"][i]) for i in idx) / len(idx)):
            continue
        left = min(data["left"][i] for i in idx) + ox
        top = min(data["top"][i] for i in idx) + oy
        right = max(data["left"][i] + data["width"][i] for i in idx) + ox
        bottom = max(data["top"][i] + data["height"][i] for i in idx) + oy
        conf = sum(float(data["conf"][i]) for i in idx) / len(idx)
        confs.append(conf)
        lines.append(OcrLine(text, (left / full_w, top / full_h, right / full_w, bottom / full_h), round(conf, 1)))
    ms = int((time.monotonic() - started) * 1000)
    if not lines:
        return OcrResult([], "empty", ms, region=region)
    return OcrResult(lines, "ok", ms, region=region, mean_conf=round(sum(confs) / len(confs), 1))


async def run_ocr(image: str | bytes, region: tuple[float, float, float, float] | None = None,
                  timeout: float | None = None) -> OcrResult:
    limit = timeout if timeout is not None else float(os.getenv("CELIA_VISION_OCR_TIMEOUT", "6.0"))
    started = time.monotonic()
    try:
        return await asyncio.wait_for(asyncio.to_thread(run_ocr_sync, image, region), timeout=limit)
    except asyncio.TimeoutError:
        return OcrResult([], "error", int((time.monotonic() - started) * 1000), f"timeout {limit}s", region)


def ocr_text(lines: list[OcrLine], limit: int = 900) -> str:
    return "\n".join(line.text for line in lines)[:limit]
