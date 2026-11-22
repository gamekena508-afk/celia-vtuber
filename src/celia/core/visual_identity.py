"""Reference-based visual identity, independent of persona and reply generation."""
from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import io
from PIL import Image
import time
import sys
import uuid
import unicodedata
from datetime import datetime, timezone

from .raizes import RAIZ
from .local_vision import local_vision_client
from .gpu_scheduler import gpu_scheduler
from . import visual_scene as scene

RELATIONS = {"celia": "self_avatar", "criador_slime": "creator_avatar", "criador_human": "creator_human"}


def identity_candidates(analysis: dict) -> list[str]:
    """Semantic hints select comparisons; OCR and aliases never confirm identity."""
    def fold(value):
        return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    visual = " ".join([str(analysis.get("summary") or "")] + [
        " ".join([str(entity.get("name") or "")] + [str(e) for e in entity.get("evidence", [])])
        for entity in analysis.get("entities", [])
        if entity.get("source", "visual") == "visual" and not entity.get("uncertain")])
    registry = json.loads((RAIZ / "config/image-identities.json").read_text())
    return [key for key, item in registry["identities"].items()
            if any(fold(hint) in fold(visual) for hint in item.get("visual_candidates", []))][:2]


def reference(identity: str) -> str:
    registry = json.loads((RAIZ / "config/image-identities.json").read_text())
    path = RAIZ / registry["identities"][identity]["main"]
    return bounded_image("data:image/" + ("jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "png")
                         + ";base64," + base64.b64encode(path.read_bytes()).decode(), 512)


def bounded_image(image: str, max_edge: int = 1024) -> str:
    raw = scene.decode_data_url(image)
    with Image.open(io.BytesIO(raw)) as original:
        prepared = original.convert("RGB")
        prepared.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        prepared.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()



def verdict(identity: str, real: dict, control: dict, shape: dict | None = None) -> dict:
    # A control must explicitly reject the other identity, not merely be unsure.
    confirmed = (real.get("present") == "yes" and control.get("present") == "no"
                 and real.get("confidence", 0) >= .85 and bool(real.get("evidence")))
    # A humanoid blue catgirl previously passed the slime comparison.
    if identity == "criador_slime":
        confirmed = confirmed and bool(shape and shape.get("blob_present") is True
                                       and shape.get("confidence", 0) >= .85)
    return {"identity": identity, "relation": RELATIONS[identity], "confirmed": confirmed,
            "confidence": "high" if confirmed else "unconfirmed", "evidence": real.get("evidence", [])[:3],
            "control_rejected": control.get("present") == "no",
            "shape_check": shape}


class VisualIdentity:
    def __init__(self):
        self.records: dict[tuple[str, str], tuple[float, dict]] = {}

    def clear(self):
        self.records.clear()

    async def verify(self, image: str, identity: str) -> dict:
        if identity not in RELATIONS:
            raise ValueError("unknown visual identity")
        digest = hashlib.sha256(image.encode()).hexdigest()
        key = (identity, digest)
        cached = self.records.get(key)
        if cached and time.monotonic() - cached[0] < 15:
            return dict(cached[1])
        quality = scene.image_quality(image)
        if quality is None or quality.blurry:
            return {"identity": identity, "relation": RELATIONS[identity], "confirmed": False,
                    "confidence": "unconfirmed", "reason": "insufficient_image_quality"}
        # The creator's human reference is distinct from the Celia+Slime pair.
        # If a control is also truly present, stay unconfirmed rather than guess.
        decoy = "celia" if identity == "criador_human" else "criador_human"
        prepared_image = bounded_image(image)
        prompt = (
            "Compare image 1 (reference) with image 2 (scene). Is the SAME specific character/person visibly depicted "
            "in the scene? A portrait, drawing or 3D rendering counts. Text or a name alone does not. "
            "Require matching distinctive face, accessories and design; generic color or species alone is insufficient. "
            "Reject different characters of the same color/species. If details are too small or ambiguous say unsure. "
            "Return JSON only: present=yes|no|unsure, external_reference=false, confidence=0..1, evidence=[visible distinctive matches]."
        )

        async def compare():
            checks = []
            for ref in (identity, decoy):
                registry = json.loads((RAIZ / "config/image-identities.json").read_text())
                features = registry["identities"][ref].get("distinctive_features", [])
                response = await local_vision_client.request(prompt=prompt + "\nReference design: " + "; ".join(features), image=[reference(ref), prepared_image],
                                                             max_tokens=180, json_schema=scene.AVATAR_SCHEMA)
                check = scene.parse_avatar_check(response.raw)
                if check is None:
                    raise ValueError("invalid identity response")
                checks.append(check)
            shape = None
            if identity == "criador_slime" and checks[0].get("present") == "yes":
                response = await local_vision_client.request(
                    prompt=("Inspect this scene. Is there a standalone rounded cat-shaped slime/blob, "
                            "with its face on the blob itself and NO humanoid torso, arms or legs? "
                            "A human-shaped catgirl with blue hair and cat ears does not qualify. "
                            "If both figures are present, inspect the separate blob. "
                            "Return blob_present and confidence based only on visible body shape."),
                    image=prepared_image, max_tokens=64,
                    json_schema={"type": "object", "properties": {
                        "blob_present": {"type": "boolean"},
                        "confidence": {"type": "number"}},
                        "required": ["blob_present", "confidence"], "additionalProperties": False})
                shape = json.loads(response.raw)
            return verdict(identity, *checks, shape=shape)

        started = time.monotonic()
        record = {"run_id": str(uuid.uuid4()), "schema_version": 1, "operation": "visual_identity",
                  "started_at": datetime.now(timezone.utc).isoformat(), "identity": identity,
                  "input_sha256": digest, "model": local_vision_client.model, "runtime": sys.executable,
                  "device": "local_llama_server", "token_usage": "unavailable: aggregate not exposed by local client"}
        try:
            result = await gpu_scheduler.run_job("vision_request", compare)
            record.update(status="ok", confirmed=result["confirmed"],
                          output_sha256=hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest())
        except asyncio.CancelledError:
            record.update(status="cancelled", error_class="CancelledError")
            raise
        except Exception as exc:
            record.update(status="error", error_class=type(exc).__name__, error_code=getattr(exc, "code", "identity_error"))
            raise
        finally:
            record.update(ended_at=datetime.now(timezone.utc).isoformat(), elapsed_ms=int((time.monotonic()-started)*1000))
            directory = RAIZ / "var/debug/visual-identity"
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / "runs.jsonl").open("a") as log:
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
        result.update(frame_sha256=digest, analyzed_at=time.time(), model=local_vision_client.model)
        self.records[key] = (time.monotonic(), result)
        return result


visual_identity = VisualIdentity()
